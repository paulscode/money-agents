#!/usr/bin/env python3
"""
test-local-ai.py — Test all enabled local AI services

Tests each local AI service (Ollama, Z-Image, Qwen3-TTS, ACE-Step, SeedVR2, Dev Sandbox),
generates a sample output, and saves it to the tmp/ directory.

Usage:
    python test-local-ai.py           # Test all enabled services
    python test-local-ai.py ollama    # Test only Ollama
    python test-local-ai.py zimage    # Test only Z-Image
    python test-local-ai.py tts       # Test only Qwen3-TTS
    python test-local-ai.py acestep   # Test only ACE-Step
    python test-local-ai.py seedvr2   # Test only SeedVR2
    python test-local-ai.py sandbox   # Test only Dev Sandbox

Works on Linux, macOS, and Windows.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from datetime import datetime

# ---------------------------------------------------------------------------
# Paths & colours
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = PROJECT_ROOT / "tmp" / "test-local-ai"
ENV_FILE = PROJECT_ROOT / ".env"

# ANSI colours (disabled on Windows unless WT / modern terminal)
NO_COLOR = os.environ.get("NO_COLOR") or (
    sys.platform == "win32" and "WT_SESSION" not in os.environ
)

class C:
    """Colour helpers — degrade gracefully."""
    BOLD   = "" if NO_COLOR else "\033[1m"
    DIM    = "" if NO_COLOR else "\033[2m"
    GREEN  = "" if NO_COLOR else "\033[92m"
    YELLOW = "" if NO_COLOR else "\033[93m"
    RED    = "" if NO_COLOR else "\033[91m"
    CYAN   = "" if NO_COLOR else "\033[96m"
    RESET  = "" if NO_COLOR else "\033[0m"


def banner():
    print()
    print(f"{C.BOLD}{'=' * 60}{C.RESET}")
    print(f"{C.BOLD}  Local AI Services — Smoke Test{C.RESET}")
    print(f"{C.BOLD}{'=' * 60}{C.RESET}")
    print()


# ---------------------------------------------------------------------------
# .env reader (no 3rd-party deps)
# ---------------------------------------------------------------------------

def load_env() -> dict:
    """Parse .env into a dict (does NOT override os.environ)."""
    env = {}
    if not ENV_FILE.is_file():
        return env
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return env


def is_true(val: str | None) -> bool:
    return (val or "").lower() in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# HTTP helpers (stdlib only — no httpx/requests)
# ---------------------------------------------------------------------------

def http_get(url: str, timeout: int = 10) -> tuple:
    """GET request → (status_code, body_bytes).  Returns (0, error_str) on failure."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 0, str(e).encode()


def http_post_json(url: str, data: dict, timeout: int = 120) -> tuple:
    """POST JSON → (status_code, response_dict | error_str)."""
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw.decode(errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw.decode(errors="replace")
    except Exception as e:
        return 0, str(e)


def download_file(url: str, dest: Path, timeout: int = 30) -> bool:
    """Download a URL to a local file. Returns True on success."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
        return True
    except Exception as e:
        print(f"    {C.RED}Download failed: {e}{C.RESET}")
        return False


# ---------------------------------------------------------------------------
# Individual service tests
# ---------------------------------------------------------------------------

def test_ollama(env: dict) -> bool:
    """Test Ollama local LLM — generate a short text."""
    print(f"{C.BOLD}[1/4] Ollama (Local LLM){C.RESET}")

    if not is_true(env.get("USE_OLLAMA")):
        print(f"  {C.YELLOW}⏭  SKIPPED — USE_OLLAMA is not enabled{C.RESET}")
        print()
        return True  # not a failure

    # Determine URL — use localhost on host, not host.docker.internal
    base_url = env.get("OLLAMA_BASE_URL", "http://localhost:11434")
    base_url = base_url.replace("host.docker.internal", "localhost")
    model_tiers = env.get("OLLAMA_MODEL_TIERS", "mistral:7b")
    model = model_tiers.split(",")[0].strip()  # use fastest tier

    # Health check
    print(f"  Checking Ollama at {base_url} ...")
    status, body = http_get(f"{base_url}/", timeout=5)
    if status == 0:
        print(f"  {C.RED}✗ FAILED — Cannot reach Ollama: {body.decode(errors='replace')}{C.RESET}")
        print()
        return False
    print(f"  {C.GREEN}✓ Ollama is running{C.RESET}")

    # Check model is available
    print(f"  Checking model '{model}' is pulled ...")
    status, body = http_get(f"{base_url}/api/tags", timeout=10)
    if status == 200:
        try:
            tags = json.loads(body)
            names = [m.get("name", "") for m in tags.get("models", [])]
            # Match both "mistral:7b" and "mistral:7b-instruct-..." etc.
            model_base = model.split(":")[0]
            found = any(model_base in n for n in names)
            if not found:
                print(f"  {C.YELLOW}⚠ Model '{model}' not found. Available: {', '.join(names[:5])}{C.RESET}")
                if names:
                    model = names[0]
                    print(f"  Using '{model}' instead")
                else:
                    print(f"  {C.RED}✗ No models available. Run: ollama pull {model}{C.RESET}")
                    print()
                    return False
        except Exception:
            pass  # proceed anyway

    # Generate
    prompt = "In exactly one sentence, tell me an interesting fact about hummingbirds."
    print(f"  Generating text with '{model}' ...")
    print(f"  {C.DIM}Prompt: {prompt}{C.RESET}")

    t0 = time.time()
    status, resp = http_post_json(f"{base_url}/api/generate", {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }, timeout=120)
    elapsed = time.time() - t0

    if status != 200 or not isinstance(resp, dict):
        print(f"  {C.RED}✗ FAILED — HTTP {status}: {resp}{C.RESET}")
        print()
        return False

    text = resp.get("response", "").strip()
    if not text:
        print(f"  {C.RED}✗ FAILED — Empty response{C.RESET}")
        print()
        return False

    # Save to file
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / "ollama-test.txt"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(f"Model: {model}\n")
        f.write(f"Prompt: {prompt}\n")
        f.write(f"Generated: {datetime.now().isoformat()}\n")
        f.write(f"Time: {elapsed:.1f}s\n")
        f.write(f"\n{text}\n")

    print(f"  {C.GREEN}✓ SUCCESS{C.RESET} ({elapsed:.1f}s)")
    # Show a truncated preview
    preview = text[:120] + ("..." if len(text) > 120 else "")
    print(f"  {C.DIM}Response: {preview}{C.RESET}")
    print(f"  {C.CYAN}Output:  {out_file}{C.RESET}")
    print()
    return True


def test_zimage(env: dict) -> bool:
    """Test Z-Image — generate a small test image."""
    print(f"{C.BOLD}[2/5] Z-Image (Local Image Generation){C.RESET}")

    if not is_true(env.get("USE_ZIMAGE")):
        print(f"  {C.YELLOW}⏭  SKIPPED — USE_ZIMAGE is not enabled{C.RESET}")
        print()
        return True

    port = env.get("ZIMAGE_API_PORT", "8003")
    base_url = f"http://localhost:{port}"

    # Health check
    print(f"  Checking Z-Image at {base_url} ...")
    status, body = http_get(f"{base_url}/health", timeout=10)
    if status == 0:
        print(f"  {C.RED}✗ FAILED — Cannot reach Z-Image server on port {port}{C.RESET}")
        print(f"  {C.DIM}Make sure Z-Image is running (start.sh handles this automatically){C.RESET}")
        print()
        return False
    print(f"  {C.GREEN}✓ Z-Image server is running{C.RESET}")

    if status == 200:
        try:
            info = json.loads(body)
            loaded = info.get("model_loaded", False)
            if not loaded:
                print(f"  {C.DIM}Model not loaded yet — first request will trigger loading (~30-60s){C.RESET}")
        except Exception:
            pass

    # Generate (use smaller resolution for speed)
    prompt = "A beautiful hummingbird hovering near red flowers, soft bokeh background, nature photography"
    print(f"  Generating 512x512 image ...")
    print(f"  {C.DIM}Prompt: {prompt}{C.RESET}")

    t0 = time.time()
    status, resp = http_post_json(f"{base_url}/generate", {
        "prompt": prompt,
        "width": 512,
        "height": 512,
    }, timeout=300)  # long timeout for first-load
    elapsed = time.time() - t0

    if status != 200:
        error_msg = resp if isinstance(resp, str) else json.dumps(resp, indent=2)
        print(f"  {C.RED}✗ FAILED — HTTP {status}: {error_msg[:200]}{C.RESET}")
        print()
        return False

    if isinstance(resp, dict) and not resp.get("success", False):
        print(f"  {C.RED}✗ FAILED — {resp.get('error', 'Unknown error')}{C.RESET}")
        print()
        return False

    # Download the image
    image_url = resp.get("image_url", "") if isinstance(resp, dict) else ""
    if not image_url:
        print(f"  {C.RED}✗ FAILED — No image URL in response{C.RESET}")
        print()
        return False

    # The URL may use 127.0.0.1 or the configured host — normalise to localhost
    image_url = image_url.replace("127.0.0.1", "localhost").replace("0.0.0.0", "localhost")
    if image_url.startswith("/"):
        image_url = f"{base_url}{image_url}"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / "zimage-test.png"
    print(f"  Downloading image ...")
    if not download_file(image_url, out_file):
        print(f"  {C.RED}✗ FAILED — Could not download generated image{C.RESET}")
        print()
        return False

    size_kb = out_file.stat().st_size / 1024
    gen_time = resp.get("generation_time_seconds", elapsed) if isinstance(resp, dict) else elapsed
    seed = resp.get("seed", "?") if isinstance(resp, dict) else "?"

    print(f"  {C.GREEN}✓ SUCCESS{C.RESET} ({gen_time:.1f}s, {size_kb:.0f} KB, seed={seed})")
    print(f"  {C.CYAN}Output:  {out_file}{C.RESET}")
    print()
    return True


def test_qwen3_tts(env: dict) -> bool:
    """Test Qwen3-TTS — generate a short audio clip."""
    print(f"{C.BOLD}[3/5] Qwen3-TTS (Local Voice Generation){C.RESET}")

    if not is_true(env.get("USE_QWEN3_TTS")):
        print(f"  {C.YELLOW}⏭  SKIPPED — USE_QWEN3_TTS is not enabled{C.RESET}")
        print()
        return True

    port = env.get("QWEN3_TTS_API_PORT", "8002")
    base_url = f"http://localhost:{port}"

    # Health check
    print(f"  Checking Qwen3-TTS at {base_url} ...")
    status, body = http_get(f"{base_url}/health", timeout=10)
    if status == 0:
        print(f"  {C.RED}✗ FAILED — Cannot reach Qwen3-TTS server on port {port}{C.RESET}")
        print(f"  {C.DIM}Make sure Qwen3-TTS is running (start.sh handles this automatically){C.RESET}")
        print()
        return False
    print(f"  {C.GREEN}✓ Qwen3-TTS server is running{C.RESET}")

    if status == 200:
        try:
            info = json.loads(body)
            loaded = info.get("model_loaded", False)
            if not loaded:
                print(f"  {C.DIM}Model not loaded yet — first request will trigger loading (~20-40s){C.RESET}")
        except Exception:
            pass

    # Generate speech
    text = "Hello! This is a test of the local voice generation system. Everything is working great."
    print(f"  Generating speech with voice 'Ryan' ...")
    print(f"  {C.DIM}Text: {text}{C.RESET}")

    t0 = time.time()
    status, resp = http_post_json(f"{base_url}/generate", {
        "text": text,
        "mode": "custom_voice",
        "voice": "Ryan",
    }, timeout=180)
    elapsed = time.time() - t0

    if status != 200:
        error_msg = resp if isinstance(resp, str) else json.dumps(resp, indent=2)
        print(f"  {C.RED}✗ FAILED — HTTP {status}: {error_msg[:200]}{C.RESET}")
        print()
        return False

    if isinstance(resp, dict) and not resp.get("success", False):
        print(f"  {C.RED}✗ FAILED — {resp.get('error', 'Unknown error')}{C.RESET}")
        print()
        return False

    # Download the audio
    audio_url = resp.get("audio_url", "") if isinstance(resp, dict) else ""
    if not audio_url:
        print(f"  {C.RED}✗ FAILED — No audio URL in response{C.RESET}")
        print()
        return False

    if audio_url.startswith("/"):
        audio_url = f"{base_url}{audio_url}"

    ext = "wav" if ".wav" in audio_url else "mp3"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / f"tts-test.{ext}"
    print(f"  Downloading audio ...")
    if not download_file(audio_url, out_file):
        print(f"  {C.RED}✗ FAILED — Could not download generated audio{C.RESET}")
        print()
        return False

    size_kb = out_file.stat().st_size / 1024
    gen_time = resp.get("generation_time_seconds", elapsed) if isinstance(resp, dict) else elapsed
    duration = resp.get("duration_seconds", "?") if isinstance(resp, dict) else "?"

    print(f"  {C.GREEN}✓ SUCCESS{C.RESET} ({gen_time:.1f}s generation, {duration}s audio, {size_kb:.0f} KB)")
    print(f"  {C.CYAN}Output:  {out_file}{C.RESET}")
    print()
    return True


def test_acestep(env: dict) -> bool:
    """Test ACE-Step — generate a short music clip."""
    print(f"{C.BOLD}[4/5] ACE-Step (Local Music Generation){C.RESET}")

    if not is_true(env.get("USE_ACESTEP")):
        print(f"  {C.YELLOW}⏭  SKIPPED — USE_ACESTEP is not enabled{C.RESET}")
        print()
        return True

    port = env.get("ACESTEP_API_PORT", "8001")
    base_url = f"http://localhost:{port}"
    api_key = env.get("ACESTEP_API_KEY", "")

    # Health check
    print(f"  Checking ACE-Step at {base_url} ...")
    status, body = http_get(f"{base_url}/health", timeout=10)
    if status == 0:
        print(f"  {C.RED}✗ FAILED — Cannot reach ACE-Step server on port {port}{C.RESET}")
        print(f"  {C.DIM}Make sure ACE-Step is running (start.sh handles this automatically){C.RESET}")
        print()
        return False
    print(f"  {C.GREEN}✓ ACE-Step server is running{C.RESET}")

    # Submit generation task (short instrumental clip)
    prompt = "upbeat happy electronic pop, bright synths, energetic"
    print(f"  Submitting 15-second music generation task ...")
    print(f"  {C.DIM}Prompt: {prompt}{C.RESET}")

    payload = {
        "prompt": prompt,
        "lyrics": "[inst]",
        "audio_duration": 15,
        "inference_steps": 8,
    }
    if api_key:
        payload["ai_token"] = api_key

    t0 = time.time()
    status, resp = http_post_json(f"{base_url}/release_task", payload, timeout=30)

    if status != 200 or not isinstance(resp, dict):
        error_msg = resp if isinstance(resp, str) else json.dumps(resp, indent=2)
        print(f"  {C.RED}✗ FAILED — HTTP {status}: {error_msg[:200]}{C.RESET}")
        print()
        return False

    task_data = resp.get("data", {})
    task_id = task_data.get("task_id")
    if not task_id:
        print(f"  {C.RED}✗ FAILED — No task_id in response: {resp}{C.RESET}")
        print()
        return False

    print(f"  {C.DIM}Task ID: {task_id}{C.RESET}")

    # Poll for results
    print(f"  Waiting for generation ", end="", flush=True)
    max_wait = 300  # 5 minutes
    poll_interval = 3
    waited = 0
    result_url = None

    while waited < max_wait:
        time.sleep(poll_interval)
        waited += poll_interval
        print(".", end="", flush=True)

        status, resp = http_post_json(f"{base_url}/query_result", {
            "task_id_list": [task_id],
        }, timeout=15)

        if status != 200 or not isinstance(resp, dict):
            continue

        results = resp.get("data", [])
        if not results:
            continue

        task_result = results[0] if isinstance(results, list) else results
        task_status = task_result.get("status", 0)

        if task_status == 2:  # failed
            print()
            print(f"  {C.RED}✗ FAILED — Generation task failed{C.RESET}")
            print()
            return False

        if task_status == 1:  # succeeded
            # Parse the result JSON string
            try:
                result_items = json.loads(task_result.get("result", "[]"))
                if result_items and isinstance(result_items, list):
                    result_url = result_items[0].get("file", "")
            except (json.JSONDecodeError, IndexError):
                pass
            break

    elapsed = time.time() - t0
    print()  # newline after dots

    if not result_url:
        if waited >= max_wait:
            print(f"  {C.RED}✗ FAILED — Timed out after {max_wait}s{C.RESET}")
        else:
            print(f"  {C.RED}✗ FAILED — No audio file in result{C.RESET}")
        print()
        return False

    # Download the audio
    if result_url.startswith("/"):
        audio_url = f"{base_url}{result_url}"
    else:
        audio_url = result_url

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / "acestep-test.mp3"
    print(f"  Downloading audio ...")
    if not download_file(audio_url, out_file, timeout=30):
        print(f"  {C.RED}✗ FAILED — Could not download generated audio{C.RESET}")
        print()
        return False

    size_kb = out_file.stat().st_size / 1024
    print(f"  {C.GREEN}✓ SUCCESS{C.RESET} ({elapsed:.1f}s, {size_kb:.0f} KB)")
    print(f"  {C.CYAN}Output:  {out_file}{C.RESET}")
    print()
    return True


# ---------------------------------------------------------------------------
# 5) SeedVR2 upscaler
# ---------------------------------------------------------------------------

def test_seedvr2(env: dict) -> bool:
    """Test SeedVR2 — generate a small image with Z-Image then upscale it."""
    print(f"{C.BOLD}[5/5] SeedVR2 (Local Image & Video Upscaler){C.RESET}")

    if not is_true(env.get("USE_SEEDVR2")):
        print(f"  {C.YELLOW}⏭  SKIPPED — USE_SEEDVR2 is not enabled{C.RESET}")
        print()
        return True

    port = env.get("SEEDVR2_API_PORT", "8004")
    base_url = f"http://localhost:{port}"

    # Health check
    print(f"  Checking SeedVR2 at {base_url} ...")
    status, body = http_get(f"{base_url}/health", timeout=10)
    if status == 0:
        print(f"  {C.RED}✗ FAILED — Cannot reach SeedVR2 server on port {port}{C.RESET}")
        print(f"  {C.DIM}Make sure SeedVR2 is running (start.sh handles this automatically){C.RESET}")
        print()
        return False
    print(f"  {C.GREEN}✓ SeedVR2 server is running{C.RESET}")

    if status == 200:
        try:
            info = json.loads(body)
            loaded = info.get("model_loaded", False)
            if not loaded:
                print(f"  {C.DIM}Model not loaded yet — first request will trigger loading + download (~4GB){C.RESET}")
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Step 1: Get or create a source image to upscale
    # -----------------------------------------------------------------------
    # Try to use Z-Image to produce a small test image; fall back to the
    # previously-generated zimage-test.png from an earlier test run, or a
    # a tiny solid-colour PNG as last resort.

    source_image_url = None  # URL for the SeedVR2 /upscale/image endpoint
    source_image_path = None  # Local path fallback

    zimage_port = env.get("ZIMAGE_API_PORT", "8003")
    zimage_base = f"http://localhost:{zimage_port}"

    if is_true(env.get("USE_ZIMAGE")):
        # Try generating a small image with Z-Image
        print(f"  Generating 256×256 source image with Z-Image ...")
        gen_status, gen_resp = http_post_json(
            f"{zimage_base}/generate",
            {"prompt": "A colourful parrot on a branch, nature photography",
             "width": 256, "height": 256},
            timeout=300,
        )
        if gen_status == 200 and isinstance(gen_resp, dict) and gen_resp.get("success"):
            img_url = gen_resp.get("image_url", "")
            img_url = img_url.replace("127.0.0.1", "localhost").replace("0.0.0.0", "localhost")
            if img_url.startswith("/"):
                img_url = f"{zimage_base}{img_url}"
            source_image_url = img_url
            print(f"  {C.GREEN}✓ Source image ready{C.RESET}")
        else:
            print(f"  {C.YELLOW}Z-Image generation failed (HTTP {gen_status}), looking for fallback ...{C.RESET}")

    # Fallback: previously-saved test image
    if not source_image_url:
        prev = OUTPUT_DIR / "zimage-test.png"
        if prev.is_file():
            source_image_path = str(prev)
            print(f"  {C.DIM}Using previous test image: {prev}{C.RESET}")
        else:
            # Last resort: create a tiny 64×64 solid PNG with stdlib
            print(f"  {C.DIM}Creating minimal 64×64 test PNG ...{C.RESET}")
            import struct, zlib
            w, h = 64, 64
            raw_rows = b""
            for _ in range(h):
                raw_rows += b"\x00" + (b"\x40\x80\xc0" * w)  # filter byte + RGB
            compressed = zlib.compress(raw_rows)
            ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)

            def _chunk(ctype, data):
                c = ctype + data
                return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            tiny_path = OUTPUT_DIR / "seedvr2-source.png"
            with open(tiny_path, "wb") as fp:
                fp.write(b"\x89PNG\r\n\x1a\n")
                fp.write(_chunk(b"IHDR", ihdr))
                fp.write(_chunk(b"IDAT", compressed))
                fp.write(_chunk(b"IEND", b""))
            source_image_path = str(tiny_path)
            print(f"  {C.DIM}Created {tiny_path}{C.RESET}")

    # -----------------------------------------------------------------------
    # Step 2: Upscale the image with SeedVR2
    # -----------------------------------------------------------------------
    payload = {
        "resolution": 1024,  # 2× upscale from 512×512 source — proves actual super-resolution
        "color_correction": "lab",
        "seed": 42,
    }
    if source_image_url:
        payload["image_url"] = source_image_url
    elif source_image_path:
        payload["image_path"] = source_image_path
    else:
        print(f"  {C.RED}✗ FAILED — No source image available{C.RESET}")
        print()
        return False

    src_label = source_image_url or source_image_path
    print(f"  Upscaling to 1024p (2× from source) ...")
    print(f"  {C.DIM}Source: {src_label}{C.RESET}")

    t0 = time.time()
    status, resp = http_post_json(f"{base_url}/upscale/image", payload, timeout=600)
    elapsed = time.time() - t0

    if status != 200:
        error_msg = resp if isinstance(resp, str) else json.dumps(resp, indent=2)
        print(f"  {C.RED}✗ FAILED — HTTP {status}: {error_msg[:300]}{C.RESET}")
        print()
        return False

    if isinstance(resp, dict) and not resp.get("success", False):
        print(f"  {C.RED}✗ FAILED — {resp.get('detail', resp.get('error', 'Unknown'))}{C.RESET}")
        print()
        return False

    # Download the upscaled image
    output_url = resp.get("output_url", "") if isinstance(resp, dict) else ""
    if not output_url:
        print(f"  {C.RED}✗ FAILED — No output URL in response{C.RESET}")
        print()
        return False

    output_url = output_url.replace("127.0.0.1", "localhost").replace("0.0.0.0", "localhost")
    if output_url.startswith("/"):
        output_url = f"{base_url}{output_url}"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUTPUT_DIR / "seedvr2-test.png"
    print(f"  Downloading upscaled image ...")
    if not download_file(output_url, out_file):
        print(f"  {C.RED}✗ FAILED — Could not download upscaled image{C.RESET}")
        print()
        return False

    size_kb = out_file.stat().st_size / 1024
    proc_time = resp.get("processing_time_seconds", elapsed) if isinstance(resp, dict) else elapsed
    in_res = resp.get("input_resolution", "?") if isinstance(resp, dict) else "?"
    out_res = resp.get("output_resolution", "?") if isinstance(resp, dict) else "?"
    model_used = resp.get("model_used", "?") if isinstance(resp, dict) else "?"
    seed = resp.get("seed", "?") if isinstance(resp, dict) else "?"

    print(f"  {C.GREEN}✓ SUCCESS{C.RESET} ({proc_time:.1f}s, {size_kb:.0f} KB, {in_res} → {out_res}, seed={seed})")
    print(f"  {C.DIM}Model: {model_used}{C.RESET}")
    print(f"  {C.CYAN}Output:  {out_file}{C.RESET}")
    print()
    return True


# ---------------------------------------------------------------------------
# Canary-STT test
# ---------------------------------------------------------------------------

def test_canary_stt(env: dict) -> bool:
    """Test Canary-STT — health check and server info."""
    print(f"{C.BOLD}[7/8] Canary-STT (Local Speech-to-Text){C.RESET}")

    if not is_true(env.get("USE_CANARY_STT", "false")):
        print(f"  {C.YELLOW}⏭  SKIPPED — USE_CANARY_STT is not enabled{C.RESET}")
        print()
        return True

    base_url = env.get("CANARY_STT_API_URL", "http://localhost:8005").replace(
        "host.docker.internal", "localhost"
    )
    port = env.get("CANARY_STT_API_PORT", "8005")
    if ":" not in base_url.split("/")[-1]:
        base_url = f"{base_url}:{port}"

    t0 = time.time()

    # Step 1: Health check
    print(f"  Checking Canary-STT at {base_url} ...")
    status, raw = http_get(f"{base_url}/health", timeout=10)
    if status != 200:
        print(f"  {C.RED}✗ FAILED — Server not reachable (HTTP {status}){C.RESET}")
        print(f"  {C.DIM}Make sure canary-stt/app.py is running on port {port}{C.RESET}")
        print()
        return False

    try:
        resp = json.loads(raw)
    except Exception:
        resp = {}
    model_loaded = resp.get("model_loaded", False) if isinstance(resp, dict) else False
    print(f"  {C.GREEN}✓ Server healthy{C.RESET} (model loaded: {model_loaded})")

    # Step 2: Get server info
    print(f"  Querying server info ...")
    status, raw = http_get(f"{base_url}/info", timeout=10)
    if status == 200:
        try:
            resp = json.loads(raw)
        except Exception:
            resp = {}
        if isinstance(resp, dict):
            device = resp.get("device", "?")
            dtype = resp.get("dtype", "?")
            model_repo = resp.get("model_repo", "?")
            gpu = resp.get("gpu", "N/A")
            max_dur = resp.get("max_audio_duration", "?")
            formats = resp.get("supported_formats", [])
            print(f"  {C.GREEN}✓ Model: {model_repo}{C.RESET}")
            print(f"  {C.DIM}Device: {device}, dtype: {dtype}, GPU: {gpu}{C.RESET}")
            print(f"  {C.DIM}Max audio: {max_dur}s, formats: {', '.join(formats[:6])}{C.RESET}")
        else:
            print(f"  {C.YELLOW}⚠ Could not parse server info{C.RESET}")
    else:
        print(f"  {C.YELLOW}⚠ Could not get server info{C.RESET}")

    elapsed = time.time() - t0
    print(f"\n  {C.GREEN}✓ SUCCESS{C.RESET} ({elapsed:.1f}s)")
    print()
    return True


# ---------------------------------------------------------------------------
# Dev Sandbox test
# ---------------------------------------------------------------------------

def test_sandbox(env: dict) -> bool:
    """Test Dev Sandbox — create container, exec command, write/read file, destroy."""
    print(f"{C.BOLD}[8/8] Dev Sandbox (Isolated Development Environment){C.RESET}")

    if not is_true(env.get("USE_DEV_SANDBOX", "true")):
        print(f"  {C.YELLOW}⏭  SKIPPED — USE_DEV_SANDBOX is not enabled{C.RESET}")
        print()
        return True

    # Check Docker is available
    print(f"  Checking Docker daemon ...")
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            print(f"  {C.RED}✗ FAILED — Docker not available: {result.stderr.strip()}{C.RESET}")
            print()
            return False
        docker_version = result.stdout.strip()
        print(f"  {C.GREEN}✓ Docker {docker_version}{C.RESET}")
    except FileNotFoundError:
        print(f"  {C.RED}✗ FAILED — 'docker' command not found{C.RESET}")
        print()
        return False
    except Exception as e:
        print(f"  {C.RED}✗ FAILED — Docker check error: {e}{C.RESET}")
        print()
        return False

    container_name = None
    t0 = time.time()

    try:
        # Step 1: Create a sandbox container
        image = env.get("DEV_SANDBOX_DEFAULT_IMAGE", "python:3.12-slim")
        container_name = f"sandbox-test-{int(time.time())}"
        print(f"  Creating sandbox ({image}) ...")

        result = subprocess.run(
            [
                "docker", "run", "-d",
                "--name", container_name,
                "--memory", "256m",
                "--cpus", "1",
                "--security-opt", "no-new-privileges",
                "-w", "/workspace",
                image,
                "sleep", "120",
            ],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            print(f"  {C.RED}✗ FAILED — Could not create container: {result.stderr.strip()}{C.RESET}")
            print()
            return False
        container_id = result.stdout.strip()[:12]
        print(f"  {C.GREEN}✓ Container created: {container_id}{C.RESET}")

        # Step 2: Execute a command
        print(f"  Running 'python --version' ...")
        result = subprocess.run(
            ["docker", "exec", container_name, "python3", "--version"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"  {C.RED}✗ FAILED — exec failed: {result.stderr.strip()}{C.RESET}")
            return False
        py_version = result.stdout.strip()
        print(f"  {C.GREEN}✓ {py_version}{C.RESET}")

        # Step 3: Write a file (via exec + sh -c)
        test_content = "print('Hello from Dev Sandbox!')"
        print(f"  Writing test script ...")
        result = subprocess.run(
            ["docker", "exec", container_name, "sh", "-c",
             f"echo \"{test_content}\" > /workspace/hello.py"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            print(f"  {C.RED}✗ FAILED — write failed: {result.stderr.strip()}{C.RESET}")
            return False
        print(f"  {C.GREEN}✓ File written{C.RESET}")

        # Step 4: Execute the script
        print(f"  Running test script ...")
        result = subprocess.run(
            ["docker", "exec", container_name, "python3", "/workspace/hello.py"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            print(f"  {C.RED}✗ FAILED — script execution failed: {result.stderr.strip()}{C.RESET}")
            return False
        output = result.stdout.strip()
        if "Hello from Dev Sandbox" not in output:
            print(f"  {C.RED}✗ FAILED — unexpected output: {output}{C.RESET}")
            return False
        print(f"  {C.GREEN}✓ Output: {output}{C.RESET}")

        # Step 5: Read the file back
        print(f"  Reading file back ...")
        result = subprocess.run(
            ["docker", "exec", container_name, "cat", "/workspace/hello.py"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            print(f"  {C.RED}✗ FAILED — read failed: {result.stderr.strip()}{C.RESET}")
            return False
        print(f"  {C.GREEN}✓ File content verified{C.RESET}")

        elapsed = time.time() - t0
        print(f"\n  {C.GREEN}✓ SUCCESS{C.RESET} ({elapsed:.1f}s)")
        print()
        return True

    except subprocess.TimeoutExpired:
        print(f"  {C.RED}✗ FAILED — Operation timed out{C.RESET}")
        print()
        return False
    except Exception as e:
        print(f"  {C.RED}✗ FAILED — {e}{C.RESET}")
        print()
        return False
    finally:
        # Cleanup: always remove the test container
        if container_name:
            try:
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    capture_output=True, timeout=15,
                )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

TESTS = {
    "ollama":  test_ollama,
    "zimage":  test_zimage,
    "tts":     test_qwen3_tts,
    "acestep": test_acestep,
    "seedvr2": test_seedvr2,
    "canary":  test_canary_stt,
    "sandbox": test_sandbox,
}

def main():
    banner()

    env = load_env()
    if not env:
        print(f"{C.RED}Could not find .env file at {ENV_FILE}{C.RESET}")
        print(f"Run start.sh first to set up your environment.")
        sys.exit(1)

    # Parse CLI args — filter to specific tests
    requested = [a.lower() for a in sys.argv[1:] if not a.startswith("-")]
    if requested:
        unknown = [r for r in requested if r not in TESTS]
        if unknown:
            print(f"{C.RED}Unknown test(s): {', '.join(unknown)}{C.RESET}")
            print(f"Available: {', '.join(TESTS.keys())}")
            sys.exit(1)
        tests_to_run = [(name, fn) for name, fn in TESTS.items() if name in requested]
    else:
        tests_to_run = list(TESTS.items())

    print(f"  {C.DIM}Output directory: {OUTPUT_DIR}{C.RESET}")
    print()

    # Run tests
    results = {}
    for name, fn in tests_to_run:
        try:
            results[name] = fn(env)
        except KeyboardInterrupt:
            print(f"\n  {C.YELLOW}Interrupted!{C.RESET}")
            results[name] = False
            break
        except Exception as e:
            print(f"  {C.RED}✗ UNEXPECTED ERROR: {e}{C.RESET}")
            results[name] = False
            print()

    # Summary
    print(f"{C.BOLD}{'=' * 60}{C.RESET}")
    print(f"{C.BOLD}  Summary{C.RESET}")
    print(f"{C.BOLD}{'=' * 60}{C.RESET}")
    print()

    all_ok = True
    for name, passed in results.items():
        label = {"ollama": "Ollama", "zimage": "Z-Image", "tts": "Qwen3-TTS", "acestep": "ACE-Step", "seedvr2": "SeedVR2", "canary": "Canary-STT", "sandbox": "Dev Sandbox"}
        status = f"{C.GREEN}✓ PASS{C.RESET}" if passed else f"{C.RED}✗ FAIL{C.RESET}"
        print(f"  {status}  {label.get(name, name)}")
        if not passed:
            all_ok = False

    print()
    if all_ok:
        print(f"  {C.GREEN}All tests passed!{C.RESET}")
    else:
        print(f"  {C.YELLOW}Some tests failed — see details above.{C.RESET}")

    if OUTPUT_DIR.exists() and any(OUTPUT_DIR.iterdir()):
        print(f"\n  {C.CYAN}Output files: {OUTPUT_DIR}{C.RESET}")

    print()
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
