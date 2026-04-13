#!/usr/bin/env python3
"""
Post-install patch for ACE-Step: adds cooperative VRAM unload/reload support.

ACE-Step is cloned from upstream (https://github.com/ace-step/ACE-Step-1.5)
and gitignored.  This script is run automatically by start.py after cloning
to add two capabilities the upstream code lacks:

  1. handler.py  – unload_models() method + is_initialized property
  2. api_server.py – POST /unload, POST /reload endpoints

Without these, the only way to free AceStep's VRAM is to kill the process
(and the backend cannot restart it).  With them, the GPU lifecycle service
can cooperatively evict and later reload AceStep models, matching the
pattern used by every other GPU tool.

The upstream already provides:
  - Path-traversal-safe /v1/audio (in api/http/audio_route.py)
  - last_init_params stored on the handler
  - /health with models_initialized status
  - /v1/init for on-demand model initialization

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
PATCH_VERSION = "3"  # v3: added /shutdown endpoint for Phase 2 VRAM eviction


# ─────────────────────────────────────────────────────────────────────────────
# Handler patches  (handler.py — mixin-based since upstream refactor)
# ─────────────────────────────────────────────────────────────────────────────

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
        import gc
        unloaded = []
        try:
            # Unload LoRA first if loaded
            if self.lora_loaded:
                self.unload_lora()

            # Delete all model components
            for attr_name in ("model", "vae", "text_encoder", "text_tokenizer",
                              "silence_latent", "reward_model", "_base_decoder",
                              "mlx_decoder", "mlx_vae"):
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

            from loguru import logger
            logger.info(f"[unload_models] Unloaded: {{unloaded}}")
            return f"Models unloaded: {{', '.join(unloaded)}}"

        except Exception as e:
            from loguru import logger
            logger.exception("[unload_models] Error during unload")
            return f"Error unloading models: {{str(e)}}"

    @property
    def is_initialized(self) -> bool:
        """Check if models are loaded and ready for inference."""
        return self.model is not None and self.vae is not None and self.text_encoder is not None
'''


# ─────────────────────────────────────────────────────────────────────────────
# API server patches  (api_server.py — modular route architecture)
#
# We add /unload and /reload routes directly after configure_api_routes()
# in create_app().  The handler already stores last_init_params natively.
# ─────────────────────────────────────────────────────────────────────────────

API_UNLOAD_RELOAD_ROUTES = f'''
    {PATCH_MARKER} v{PATCH_VERSION}
    # --- Cooperative VRAM management endpoints ---

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

        # Unload LLM handler — check both app.state flag and handler's own flag
        llm = getattr(app.state, "llm_handler", None)
        llm_initialized = (
            getattr(app.state, "_llm_initialized", False)
            or (llm is not None and getattr(llm, "llm_initialized", False))
        )
        if llm and llm_initialized:
            try:
                llm.unload()
                results.append("llm: unloaded")
            except Exception as e:
                results.append(f"llm: ERROR - {{e}}")
        app.state._llm_initialized = False

        # Mark all as uninitialized
        app.state._initialized = False
        app.state._initialized2 = False
        app.state._initialized3 = False
        app.state._init_error = None

        print("[API Server] Models unloaded via /unload endpoint")
        return _wrap_response({{"status": "unloaded", "results": results}})

    @app.post("/reload")
    async def reload_models():
        """Reload models after they were unloaded via /unload.

        Re-initializes the primary handler using the stored init params.
        """
        import asyncio

        handler = getattr(app.state, "handler", None)
        if not handler:
            return _wrap_response(None, code=500, error="No handler available")

        init_params = getattr(handler, "last_init_params", None)
        if not init_params:
            return _wrap_response(None, code=500, error="No init params stored - cannot reload")

        if getattr(app.state, "_initialized", False) and handler.is_initialized:
            return _wrap_response({{"status": "already_loaded", "message": "Models are already loaded"}})

        results = []

        # Reload primary handler
        try:
            print("[API Server] Reloading primary model via /reload...")
            loop = asyncio.get_running_loop()
            status_msg, ok = await loop.run_in_executor(
                getattr(app.state, "executor", None),
                lambda: handler.initialize_service(**init_params),
            )
            app.state._initialized = ok
            if ok:
                results.append("handler1: loaded")
                print("[API Server] Primary model reloaded successfully")
            else:
                app.state._init_error = status_msg
                results.append(f"handler1: FAILED - {{status_msg}}")
        except Exception as e:
            app.state._initialized = False
            app.state._init_error = str(e)
            results.append(f"handler1: ERROR - {{e}}")

        # Reload secondary/third handlers if they exist
        for idx, (h_attr, init_attr, cfg_attr) in enumerate([
            ("handler2", "_initialized2", "_config_path2"),
            ("handler3", "_initialized3", "_config_path3"),
        ], start=2):
            h = getattr(app.state, h_attr, None)
            cfg = getattr(app.state, cfg_attr, "")
            if h and cfg:
                try:
                    params = {{**init_params, "config_path": cfg}}
                    s, ok = await loop.run_in_executor(
                        getattr(app.state, "executor", None),
                        lambda p=params: h.initialize_service(**p),
                    )
                    setattr(app.state, init_attr, ok)
                    results.append(f"handler{{idx}}: {{'loaded' if ok else 'FAILED'}}")
                except Exception as e:
                    setattr(app.state, init_attr, False)
                    results.append(f"handler{{idx}}: ERROR - {{e}}")

        any_loaded = getattr(app.state, "_initialized", False)
        return _wrap_response({{"status": "loaded" if any_loaded else "failed", "results": results}})

    @app.post("/shutdown")
    async def graceful_shutdown():
        """Gracefully terminate the server process to fully release VRAM.

        Used by the GPU lifecycle manager (Phase 2) when /unload alone
        doesn't free CUDA context memory (~5 GB for nanovllm KV cache).
        The service manager will restart the process on the next request.
        """
        import os, signal, threading
        def _kill():
            import time
            time.sleep(0.5)
            os.kill(os.getpid(), signal.SIGTERM)
        threading.Thread(target=_kill, daemon=True).start()
        print("[API Server] Shutdown requested via /shutdown endpoint")
        return _wrap_response({{"status": "shutting_down"}})
'''


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


def _get_patch_version(filepath: Path) -> int:
    """Return the patch version currently applied, or 0 if not patched."""
    try:
        content = filepath.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    m = re.search(r"MONEY-AGENTS-PATCH.*?v(\d+)", content)
    return int(m.group(1)) if m else 0


def patch_handler(handler_path: Path, force: bool = False) -> bool:
    """Patch handler.py with unload_models() and is_initialized."""
    if not handler_path.exists():
        print(f"  WARNING: {handler_path} not found — skipping handler patch")
        return False

    existing_version = _get_patch_version(handler_path)
    if existing_version >= int(PATCH_VERSION) and not force:
        print(f"  handler.py already patched (v{existing_version}) — skipping")
        return True

    content = handler_path.read_text(encoding="utf-8")

    # Remove old patch content if upgrading
    if PATCH_MARKER in content:
        # Remove old unload_models and is_initialized blocks
        lines = content.split("\n")
        new_lines = []
        skip = False
        for line in lines:
            if PATCH_MARKER in line:
                skip = True
                continue
            if skip:
                # End skip when we hit a non-indented line or a new def at same level
                if line and not line.startswith(" ") and not line.startswith("\t"):
                    skip = False
                    new_lines.append(line)
                elif line.startswith("    def ") and "unload_models" not in line and "is_initialized" not in line:
                    skip = False
                    new_lines.append(line)
                # Skip lines that are part of the patched methods
                continue
            new_lines.append(line)
        content = "\n".join(new_lines)

    changes = 0

    # Add unload_models() and is_initialized at end of handler class
    if "def unload_models" not in content:
        # Append to the end of the file (handler.py ends after __init__)
        content = content.rstrip() + "\n" + HANDLER_METHODS_PATCH + "\n"
        changes += 1

    if changes > 0:
        handler_path.write_text(content, encoding="utf-8")
        print(f"  handler.py patched ({changes} changes, v{PATCH_VERSION})")
    return True


def patch_api_server(api_path: Path, force: bool = False) -> bool:
    """Patch api_server.py with /unload and /reload endpoints."""
    if not api_path.exists():
        print(f"  WARNING: {api_path} not found — skipping api_server patch")
        return False

    existing_version = _get_patch_version(api_path)
    if existing_version >= int(PATCH_VERSION) and not force:
        print(f"  api_server.py already patched (v{existing_version}) — skipping")
        return True

    content = api_path.read_text(encoding="utf-8")

    # Remove old patch content if upgrading
    if PATCH_MARKER in content:
        # Remove everything from the patch marker to the end of the patched block
        marker_idx = content.find(PATCH_MARKER)
        if marker_idx >= 0:
            # Find the start of the line containing the marker
            line_start = content.rfind("\n", 0, marker_idx)
            if line_start < 0:
                line_start = 0
            # Remove from there to end of file, then add back the return/app/main lines
            pre_patch = content[:line_start]
            # Find "    return app" after the marker — that's where we resume
            return_idx = content.find("    return app", marker_idx)
            if return_idx >= 0:
                post_patch = content[return_idx:]
                content = pre_patch + "\n\n" + post_patch
            else:
                # Fallback: just strip the old marker lines
                content = "\n".join(
                    line for line in content.split("\n")
                    if PATCH_MARKER not in line
                )

    changes = 0

    # Insert /unload and /reload routes after configure_api_routes() call
    if "def unload_models" not in content:
        # Find the anchor: "    return app" at the end of create_app()
        anchor = "    return app\n"
        if anchor in content:
            content = content.replace(
                anchor,
                API_UNLOAD_RELOAD_ROUTES + "\n" + anchor,
                1,
            )
            changes += 1
        else:
            print(f"  WARNING: Could not find 'return app' anchor in api_server.py")

    if changes > 0:
        api_path.write_text(content, encoding="utf-8")
        print(f"  api_server.py patched ({changes} changes, v{PATCH_VERSION})")
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
