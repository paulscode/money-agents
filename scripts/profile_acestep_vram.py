#!/usr/bin/env python3
"""
Empirical VRAM profiler for ACE-Step music generation models.

Measures actual GPU memory usage for each model variant and configuration,
then compares against the theoretical estimates in gpu_config.py.

Usage:
    # Profile all models the server supports (requires running ACE-Step API):
    python scripts/profile_acestep_vram.py --api-url http://localhost:8001

    # Profile specific model:
    python scripts/profile_acestep_vram.py --api-url http://localhost:8001 --model xl-turbo

    # Just read current GPU state (no generation):
    python scripts/profile_acestep_vram.py --gpu-only

    # Output JSON for further analysis:
    python scripts/profile_acestep_vram.py --api-url http://localhost:8001 --json

Requirements:
    pip install httpx pynvml
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import httpx
except ImportError:
    print("ERROR: httpx required. Install with: pip install httpx")
    sys.exit(1)


# ──────────────────────────────────────────────────────────────────────
# NVIDIA GPU monitoring
# ──────────────────────────────────────────────────────────────────────

def _get_nvidia_gpu_info() -> List[Dict[str, Any]]:
    """Get GPU memory info via pynvml (NVML)."""
    try:
        import pynvml
        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        gpus = []
        for i in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8")
            gpus.append({
                "index": i,
                "name": name,
                "total_gb": round(info.total / (1024**3), 2),
                "used_gb": round(info.used / (1024**3), 2),
                "free_gb": round(info.free / (1024**3), 2),
            })
        pynvml.nvmlShutdown()
        return gpus
    except Exception:
        pass

    # Fallback: nvidia-smi
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10
        )
        gpus = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(",")]
            gpus.append({
                "index": int(parts[0]),
                "name": parts[1],
                "total_gb": round(float(parts[2]) / 1024, 2),
                "used_gb": round(float(parts[3]) / 1024, 2),
                "free_gb": round(float(parts[4]) / 1024, 2),
            })
        return gpus
    except Exception as e:
        print(f"WARNING: Cannot read GPU info: {e}")
        return []


def get_gpu_vram_used(gpu_index: int = 0) -> float:
    """Return current VRAM usage in GB for the given GPU."""
    gpus = _get_nvidia_gpu_info()
    for g in gpus:
        if g["index"] == gpu_index:
            return g["used_gb"]
    return 0.0


# ──────────────────────────────────────────────────────────────────────
# Profiling data structures
# ──────────────────────────────────────────────────────────────────────

@dataclass
class VRAMSnapshot:
    """Single VRAM measurement point."""
    label: str
    used_gb: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class ModelProfile:
    """Profile result for a single model configuration."""
    model: str
    snapshots: List[VRAMSnapshot] = field(default_factory=list)
    baseline_gb: float = 0.0
    after_init_gb: float = 0.0
    peak_inference_gb: float = 0.0
    after_unload_gb: float = 0.0

    # Derived
    init_delta_gb: float = 0.0  # VRAM consumed by model loading
    inference_delta_gb: float = 0.0  # Additional VRAM during inference
    total_peak_gb: float = 0.0

    # Generation parameters used
    duration: float = 30.0
    steps: int = 8
    batch_size: int = 1

    # Timing
    init_time_s: float = 0.0
    gen_time_s: float = 0.0

    error: Optional[str] = None

    def compute_deltas(self):
        self.init_delta_gb = round(self.after_init_gb - self.baseline_gb, 2)
        self.inference_delta_gb = round(self.peak_inference_gb - self.after_init_gb, 2)
        self.total_peak_gb = round(self.peak_inference_gb, 2)


# ──────────────────────────────────────────────────────────────────────
# ACE-Step API interaction
# ──────────────────────────────────────────────────────────────────────

MODELS = {
    "turbo": {"config": "acestep-v15-turbo", "steps": 8, "is_turbo": True},
    "base": {"config": "acestep-v15-base", "steps": 45, "is_turbo": False},
    "xl-turbo": {"config": "acestep-v15-xl-turbo", "steps": 8, "is_turbo": True},
    "xl-base": {"config": "acestep-v15-xl-base", "steps": 50, "is_turbo": False},
    "xl-sft": {"config": "acestep-v15-xl-sft", "steps": 50, "is_turbo": False},
}

# Theoretical estimates from gpu_config.py for comparison
# Includes DiT weights + VAE + text_encoder + cuda_context + LM 0.6B (default)
THEORETICAL_VRAM = {
    "turbo": {"dit": 4.7, "vae": 0.33, "text_encoder": 1.2, "lm": 1.5, "kv_cache": 3.1, "cuda": 0.5, "inference_per_batch": 0.3},
    "base": {"dit": 4.7, "vae": 0.33, "text_encoder": 1.2, "lm": 1.5, "kv_cache": 3.1, "cuda": 0.5, "inference_per_batch": 0.6},
    "xl-turbo": {"dit": 9.0, "vae": 0.33, "text_encoder": 1.2, "lm": 1.5, "kv_cache": 3.1, "cuda": 0.5, "inference_per_batch": 0.5},
    "xl-base": {"dit": 9.0, "vae": 0.33, "text_encoder": 1.2, "lm": 1.5, "kv_cache": 3.1, "cuda": 0.5, "inference_per_batch": 1.0},
    "xl-sft": {"dit": 9.0, "vae": 0.33, "text_encoder": 1.2, "lm": 1.5, "kv_cache": 3.1, "cuda": 0.5, "inference_per_batch": 1.0},
}


def check_health(base_url: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    """Check ACE-Step API health."""
    headers = {"X-API-Key": api_key} if api_key else {}
    resp = httpx.get(f"{base_url}/health", headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()


def unload_models(base_url: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    """Unload all models via /unload endpoint."""
    headers = {"X-API-Key": api_key} if api_key else {}
    resp = httpx.post(f"{base_url}/unload", headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def init_model(base_url: str, model_config: str, api_key: Optional[str] = None, init_llm: bool = True) -> Dict[str, Any]:
    """Initialize a specific model via /v1/init."""
    headers = {"X-API-Key": api_key} if api_key else {}
    payload = {"model": model_config, "init_llm": init_llm}
    resp = httpx.post(f"{base_url}/v1/init", headers=headers, json=payload, timeout=300)
    resp.raise_for_status()
    return resp.json()


def generate_music(
    base_url: str,
    *,
    model_config: str,
    steps: int = 8,
    duration: float = 30.0,
    batch_size: int = 1,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Submit a music generation task. Returns the release_task response (async, queued)."""
    headers = {"X-API-Key": api_key} if api_key else {}
    payload = {
        "prompt": "upbeat electronic pop",
        "lyrics": "",
        "audio_duration": duration,
        "inference_steps": steps,
        "guidance_scale": 3.5 if "turbo" in model_config else 5.0,
        "batch_size": batch_size,
        "use_random_seed": True,
        "seed": -1,
        "audio_format": "mp3",
        "model": model_config,
    }
    resp = httpx.post(
        f"{base_url}/release_task",
        headers=headers,
        json=payload,
        timeout=30,  # Just submitting, not waiting for generation
    )
    resp.raise_for_status()
    return resp.json()


def query_task_result(
    base_url: str,
    task_id: str,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Poll task status via POST /query_result. Returns first result item."""
    headers = {"X-API-Key": api_key} if api_key else {}
    resp = httpx.post(
        f"{base_url}/query_result",
        headers=headers,
        json={"task_id_list": [task_id]},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    # Response: {data: [{task_id, result, status, progress_text}], code: 200}
    items = data.get("data", [])
    if items and isinstance(items, list):
        return items[0]
    return {"status": -1}


# ──────────────────────────────────────────────────────────────────────
# VRAM monitoring during generation
# ──────────────────────────────────────────────────────────────────────

def monitor_vram_during_task(
    base_url: str,
    task_id: str,
    gpu_index: int = 0,
    api_key: Optional[str] = None,
    poll_interval: float = 0.5,
    timeout: float = 600.0,
) -> List[VRAMSnapshot]:
    """Poll VRAM usage while a task is running.

    Uses POST /query_result to check task status.
    Status codes: 0 = running, 1 = completed, 2 = failed/timeout.
    """
    snapshots = []
    start = time.time()
    while (time.time() - start) < timeout:
        vram = get_gpu_vram_used(gpu_index)
        snapshots.append(VRAMSnapshot(label="inference", used_gb=vram))

        # Check task status via /query_result
        try:
            result = query_task_result(base_url, task_id, api_key)
            status = result.get("status", -1)
            if status == 1:  # completed
                break
            if status == 2:  # failed
                progress = result.get("progress_text", "")
                print(f"    Task failed: {progress}")
                break
        except Exception:
            pass

        time.sleep(poll_interval)

    return snapshots


# ──────────────────────────────────────────────────────────────────────
# Main profiling loop
# ──────────────────────────────────────────────────────────────────────

def profile_model(
    base_url: str,
    model_name: str,
    gpu_index: int = 0,
    duration: float = 30.0,
    batch_size: int = 1,
    api_key: Optional[str] = None,
) -> ModelProfile:
    """Profile a single model variant end-to-end."""
    model_info = MODELS[model_name]
    profile = ModelProfile(
        model=model_name,
        duration=duration,
        steps=model_info["steps"],
        batch_size=batch_size,
    )

    try:
        # 1. Unload everything first to get a clean baseline
        print(f"  [{model_name}] Unloading existing models...")
        try:
            unload_models(base_url, api_key)
            time.sleep(2)  # Allow GPU memory to settle
        except Exception as e:
            print(f"  [{model_name}] Unload failed (may not be loaded): {e}")

        # 2. Record baseline VRAM (nothing loaded)
        profile.baseline_gb = get_gpu_vram_used(gpu_index)
        profile.snapshots.append(VRAMSnapshot("baseline", profile.baseline_gb))
        print(f"  [{model_name}] Baseline VRAM: {profile.baseline_gb:.2f} GB")

        # 3. Initialize the model
        print(f"  [{model_name}] Initializing model ({model_info['config']})...")
        t0 = time.time()
        init_model(base_url, model_info["config"], api_key)
        profile.init_time_s = round(time.time() - t0, 1)
        time.sleep(2)  # Allow memory to settle

        profile.after_init_gb = get_gpu_vram_used(gpu_index)
        profile.snapshots.append(VRAMSnapshot("after_init", profile.after_init_gb))
        print(f"  [{model_name}] After init VRAM: {profile.after_init_gb:.2f} GB "
              f"(+{profile.after_init_gb - profile.baseline_gb:.2f} GB, {profile.init_time_s}s)")

        # 4. Run inference and monitor VRAM
        print(f"  [{model_name}] Generating {duration}s of audio "
              f"({model_info['steps']} steps, batch={batch_size})...")
        t0 = time.time()
        result = generate_music(
            base_url,
            model_config=model_info["config"],
            steps=model_info["steps"],
            duration=duration,
            batch_size=batch_size,
            api_key=api_key,
        )

        # Extract task_id from async response
        task_id = (result.get("data", {}).get("task_id")
                   or result.get("task_id"))

        if task_id:
            # Monitor VRAM while task runs
            inference_snaps = monitor_vram_during_task(
                base_url, task_id, gpu_index, api_key,
                timeout=max(600, int(duration * 10)),
            )
            profile.gen_time_s = round(time.time() - t0, 1)
            profile.snapshots.extend(inference_snaps)
            if inference_snaps:
                profile.peak_inference_gb = max(s.used_gb for s in inference_snaps)
            else:
                profile.peak_inference_gb = get_gpu_vram_used(gpu_index)
        else:
            # No task_id — might be a synchronous error
            profile.gen_time_s = round(time.time() - t0, 1)
            profile.peak_inference_gb = get_gpu_vram_used(gpu_index)
            print(f"  [{model_name}] WARNING: No task_id returned: {result}")

        profile.snapshots.append(VRAMSnapshot("peak_inference", profile.peak_inference_gb))
        print(f"  [{model_name}] Peak inference VRAM: {profile.peak_inference_gb:.2f} GB "
              f"(+{profile.peak_inference_gb - profile.after_init_gb:.2f} GB over init)")
        print(f"  [{model_name}] Generation time: {profile.gen_time_s}s")

        # 5. Unload and measure recovery
        print(f"  [{model_name}] Unloading models...")
        unload_models(base_url, api_key)
        time.sleep(2)
        profile.after_unload_gb = get_gpu_vram_used(gpu_index)
        profile.snapshots.append(VRAMSnapshot("after_unload", profile.after_unload_gb))
        print(f"  [{model_name}] After unload VRAM: {profile.after_unload_gb:.2f} GB")

    except Exception as e:
        profile.error = str(e)
        print(f"  [{model_name}] ERROR: {e}")

    profile.compute_deltas()
    return profile


def print_comparison_table(profiles: List[ModelProfile]):
    """Print a comparison of measured vs theoretical VRAM."""
    print("\n" + "=" * 90)
    print("VRAM PROFILING RESULTS")
    print("=" * 90)

    # Header
    print(f"{'Model':<12} {'Baseline':>9} {'Init':>9} {'Peak':>9} "
          f"{'Init Δ':>9} {'Inf Δ':>9} {'Theory':>9} {'Diff':>8} "
          f"{'Init(s)':>8} {'Gen(s)':>8}")
    print("-" * 90)

    for p in profiles:
        if p.error:
            print(f"{p.model:<12} ERROR: {p.error}")
            continue

        theory = THEORETICAL_VRAM.get(p.model, {})
        # Theoretical total = dit + vae + text_encoder + lm + kv_cache + cuda_context + inference
        theory_init = (theory.get("dit", 0) + theory.get("vae", 0) +
                       theory.get("text_encoder", 0) + theory.get("lm", 0) +
                       theory.get("kv_cache", 0) + theory.get("cuda", 0.5))
        theory_peak = theory_init + theory.get("inference_per_batch", 0) * p.batch_size
        diff = p.total_peak_gb - theory_peak if theory_peak > 0 else 0

        print(f"{p.model:<12} {p.baseline_gb:>8.2f}G {p.after_init_gb:>8.2f}G "
              f"{p.total_peak_gb:>8.2f}G {p.init_delta_gb:>+8.2f}G "
              f"{p.inference_delta_gb:>+8.2f}G {theory_peak:>8.2f}G "
              f"{diff:>+7.2f}G {p.init_time_s:>7.1f}s {p.gen_time_s:>7.1f}s")

    print("-" * 90)

    # Summary
    if profiles:
        print(f"\nUnload recovery: ", end="")
        for p in profiles:
            if not p.error:
                leaked = p.after_unload_gb - p.baseline_gb
                status = "OK" if leaked < 0.3 else f"LEAKED {leaked:.2f}G"
                print(f"{p.model}={status}  ", end="")
        print()


def main():
    parser = argparse.ArgumentParser(description="Profile ACE-Step VRAM usage")
    parser.add_argument("--api-url", default="http://localhost:8001",
                        help="ACE-Step API URL")
    parser.add_argument("--api-key", default=None, help="API key")
    parser.add_argument("--model", default=None,
                        choices=list(MODELS.keys()),
                        help="Profile a specific model (default: all available)")
    parser.add_argument("--duration", type=float, default=30.0,
                        help="Audio duration in seconds for test generation")
    parser.add_argument("--batch-size", type=int, default=1,
                        help="Batch size for test generation")
    parser.add_argument("--gpu-index", type=int, default=0,
                        help="GPU index to monitor")
    parser.add_argument("--gpu-only", action="store_true",
                        help="Just print current GPU state, no profiling")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON")
    args = parser.parse_args()

    # GPU-only mode
    if args.gpu_only:
        gpus = _get_nvidia_gpu_info()
        if not gpus:
            print("No GPUs found")
            sys.exit(1)
        for g in gpus:
            print(f"GPU {g['index']}: {g['name']}")
            print(f"  Total: {g['total_gb']:.2f} GB")
            print(f"  Used:  {g['used_gb']:.2f} GB")
            print(f"  Free:  {g['free_gb']:.2f} GB")
        sys.exit(0)

    # Check server health
    print(f"Connecting to ACE-Step at {args.api_url}...")
    try:
        health = check_health(args.api_url, args.api_key)
        print(f"Server healthy: {health}")
    except Exception as e:
        print(f"ERROR: Cannot connect to ACE-Step: {e}")
        print("Start the server first: ACESTEP_LAZY_LOAD=1 uv run acestep-api")
        sys.exit(1)

    # GPU info
    gpus = _get_nvidia_gpu_info()
    if gpus:
        gpu = gpus[args.gpu_index] if args.gpu_index < len(gpus) else gpus[0]
        print(f"GPU: {gpu['name']} ({gpu['total_gb']:.1f} GB total, "
              f"{gpu['used_gb']:.1f} GB used, {gpu['free_gb']:.1f} GB free)")
    else:
        print("WARNING: Cannot read GPU info — VRAM measurements will be unavailable")

    # Determine models to profile
    if args.model:
        models_to_profile = [args.model]
    else:
        # Profile all standard models (skip xl-sft as it shares xl-base VRAM profile)
        models_to_profile = ["turbo", "base", "xl-turbo", "xl-base"]

    # Run profiles
    print(f"\nProfiling {len(models_to_profile)} model(s): {', '.join(models_to_profile)}")
    print(f"Parameters: duration={args.duration}s, batch_size={args.batch_size}")
    print()

    profiles = []
    for model in models_to_profile:
        print(f"--- Profiling: {model} ---")
        profile = profile_model(
            args.api_url,
            model,
            gpu_index=args.gpu_index,
            duration=args.duration,
            batch_size=args.batch_size,
            api_key=args.api_key,
        )
        profiles.append(profile)
        print()

    # Output
    if args.json:
        output = {
            "gpu": gpus[args.gpu_index] if gpus and args.gpu_index < len(gpus) else None,
            "parameters": {
                "duration": args.duration,
                "batch_size": args.batch_size,
            },
            "profiles": [asdict(p) for p in profiles],
            "theoretical": THEORETICAL_VRAM,
        }
        print(json.dumps(output, indent=2, default=str))
    else:
        print_comparison_table(profiles)


if __name__ == "__main__":
    main()
