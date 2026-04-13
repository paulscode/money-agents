#!/usr/bin/env python3
"""
Download all required model files for LTX-2.3 Video Generation.

Downloads from HuggingFace (~96 GB total on disk):
  - ltx-2.3-22b-distilled.safetensors     (46 GB) — DiT checkpoint (official bf16 weights)
  - ltx-2.3-spatial-upscaler-x2-1.1.safetensors (~1 GB) — Spatial upscaler
  - Gemma 3 12B text encoder weights        (~49 GB) — Text encoder (streamed on low-VRAM GPUs)
  - Gemma tokenizer + config files           (~39 MB) — Required for text encoding

All files come from Lightricks/LTX-2.3.
No gated-model access is required — no HuggingFace token needed.
Resume is supported — interrupted downloads continue where they left off.

Usage:
  python download_models.py              # Download all missing files
  python download_models.py --check      # Only check what's missing
  python download_models.py --force      # Re-download everything
  python download_models.py --skip-weights  # Skip large text encoder weights (dev/debug)
"""

import argparse
import sys
from pathlib import Path

REPO_ID = "Lightricks/LTX-2.3"

# Official bf16 checkpoint from Lightricks
CHECKPOINT = "ltx-2.3-22b-distilled.safetensors"

# Files to download from the main repo → models/ltx-2/
UPSCALER_FILES = [
    (REPO_ID, "ltx-2.3-spatial-upscaler-x2-1.1.safetensors", "Spatial upscaler v1.1 (~1 GB)"),
]

# Selective patterns for Gemma text encoder → models/ltx-2/<gemma_dir>/
# Uses only model-* shards (NOT diffusion_pytorch_model-*) to avoid
# duplicate weight loading by ltx-pipelines' rglob("*.safetensors").
GEMMA_WEIGHT_PATTERNS = [
    "text_encoder/config.json",
    "text_encoder/model-*.safetensors",
    "text_encoder/model.safetensors.index.json",
]

GEMMA_TOKENIZER_PATTERNS = [
    "tokenizer/added_tokens.json",
    "tokenizer/preprocessor_config.json",
    "tokenizer/processor_config.json",
    "tokenizer/special_tokens_map.json",
    "tokenizer/tokenizer.json",
    "tokenizer/tokenizer.model",
    "tokenizer/tokenizer_config.json",
]


def get_model_dir():
    """Determine model directory and gemma subdir from config.yaml."""
    script_dir = Path(__file__).parent
    config_path = script_dir / "config.yaml"

    model_dir_str = "../models/ltx-2"
    gemma_dir_name = "gemma-3-12b"

    if config_path.exists():
        try:
            import yaml

            with open(config_path) as f:
                config = yaml.safe_load(f) or {}
            model_config = config.get("model", {})
            model_dir_str = model_config.get("model_dir", model_dir_str)
            gemma_dir_name = model_config.get("gemma_dir", gemma_dir_name)
        except Exception:
            pass  # Fall through to defaults

    model_path = Path(model_dir_str)
    if not model_path.is_absolute():
        model_path = (script_dir / model_path).resolve()

    return model_path, gemma_dir_name


def check_models(model_dir: Path, gemma_dir_name: str) -> list:
    """
    Check which model components are missing.

    Returns list of (component_name, description, size_hint) tuples.
    """
    missing = []

    # BF16 checkpoint (official weights from Lightricks)
    ckpt_path = model_dir / CHECKPOINT
    if not ckpt_path.exists():
        missing.append((CHECKPOINT, "LTX-2.3 bf16 checkpoint (46 GB)", ckpt_path))

    # Upscaler
    for repo_id, filename, desc in UPSCALER_FILES:
        filepath = model_dir / filename
        if not filepath.exists():
            missing.append((filename, desc, filepath))

    # Gemma text encoder weights (11 shards)
    gemma_root = model_dir / gemma_dir_name
    model_shards = sorted(gemma_root.rglob("model-*.safetensors")) if gemma_root.exists() else []
    if len(model_shards) < 11:
        missing.append((
            "gemma_weights",
            f"Gemma 3 12B text encoder (~49 GB, {len(model_shards)}/11 shards present)",
            gemma_root / "text_encoder",
        ))

    # Gemma tokenizer
    tokenizer_model = gemma_root / "tokenizer" / "tokenizer.model"
    preprocessor_cfg = gemma_root / "tokenizer" / "preprocessor_config.json"
    if not tokenizer_model.exists() or not preprocessor_cfg.exists():
        missing.append((
            "gemma_tokenizer",
            "Gemma tokenizer + config (~39 MB)",
            gemma_root / "tokenizer",
        ))

    return missing


def download_models(model_dir: Path, gemma_dir_name: str, force: bool = False, skip_weights: bool = False):
    """Download all required model files from HuggingFace."""
    try:
        from huggingface_hub import hf_hub_download, snapshot_download
    except ImportError:
        print("ERROR: huggingface_hub not installed.  pip install huggingface_hub")
        return False

    model_dir.mkdir(parents=True, exist_ok=True)
    gemma_root = model_dir / gemma_dir_name
    ok = True

    # ---- 1. BF16 checkpoint (direct download) ----
    ckpt_path = model_dir / CHECKPOINT
    if not force and ckpt_path.exists():
        print(f"  ✓ LTX-2.3 bf16 checkpoint — already present")
    else:
        print(f"\n  Downloading bf16 checkpoint (46 GB)...")
        print(f"  Source: {REPO_ID}")
        try:
            hf_hub_download(
                repo_id=REPO_ID,
                filename=CHECKPOINT,
                local_dir=str(model_dir),
            )
            print(f"  ✓ bf16 checkpoint downloaded")
        except Exception as e:
            print(f"  ✗ Failed to download checkpoint: {e}")
            ok = False

    # ---- 2. Upscaler ----
    for repo_id, filename, desc in UPSCALER_FILES:
        filepath = model_dir / filename
        if not force and filepath.exists():
            print(f"  ✓ {desc} — already present")
            continue

        print(f"\n  Downloading {desc}...")
        print(f"  Source: {repo_id}")
        print(f"  Destination: {filepath}")
        try:
            hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                local_dir=str(model_dir),
            )
            print(f"  ✓ {desc} — done")
        except Exception as e:
            print(f"  ✗ Failed to download {filename}: {e}")
            ok = False

    # ---- 3. Gemma text encoder weights ----
    model_shards = sorted(gemma_root.rglob("model-*.safetensors")) if gemma_root.exists() else []
    need_weights = force or len(model_shards) < 11

    if need_weights and not skip_weights:
        print(f"\n  Downloading Gemma 3 12B text encoder weights (~49 GB)...")
        print(f"  On low-VRAM GPUs (≤26 GB), layers stream through GPU one at a time.")
        print(f"  Destination: {gemma_root / 'text_encoder'}")
        try:
            snapshot_download(
                repo_id=REPO_ID,
                allow_patterns=GEMMA_WEIGHT_PATTERNS,
                local_dir=str(gemma_root),
            )
            print(f"  ✓ Gemma text encoder weights — done")
        except Exception as e:
            print(f"  ✗ Failed to download text encoder: {e}")
            ok = False
    elif skip_weights and need_weights:
        print(f"  ⊘ Skipping Gemma weights (--skip-weights)")
    else:
        print(f"  ✓ Gemma text encoder weights — already present ({len(model_shards)} shards)")

    # ---- 4. Gemma tokenizer ----
    tokenizer_model = gemma_root / "tokenizer" / "tokenizer.model"
    preprocessor_cfg = gemma_root / "tokenizer" / "preprocessor_config.json"
    need_tokenizer = force or not tokenizer_model.exists() or not preprocessor_cfg.exists()

    if need_tokenizer:
        print(f"\n  Downloading Gemma tokenizer (~39 MB)...")
        try:
            snapshot_download(
                repo_id=REPO_ID,
                allow_patterns=GEMMA_TOKENIZER_PATTERNS,
                local_dir=str(gemma_root),
            )
            print(f"  ✓ Gemma tokenizer — done")
        except Exception as e:
            print(f"  ✗ Failed to download tokenizer: {e}")
            ok = False
    else:
        print(f"  ✓ Gemma tokenizer — already present")

    if ok:
        print(f"\n  ✅ All LTX-2.3 model files ready in {model_dir}")
    else:
        print(f"\n  ⚠  Some downloads failed. Re-run to retry (resume supported).")

    return ok


def main():
    parser = argparse.ArgumentParser(
        description="Download LTX-2.3 model files from HuggingFace"
    )
    parser.add_argument(
        "--model-dir", type=str, default=None,
        help="Override model directory path"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-download all files even if present"
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Only check what's missing, don't download"
    )
    parser.add_argument(
        "--skip-weights", action="store_true",
        help="Skip the large text encoder weights (for testing)"
    )
    args = parser.parse_args()

    model_dir, gemma_dir_name = get_model_dir()
    if args.model_dir:
        model_dir = Path(args.model_dir).resolve()

    print(f"LTX-2.3 Model Directory: {model_dir}")
    print(f"Gemma subdirectory:    {gemma_dir_name}")

    if args.check:
        missing = check_models(model_dir, gemma_dir_name)
        if missing:
            print(f"\nMissing {len(missing)} component(s):")
            for name, desc, path in missing:
                print(f"  ✗ {desc}")
                print(f"    → {path}")
            sys.exit(1)
        else:
            print(f"\n✅ All model files present")
            sys.exit(0)

    success = download_models(
        model_dir, gemma_dir_name,
        force=args.force,
        skip_weights=args.skip_weights,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
