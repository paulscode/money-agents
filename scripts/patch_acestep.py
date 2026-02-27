#!/usr/bin/env python3
"""
Post-install patch for ACE-Step: adds cooperative VRAM unload/reload support.

ACE-Step is cloned from upstream (https://github.com/ace-step/ACE-Step-1.5)
and gitignored.  This script is run automatically by start.py after cloning
to add three capabilities the upstream code lacks:

  1. handler.py  – unload_models() method + is_initialized property
  2. api_server.py – POST /unload, POST /reload endpoints
  3. api_server.py – GET /health now reports model_loaded status
  4. api_server.py – Startup saves init params for /reload

Without these, the only way to free AceStep's ~19 GB VRAM is to kill the
process (and the backend cannot restart it).  With them, the GPU lifecycle
service can cooperatively evict and later reload AceStep models, matching
the pattern used by every other GPU tool.

Usage:
    python scripts/patch_acestep.py [--acestep-dir PATH] [--force]

The script is idempotent — safe to run multiple times.
"""

import argparse
import os
import re
import sys
from pathlib import Path

# Marker comment inserted into patched files so we can detect prior application
PATCH_MARKER = "# MONEY-AGENTS-PATCH: cooperative-vram-unload"
PATCH_VERSION = "1"  # Bump when patch content changes


# ─────────────────────────────────────────────────────────────────────────────
# Handler patches
# ─────────────────────────────────────────────────────────────────────────────

HANDLER_IMPORT_PATCH = "import gc  " + PATCH_MARKER

HANDLER_METHODS_PATCH = f'''
    {PATCH_MARKER} v{PATCH_VERSION}
    def unload_models(self) -> str:
        """Unload all models from GPU/memory to free VRAM.

        This is the cooperative VRAM evacuation method. It deletes
        all model tensors, clears CUDA caches, and allows the handler
        to be re-initialized later via initialize_service().

        Returns:
            Status message
        """
        unloaded = []
        try:
            # Unload LoRA first if loaded
            if self.lora_loaded:
                self.unload_lora()

            # Delete all model components
            for attr_name in ("model", "vae", "text_encoder", "text_tokenizer",
                              "silence_latent", "reward_model", "_base_decoder"):
                obj = getattr(self, attr_name, None)
                if obj is not None:
                    # Move to CPU first to free GPU memory immediately
                    if hasattr(obj, "to"):
                        try:
                            obj.to("cpu")
                        except Exception:
                            pass
                    setattr(self, attr_name, None)
                    unloaded.append(attr_name)

            # Clear config so is_initialized reports False
            self.config = None

            # Force garbage collection
            gc.collect()

            # Clear CUDA cache
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()

            logger.info(f"[unload_models] Unloaded: {{unloaded}}")
            return f"Models unloaded: {{', '.join(unloaded)}}"

        except Exception as e:
            logger.exception("[unload_models] Error during unload")
            return f"Error unloading models: {{str(e)}}"

    @property
    def is_initialized(self) -> bool:
        """Check if models are loaded and ready for inference."""
        return self.model is not None and self.vae is not None and self.text_encoder is not None
'''


# ─────────────────────────────────────────────────────────────────────────────
# API server patches
# ─────────────────────────────────────────────────────────────────────────────

API_INIT_PARAMS_PATCH = f'''
        {PATCH_MARKER} v{PATCH_VERSION}
        # Store init params for /reload support
        app.state._init_params = {{
            "project_root": project_root,
            "config_path": config_path,
            "device": device,
            "use_flash_attention": use_flash_attention,
            "compile_model": False,
            "offload_to_cpu": offload_to_cpu,
            "offload_dit_to_cpu": offload_dit_to_cpu,
        }}
'''

API_HEALTH_REPLACEMENT = f'''    {PATCH_MARKER} v{PATCH_VERSION}
    @app.get("/health")
    async def health_check():
        """Health check endpoint for service status."""
        model_loaded = getattr(app.state, "_initialized", False)
        handler = getattr(app.state, "handler", None)
        handler_ready = handler.is_initialized if handler else False
        return _wrap_response({{
            "status": "ok",
            "service": "ACE-Step API",
            "version": "1.0",
            "model_loaded": model_loaded and handler_ready,
        }})

    @app.post("/unload")
    async def unload_models():
        """Unload all models from GPU to free VRAM.

        This cooperatively frees VRAM so other tools can use the GPU.
        The models can be reloaded via POST /reload.
        """
        handler = getattr(app.state, "handler", None)
        results = []

        if handler:
            msg = handler.unload_models()
            results.append(f"handler1: {{msg}}")

        handler2 = getattr(app.state, "handler2", None)
        if handler2 and getattr(app.state, "_initialized2", False):
            msg = handler2.unload_models()
            results.append(f"handler2: {{msg}}")

        handler3 = getattr(app.state, "handler3", None)
        if handler3 and getattr(app.state, "_initialized3", False):
            msg = handler3.unload_models()
            results.append(f"handler3: {{msg}}")

        # Mark all as uninitialized
        app.state._initialized = False
        app.state._initialized2 = False
        app.state._initialized3 = False
        app.state._init_error = None

        print(f"[API Server] Models unloaded via /unload endpoint")
        return _wrap_response({{
            "status": "unloaded",
            "results": results,
        }})

    @app.post("/reload")
    async def reload_models():
        """Reload models after they were unloaded via /unload.

        Re-initializes all handlers using the same parameters from startup.
        """
        init_params = getattr(app.state, "_init_params", None)
        if not init_params:
            return _wrap_response(None, code=500, error="No init params stored - cannot reload")

        handler = getattr(app.state, "handler", None)
        if not handler:
            return _wrap_response(None, code=500, error="No handler available")

        if getattr(app.state, "_initialized", False) and handler.is_initialized:
            return _wrap_response({{"status": "already_loaded", "message": "Models are already loaded"}})

        results = []

        # Reload primary handler
        try:
            print(f"[API Server] Reloading primary model via /reload...")
            status_msg, ok = handler.initialize_service(**init_params)
            app.state._initialized = ok
            if ok:
                results.append(f"handler1: loaded")
                print(f"[API Server] Primary model reloaded successfully")
            else:
                app.state._init_error = status_msg
                results.append(f"handler1: FAILED - {{status_msg}}")
                print(f"[API Server] Primary model reload FAILED: {{status_msg}}")
        except Exception as e:
            app.state._initialized = False
            app.state._init_error = str(e)
            results.append(f"handler1: ERROR - {{e}}")
            print(f"[API Server] Primary model reload ERROR: {{e}}")

        # Reload secondary handler if it exists
        handler2 = getattr(app.state, "handler2", None)
        config_path2 = getattr(app.state, "_config_path2", "")
        if handler2 and config_path2:
            try:
                params2 = {{**init_params, "config_path": config_path2}}
                status_msg2, ok2 = handler2.initialize_service(**params2)
                app.state._initialized2 = ok2
                results.append(f"handler2: {{'loaded' if ok2 else 'FAILED'}}")
            except Exception as e:
                app.state._initialized2 = False
                results.append(f"handler2: ERROR - {{e}}")

        # Reload third handler if it exists
        handler3 = getattr(app.state, "handler3", None)
        config_path3 = getattr(app.state, "_config_path3", "")
        if handler3 and config_path3:
            try:
                params3 = {{**init_params, "config_path": config_path3}}
                status_msg3, ok3 = handler3.initialize_service(**params3)
                app.state._initialized3 = ok3
                results.append(f"handler3: {{'loaded' if ok3 else 'FAILED'}}")
            except Exception as e:
                app.state._initialized3 = False
                results.append(f"handler3: ERROR - {{e}}")

        any_loaded = getattr(app.state, "_initialized", False)
        return _wrap_response({{
            "status": "loaded" if any_loaded else "failed",
            "results": results,
        }})
'''

# Security: replacement for the /v1/audio endpoint to add path validation.
# The upstream endpoint accepts an arbitrary `path` parameter with no
# validation, allowing path-traversal attacks to read any file on disk.
# This replacement restricts served files to the output directory.
AUDIO_ENDPOINT_SAFE = f'''    {PATCH_MARKER} v{PATCH_VERSION}
    @app.get("/v1/audio")
    async def get_audio(path: str, _: None = Depends(verify_api_key)):
        """Serve audio file by path (with path-traversal protection)."""
        from fastapi.responses import FileResponse

        # Resolve the output directory (where ACEStep writes generated audio)
        _output_base = Path(os.getenv("ACESTEP_OUTPUT_DIR", "output")).resolve()

        resolved = Path(path).resolve()
        # Ensure the resolved path is within the output directory
        if not str(resolved).startswith(str(_output_base) + os.sep) and resolved != _output_base:
            raise HTTPException(status_code=403, detail="Access denied — path outside output directory")

        if not resolved.exists():
            raise HTTPException(status_code=404, detail="Audio file not found")

        ext = resolved.suffix.lower()
        media_types = {{
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".flac": "audio/flac",
            ".ogg": "audio/ogg",
        }}
        media_type = media_types.get(ext, "audio/mpeg")

        return FileResponse(str(resolved), media_type=media_type)
'''

# Original pattern to find and replace
AUDIO_ENDPOINT_ORIGINAL = (
    '    @app.get("/v1/audio")\n'
    '    async def get_audio(path: str, _: None = Depends(verify_api_key)):\n'
    '        """Serve audio file by path."""\n'
    '        from fastapi.responses import FileResponse\n'
    '\n'
    '        if not os.path.exists(path):\n'
    '            raise HTTPException(status_code=404, detail=f"Audio file not found: {path}")\n'
    '\n'
    '        ext = os.path.splitext(path)[1].lower()\n'
    '        media_types = {\n'
    '            ".mp3": "audio/mpeg",\n'
    '            ".wav": "audio/wav",\n'
    '            ".flac": "audio/flac",\n'
    '            ".ogg": "audio/ogg",\n'
    '        }\n'
    '        media_type = media_types.get(ext, "audio/mpeg")\n'
    '\n'
    '        return FileResponse(path, media_type=media_type)'
)


# ─────────────────────────────────────────────────────────────────────────────
# Patch application logic
# ─────────────────────────────────────────────────────────────────────────────

def is_already_patched(filepath: Path) -> bool:
    """Check if a file already contains the patch marker."""
    try:
        content = filepath.read_text(encoding="utf-8")
        return PATCH_MARKER in content
    except FileNotFoundError:
        return False


def patch_handler(handler_path: Path, force: bool = False) -> bool:
    """Patch handler.py with unload_models() and is_initialized."""
    if not handler_path.exists():
        print(f"  WARNING: {handler_path} not found — skipping handler patch")
        return False

    if is_already_patched(handler_path) and not force:
        print(f"  handler.py already patched — skipping")
        return True

    content = handler_path.read_text(encoding="utf-8")
    changes = 0

    # 1. Add "import gc" after the existing imports block
    if "import gc" not in content:
        # Insert after "import math" line
        anchor = "import math\n"
        if anchor in content:
            content = content.replace(anchor, HANDLER_IMPORT_PATCH + "\n" + anchor, 1)
            changes += 1
        else:
            print(f"  WARNING: Could not find 'import math' anchor in handler.py")

    # 2. Add unload_models() and is_initialized before initialize_service()
    if "def unload_models" not in content:
        # Find the anchor: end of get_lora_status, before initialize_service
        anchor = "    def initialize_service(\n"
        if anchor in content:
            content = content.replace(anchor, HANDLER_METHODS_PATCH + "\n" + anchor, 1)
            changes += 1
        else:
            print(f"  WARNING: Could not find 'def initialize_service' anchor in handler.py")

    if changes > 0:
        handler_path.write_text(content, encoding="utf-8")
        print(f"  handler.py patched ({changes} changes)")
    return changes > 0 or is_already_patched(handler_path)


def patch_api_server(api_path: Path, force: bool = False) -> bool:
    """Patch api_server.py with /unload, /reload, enhanced /health, and init params."""
    if not api_path.exists():
        print(f"  WARNING: {api_path} not found — skipping api_server patch")
        return False

    if is_already_patched(api_path) and not force:
        print(f"  api_server.py already patched — skipping")
        return True

    content = api_path.read_text(encoding="utf-8")
    changes = 0

    # 1. Insert _init_params storage before initialize_service call
    if "app.state._init_params" not in content:
        # Anchor: the line that prints loading primary DiT model, right before initialize_service call
        anchor = '        print(f"[API Server] Loading primary DiT model: {config_path}")\n'
        if anchor in content:
            content = content.replace(
                anchor,
                anchor + API_INIT_PARAMS_PATCH,
                1,
            )
            changes += 1
        else:
            print(f"  WARNING: Could not find DiT loading anchor in api_server.py")

    # 2. Replace /health endpoint and add /unload + /reload
    if "def unload_models" not in content:
        # Find the original health endpoint block and replace it
        health_pattern = (
            '    @app.get("/health")\n'
            '    async def health_check():\n'
            '        """Health check endpoint for service status."""\n'
            '        return _wrap_response({\n'
            '            "status": "ok",\n'
            '            "service": "ACE-Step API",\n'
            '            "version": "1.0",\n'
            '        })'
        )
        if health_pattern in content:
            content = content.replace(health_pattern, API_HEALTH_REPLACEMENT, 1)
            changes += 1
        else:
            print(f"  WARNING: Could not find original /health endpoint in api_server.py")
            print(f"           The upstream code may have changed.")

    # 3. Replace /v1/audio endpoint with path-traversal-safe version
    if "path outside output directory" not in content:
        if AUDIO_ENDPOINT_ORIGINAL in content:
            content = content.replace(AUDIO_ENDPOINT_ORIGINAL, AUDIO_ENDPOINT_SAFE, 1)
            changes += 1
        else:
            print(f"  WARNING: Could not find original /v1/audio endpoint in api_server.py")

    if changes > 0:
        api_path.write_text(content, encoding="utf-8")
        print(f"  api_server.py patched ({changes} changes)")
    return changes > 0 or is_already_patched(api_path)


def patch_acestep(acestep_dir: Path, force: bool = False) -> bool:
    """Apply all patches to ACE-Step installation.

    Returns True if all patches applied successfully.
    """
    handler_path = acestep_dir / "acestep" / "handler.py"
    api_path = acestep_dir / "acestep" / "api_server.py"

    print(f"Patching ACE-Step for cooperative VRAM management...")

    handler_ok = patch_handler(handler_path, force=force)
    api_ok = patch_api_server(api_path, force=force)

    if handler_ok and api_ok:
        print(f"ACE-Step patched successfully (cooperative unload/reload support)")
        return True
    else:
        print(f"WARNING: ACE-Step patch partially failed — process-stop eviction will be used as fallback")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Patch ACE-Step for cooperative VRAM unload/reload support"
    )
    parser.add_argument(
        "--acestep-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "acestep",
        help="Path to ACE-Step directory (default: ../acestep relative to this script)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-apply patches even if already present",
    )
    args = parser.parse_args()

    if not args.acestep_dir.exists():
        print(f"ERROR: ACE-Step directory not found: {args.acestep_dir}")
        sys.exit(1)

    success = patch_acestep(args.acestep_dir, force=args.force)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
