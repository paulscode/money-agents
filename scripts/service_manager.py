#!/usr/bin/env python3
"""
GPU Service Manager — lightweight HTTP agent running on the host.

Manages the lifecycle of GPU services (ACE-Step, LTX-2 Video, Z-Image, etc.)
that run as native host processes.  The backend (inside Docker) calls this
agent's REST API to start/stop/restart services that were previously shut down
for VRAM eviction.

Usage:
    python scripts/service_manager.py          # default port 9100
    python scripts/service_manager.py --port 9100

Endpoints:
    GET  /health                        — agent health check
    GET  /services                      — list all managed services + status
    GET  /services/{name}/status        — single service status
    POST /services/{name}/start         — start a stopped service
    POST /services/{name}/stop          — stop a running service
    POST /services/{name}/restart       — stop then start
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ACESTEP_DIR = PROJECT_ROOT / "acestep"
QWEN3_TTS_DIR = PROJECT_ROOT / "qwen3-tts"
ZIMAGE_DIR = PROJECT_ROOT / "z-image"
SEEDVR2_DIR = PROJECT_ROOT / "seedvr2-upscaler"
CANARY_STT_DIR = PROJECT_ROOT / "canary-stt"
AUDIOSR_DIR = PROJECT_ROOT / "audiosr"
MEDIA_TOOLKIT_DIR = PROJECT_ROOT / "media-toolkit"
LTX_VIDEO_DIR = PROJECT_ROOT / "ltx-video"

logger = logging.getLogger("service-manager")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_venv_python(service_dir: Path) -> str:
    """Return the path to the venv python executable."""
    venv = service_dir / ".venv"
    py = venv / "bin" / "python"
    if py.exists():
        return str(py)
    # Windows fallback
    py_win = venv / "Scripts" / "python.exe"
    if py_win.exists():
        return str(py_win)
    raise FileNotFoundError(f"No Python found in {venv}")


def _get_uv_env() -> dict:
    """Get env dict with uv on PATH (matches platform_utils.get_uv_env)."""
    env = os.environ.copy()
    home = os.environ.get("HOME", "")
    extra = [
        os.path.join(home, ".local", "bin"),
        os.path.join(home, ".cargo", "bin"),
    ]
    env["PATH"] = ":".join(extra + [env.get("PATH", "")])
    return env


def _port_is_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """Check if a TCP port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def _read_env_file() -> Dict[str, str]:
    """Read the project .env file into a dict."""
    env_file = PROJECT_ROOT / ".env"
    values: Dict[str, str] = {}
    if not env_file.exists():
        return values
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, val = line.partition("=")
            # Strip quotes
            val = val.strip().strip("'\"")
            values[key.strip()] = val
    return values


# ---------------------------------------------------------------------------
# Service definitions
# ---------------------------------------------------------------------------

class ServiceDef:
    """Static definition of how to start a GPU service."""

    def __init__(
        self,
        name: str,
        display_name: str,
        port_env: str,
        default_port: int,
        cwd: Path,
        build_cmd: "callable",
        build_env: "callable | None" = None,
        startup_timeout: int = 120,
    ):
        self.name = name
        self.display_name = display_name
        self.port_env = port_env
        self.default_port = default_port
        self.cwd = cwd
        self._build_cmd = build_cmd
        self._build_env = build_env
        self.startup_timeout = startup_timeout

    def get_port(self, env_values: Dict[str, str]) -> int:
        return int(env_values.get(self.port_env, str(self.default_port)))

    def get_cmd(self, env_values: Dict[str, str]) -> List[str]:
        return self._build_cmd(env_values)

    def get_env(self, env_values: Dict[str, str]) -> Optional[dict]:
        if self._build_env:
            return self._build_env(env_values)
        return None

    def is_installed(self) -> bool:
        return self.cwd.exists() and (self.cwd / ".venv").exists()


def _build_service_defs() -> Dict[str, ServiceDef]:
    """Build the catalogue of restartable GPU services."""

    def _acestep_cmd(env: Dict[str, str]) -> List[str]:
        port = env.get("ACESTEP_API_PORT", "8001")
        cmd = ["uv", "run", "acestep-api", "--host", "0.0.0.0", "--port", port]
        src = env.get("ACESTEP_DOWNLOAD_SOURCE", "auto")
        if src and src != "auto":
            cmd.extend(["--download-source", src])
        api_key = env.get("ACESTEP_API_KEY", "")
        if api_key:
            cmd.extend(["--api-key", api_key])
        return cmd

    def _acestep_env(_env: Dict[str, str]) -> dict:
        e = _get_uv_env()
        # Load both turbo and base DiT models so users can select per-request
        e.setdefault("ACESTEP_CONFIG_PATH", "acestep-v15-turbo")
        e.setdefault("ACESTEP_CONFIG_PATH2", "acestep-v15-base")
        return e

    def _uvicorn_cmd(service_dir: Path, env_key: str, default_port: str):
        def _inner(env: Dict[str, str]) -> List[str]:
            port = env.get(env_key, default_port)
            python = _get_venv_python(service_dir)
            return [python, "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", port]
        return _inner

    def _ltx_env(env: Dict[str, str]) -> dict:
        e = os.environ.copy()
        e["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
        gpu = env.get("GPU_LTX_VIDEO", "")
        if gpu:
            e["CUDA_VISIBLE_DEVICES"] = gpu
        return e

    return {
        "acestep": ServiceDef(
            name="acestep",
            display_name="ACE-Step",
            port_env="ACESTEP_API_PORT",
            default_port=8001,
            cwd=ACESTEP_DIR,
            build_cmd=_acestep_cmd,
            build_env=_acestep_env,
            startup_timeout=150,
        ),
        "qwen3-tts": ServiceDef(
            name="qwen3-tts",
            display_name="Qwen3-TTS",
            port_env="QWEN3_TTS_API_PORT",
            default_port=8002,
            cwd=QWEN3_TTS_DIR,
            build_cmd=_uvicorn_cmd(QWEN3_TTS_DIR, "QWEN3_TTS_API_PORT", "8002"),
            startup_timeout=120,
        ),
        "zimage": ServiceDef(
            name="zimage",
            display_name="Z-Image",
            port_env="ZIMAGE_API_PORT",
            default_port=8003,
            cwd=ZIMAGE_DIR,
            build_cmd=_uvicorn_cmd(ZIMAGE_DIR, "ZIMAGE_API_PORT", "8003"),
            startup_timeout=180,
        ),
        "seedvr2": ServiceDef(
            name="seedvr2",
            display_name="SeedVR2 Upscaler",
            port_env="SEEDVR2_API_PORT",
            default_port=8004,
            cwd=SEEDVR2_DIR,
            build_cmd=_uvicorn_cmd(SEEDVR2_DIR, "SEEDVR2_API_PORT", "8004"),
            startup_timeout=180,
        ),
        "canary-stt": ServiceDef(
            name="canary-stt",
            display_name="Canary-STT",
            port_env="CANARY_STT_API_PORT",
            default_port=8005,
            cwd=CANARY_STT_DIR,
            build_cmd=_uvicorn_cmd(CANARY_STT_DIR, "CANARY_STT_API_PORT", "8005"),
            startup_timeout=300,
        ),
        "audiosr": ServiceDef(
            name="audiosr",
            display_name="AudioSR",
            port_env="AUDIOSR_API_PORT",
            default_port=8007,
            cwd=AUDIOSR_DIR,
            build_cmd=_uvicorn_cmd(AUDIOSR_DIR, "AUDIOSR_API_PORT", "8007"),
            startup_timeout=180,
        ),
        "media-toolkit": ServiceDef(
            name="media-toolkit",
            display_name="Media Toolkit",
            port_env="MEDIA_TOOLKIT_API_PORT",
            default_port=8008,
            cwd=MEDIA_TOOLKIT_DIR,
            build_cmd=_uvicorn_cmd(MEDIA_TOOLKIT_DIR, "MEDIA_TOOLKIT_API_PORT", "8008"),
            startup_timeout=30,
        ),
        "ltx-video": ServiceDef(
            name="ltx-video",
            display_name="LTX-2 Video",
            port_env="LTX_VIDEO_API_PORT",
            default_port=8006,
            cwd=LTX_VIDEO_DIR,
            build_cmd=_uvicorn_cmd(LTX_VIDEO_DIR, "LTX_VIDEO_API_PORT", "8006"),
            build_env=_ltx_env,
            startup_timeout=120,
        ),
    }


# ---------------------------------------------------------------------------
# Process tracker
# ---------------------------------------------------------------------------

class ProcessTracker:
    """Tracks child processes we've started."""

    def __init__(self):
        self._procs: Dict[str, subprocess.Popen] = {}

    def register(self, name: str, proc: subprocess.Popen):
        self._procs[name] = proc

    def is_tracked(self, name: str) -> bool:
        proc = self._procs.get(name)
        if proc is None:
            return False
        return proc.poll() is None  # Still alive?

    def stop(self, name: str) -> bool:
        proc = self._procs.pop(name, None)
        if proc is None:
            return False
        if proc.poll() is not None:
            return True  # Already dead
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        return True


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ServiceStatus(BaseModel):
    name: str
    display_name: str
    port: int
    running: bool
    installed: bool
    tracked: bool  # started by this manager


class ServiceActionResponse(BaseModel):
    name: str
    action: str
    success: bool
    message: str
    port: Optional[int] = None


class HealthResponse(BaseModel):
    status: str
    services_available: int
    pid: int


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(title="GPU Service Manager", version="1.0.0")

    # --- API-key authentication middleware ---
    # The service manager binds to 0.0.0.0 so Docker containers can reach
    # it via host.docker.internal.  When SERVICE_MANAGER_API_KEY is set,
    # all non-health endpoints require the key in an X-API-Key header.
    # SGA3-L6: Fail-closed — reject all requests when key is empty unless
    # SERVICE_MANAGER_AUTH_SKIP=true is explicitly set.
    import hmac as _hmac
    api_key = os.environ.get("SERVICE_MANAGER_API_KEY", "")
    auth_skip = os.environ.get("SERVICE_MANAGER_AUTH_SKIP", "").lower() == "true"

    @app.middleware("http")
    async def _require_api_key(request, call_next):
        # Health endpoint is always open for probes
        if request.url.path == "/health":
            return await call_next(request)
        if not api_key and not auth_skip:
            from starlette.responses import JSONResponse as _J
            return _J(
                {"detail": "SERVICE_MANAGER_API_KEY not configured. "
                 "Set it in .env or set SERVICE_MANAGER_AUTH_SKIP=true to disable auth."},
                status_code=403,
            )
        if api_key:
            provided = request.headers.get("X-API-Key", "")
            if not _hmac.compare_digest(provided, api_key):
                from starlette.responses import JSONResponse as _J
                return _J({"detail": "Invalid or missing API key"}, status_code=401)
        return await call_next(request)

    service_defs = _build_service_defs()
    app.state.service_defs = service_defs  # Expose for signal file watcher
    tracker = ProcessTracker()
    env_cache: Dict[str, str] = {}

    def _env() -> Dict[str, str]:
        if not env_cache:
            env_cache.update(_read_env_file())
        return env_cache

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    @app.get("/health", response_model=HealthResponse)
    async def health():
        return HealthResponse(
            status="ok",
            services_available=len(service_defs),
            pid=os.getpid(),
        )

    @app.get("/services", response_model=List[ServiceStatus])
    async def list_services():
        env = _env()
        result = []
        for sdef in service_defs.values():
            port = sdef.get_port(env)
            result.append(ServiceStatus(
                name=sdef.name,
                display_name=sdef.display_name,
                port=port,
                running=_port_is_open(port),
                installed=sdef.is_installed(),
                tracked=tracker.is_tracked(sdef.name),
            ))
        return result

    @app.get("/services/{name}/status", response_model=ServiceStatus)
    async def service_status(name: str):
        sdef = service_defs.get(name)
        if not sdef:
            raise HTTPException(status_code=404, detail=f"Unknown service: {name}")
        env = _env()
        port = sdef.get_port(env)
        return ServiceStatus(
            name=sdef.name,
            display_name=sdef.display_name,
            port=port,
            running=_port_is_open(port),
            installed=sdef.is_installed(),
            tracked=tracker.is_tracked(sdef.name),
        )

    @app.post("/services/{name}/start", response_model=ServiceActionResponse)
    async def start_service(name: str):
        sdef = service_defs.get(name)
        if not sdef:
            raise HTTPException(status_code=404, detail=f"Unknown service: {name}")

        env = _env()
        port = sdef.get_port(env)

        # Already running?
        if _port_is_open(port):
            return ServiceActionResponse(
                name=name, action="start", success=True,
                message=f"{sdef.display_name} already running on port {port}",
                port=port,
            )

        if not sdef.is_installed():
            return ServiceActionResponse(
                name=name, action="start", success=False,
                message=f"{sdef.display_name} is not installed",
            )

        cmd = sdef.get_cmd(env)
        proc_env = sdef.get_env(env)
        cwd = str(sdef.cwd)

        logger.info(f"Starting {sdef.display_name}: {' '.join(cmd)}  (cwd={cwd})")

        try:
            # Start detached process
            kwargs: Dict[str, Any] = {
                "cwd": cwd,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "start_new_session": True,
            }
            if proc_env:
                kwargs["env"] = proc_env

            proc = subprocess.Popen(cmd, **kwargs)
            tracker.register(name, proc)

            # Wait for port to open
            deadline = time.monotonic() + sdef.startup_timeout
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    return ServiceActionResponse(
                        name=name, action="start", success=False,
                        message=f"{sdef.display_name} process exited (code {proc.returncode})",
                    )
                if _port_is_open(port):
                    logger.info(f"{sdef.display_name} started on port {port} (pid {proc.pid})")
                    return ServiceActionResponse(
                        name=name, action="start", success=True,
                        message=f"{sdef.display_name} started on port {port}",
                        port=port,
                    )
                await asyncio.sleep(1)

            return ServiceActionResponse(
                name=name, action="start", success=False,
                message=f"{sdef.display_name} did not come up within {sdef.startup_timeout}s",
            )

        except Exception as e:
            logger.exception(f"Failed to start {sdef.display_name}")
            return ServiceActionResponse(
                name=name, action="start", success=False,
                message=str(e),
            )

    @app.post("/services/{name}/stop", response_model=ServiceActionResponse)
    async def stop_service(name: str):
        sdef = service_defs.get(name)
        if not sdef:
            raise HTTPException(status_code=404, detail=f"Unknown service: {name}")

        env = _env()
        port = sdef.get_port(env)

        if not _port_is_open(port):
            return ServiceActionResponse(
                name=name, action="stop", success=True,
                message=f"{sdef.display_name} is not running",
                port=port,
            )

        # Try graceful shutdown first
        import httpx
        base_url = f"http://127.0.0.1:{port}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(f"{base_url}/shutdown")
        except Exception:
            pass  # Connection reset expected

        # Wait for port to close
        for _ in range(15):
            await asyncio.sleep(1)
            if not _port_is_open(port):
                tracker.stop(name)
                return ServiceActionResponse(
                    name=name, action="stop", success=True,
                    message=f"{sdef.display_name} stopped",
                    port=port,
                )

        # If still alive, force kill tracked process
        if tracker.stop(name):
            await asyncio.sleep(2)
            if not _port_is_open(port):
                return ServiceActionResponse(
                    name=name, action="stop", success=True,
                    message=f"{sdef.display_name} force-killed",
                    port=port,
                )

        return ServiceActionResponse(
            name=name, action="stop", success=False,
            message=f"Could not stop {sdef.display_name} on port {port}",
            port=port,
        )

    @app.post("/services/{name}/restart", response_model=ServiceActionResponse)
    async def restart_service(name: str):
        stop_result = await stop_service(name)
        if not stop_result.success and "not running" not in stop_result.message.lower():
            return ServiceActionResponse(
                name=name, action="restart", success=False,
                message=f"Stop failed: {stop_result.message}",
            )
        # Small delay to ensure port fully released
        await asyncio.sleep(1)
        return await start_service(name)

    @app.post("/reload-env")
    async def reload_env():
        """Re-read .env file (call after config changes)."""
        env_cache.clear()
        env_cache.update(_read_env_file())
        return {"status": "ok", "keys_loaded": len(env_cache)}

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="GPU Service Manager")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("SERVICE_MANAGER_PORT", 9100)),
                        help="Port to listen on (default: SERVICE_MANAGER_PORT env or 9100)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (use 127.0.0.1 to restrict to localhost only)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [service-manager] %(levelname)s %(message)s",
    )

    import uvicorn
    app = create_app()

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def _start_service_sync(svc_def) -> dict:
    """Synchronously start a service (called from the watcher thread)."""
    env_values = _read_env_file()
    port = svc_def.get_port(env_values)

    if _port_is_open(port):
        return {"success": True, "status": "already_running", "port": port}

    if not svc_def.is_installed():
        return {"success": False, "error": f"{svc_def.display_name} is not installed"}

    cmd = svc_def.get_cmd(env_values)
    proc_env = svc_def.get_env(env_values)
    cwd = str(svc_def.cwd)

    logger = logging.getLogger("service-manager.watcher")
    logger.info(f"Starting {svc_def.display_name}: {' '.join(cmd)}  (cwd={cwd})")

    try:
        kwargs = {
            "cwd": cwd,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "start_new_session": True,
        }
        if proc_env:
            kwargs["env"] = proc_env

        proc = subprocess.Popen(cmd, **kwargs)

        for _ in range(svc_def.startup_timeout):
            time.sleep(1)
            if _port_is_open(port):
                logger.info(f"{svc_def.display_name} started on port {port} (pid {proc.pid})")
                return {"success": True, "status": "started", "port": port, "pid": proc.pid}
            if proc.poll() is not None:
                return {"success": False, "error": "Process died during startup"}

        return {"success": False, "error": f"Startup timeout ({svc_def.startup_timeout}s)"}

    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    main()
