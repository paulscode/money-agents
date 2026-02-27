"""
Media Toolkit Service — Manages the Media Toolkit FFmpeg-based composition server.

This service handles:
- Setting up the Media Toolkit venv (pip install ffmpeg-python etc.)
- Starting/stopping the Media Toolkit server
- Health checks and status monitoring
- Dispatching media operations (probe, extract_audio, combine, mix, etc.)

CPU-only — no GPU required.  Port 8008.
"""

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Media Toolkit project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent  # /home/…/money-agents
MEDIA_TOOLKIT_DIR = Path(os.environ.get("MEDIA_TOOLKIT_DIR", str(PROJECT_ROOT / "media-toolkit")))


class MediaToolkitError(Exception):
    """Exception for Media Toolkit related errors."""
    pass


class MediaToolkitService:
    """Service for managing the Media Toolkit local FFmpeg composition server."""

    def __init__(self):
        self.api_url = settings.media_toolkit_api_url
        self.api_port = settings.media_toolkit_api_port
        self.auto_start = settings.media_toolkit_auto_start
        self._process: Optional[subprocess.Popen] = None

    @property
    def base_url(self) -> str:
        """Get the base URL for Media Toolkit API."""
        url = self.api_url.rstrip("/")
        if ":" in url.split("/")[-1]:
            return url
        return f"{url}:{self.api_port}"

    @property
    def headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        # SGA-M7: Use .get_secret_value() for SecretStr field
        gpu_key = settings.gpu_service_api_key.get_secret_value()
        if gpu_key:
            h["X-API-Key"] = gpu_key
        return h

    # =========================================================================
    # Installation & Setup
    # =========================================================================

    async def is_installed(self) -> bool:
        """Check if Media Toolkit venv is set up and ready."""
        venv_dir = MEDIA_TOOLKIT_DIR / ".venv"
        app_file = MEDIA_TOOLKIT_DIR / "app.py"
        return venv_dir.exists() and app_file.exists()

    async def install(self) -> bool:
        """Set up Media Toolkit venv and install dependencies."""
        if await self.is_installed():
            logger.info("Media Toolkit is already installed")
            return True

        if not (MEDIA_TOOLKIT_DIR / "app.py").exists():
            logger.error(f"Media Toolkit app.py not found at {MEDIA_TOOLKIT_DIR}")
            return False

        logger.info(f"Setting up Media Toolkit at {MEDIA_TOOLKIT_DIR}...")

        try:
            venv_dir = MEDIA_TOOLKIT_DIR / ".venv"
            if not venv_dir.exists():
                logger.info("Creating Python venv...")
                result = subprocess.run(
                    [sys.executable, "-m", "venv", str(venv_dir)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
                if result.returncode != 0:
                    logger.error(f"venv creation failed: {result.stderr}")
                    return False

            if sys.platform == "win32":
                pip_path = venv_dir / "Scripts" / "pip.exe"
            else:
                pip_path = venv_dir / "bin" / "pip"

            requirements_file = MEDIA_TOOLKIT_DIR / "requirements.txt"
            if requirements_file.exists():
                logger.info("Installing Media Toolkit dependencies...")
                result = subprocess.run(
                    [str(pip_path), "install", "-r", str(requirements_file)],
                    cwd=MEDIA_TOOLKIT_DIR,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=300,
                )
                if result.returncode != 0:
                    logger.error(f"pip install requirements failed: {result.stderr}")
                    return False

            logger.info("Media Toolkit installed successfully")
            return True

        except subprocess.TimeoutExpired:
            logger.error("Media Toolkit installation timed out")
            return False
        except Exception as e:
            logger.error(f"Media Toolkit installation failed: {e}")
            return False

    # =========================================================================
    # Server Management
    # =========================================================================

    async def start(self) -> bool:
        """Start the Media Toolkit server."""
        if not await self.is_installed():
            logger.info("Media Toolkit not installed, installing now...")
            if not await self.install():
                return False

        if await self.health_check():
            logger.info("Media Toolkit server is already running")
            return True

        logger.info(f"Starting Media Toolkit server on port {self.api_port}...")

        try:
            venv_dir = MEDIA_TOOLKIT_DIR / ".venv"
            if sys.platform == "win32":
                venv_python = venv_dir / "Scripts" / "python.exe"
            else:
                venv_python = venv_dir / "bin" / "python"

            cmd = [
                str(venv_python), "-m", "uvicorn",
                "app:app", "--host", "0.0.0.0", "--port", str(self.api_port),
            ]

            logger.info(f"Running command: {' '.join(cmd)}")

            env = os.environ.copy()

            popen_kwargs = {
                "cwd": str(MEDIA_TOOLKIT_DIR),
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "env": env,
            }
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
                )
            else:
                popen_kwargs["start_new_session"] = True

            self._process = subprocess.Popen(cmd, **popen_kwargs)

            logger.info("Waiting for Media Toolkit server to start...")
            for attempt in range(30):  # 30s timeout — CPU-only, starts fast
                await asyncio.sleep(1)
                if await self.health_check():
                    logger.info("Media Toolkit server started successfully")
                    return True

                if self._process.poll() is not None:
                    stderr = self._process.stderr.read().decode() if self._process.stderr else ""
                    stdout = self._process.stdout.read().decode() if self._process.stdout else ""
                    logger.error(f"Media Toolkit server died.\nSTDOUT: {stdout}\nSTDERR: {stderr}")
                    return False

            logger.error("Media Toolkit server failed to start within timeout")
            return False

        except Exception as e:
            logger.error(f"Failed to start Media Toolkit server: {e}")
            return False

    async def stop(self) -> bool:
        """Stop the Media Toolkit server."""
        if self._process:
            logger.info("Stopping Media Toolkit server...")
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Media Toolkit server did not stop gracefully, killing...")
                self._process.kill()
            self._process = None
            logger.info("Media Toolkit server stopped")
            return True
        return False

    async def health_check(self) -> bool:
        """Check if Media Toolkit server is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/health", headers=self.headers)
                return response.status_code == 200
        except Exception:
            return False

    async def get_status(self) -> Dict[str, Any]:
        """Get detailed status of Media Toolkit server."""
        is_healthy = await self.health_check()

        status = {
            "enabled": settings.use_media_toolkit,
            "installed": await self.is_installed(),
            "running": is_healthy,
            "url": self.base_url,
            "install_path": str(MEDIA_TOOLKIT_DIR),
        }

        if is_healthy:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    response = await client.get(f"{self.base_url}/info")
                    if response.status_code == 200:
                        info = response.json()
                        status.update({
                            "ffmpeg_version": info.get("ffmpeg_version"),
                            "operations": info.get("operations"),
                        })
            except Exception:
                pass

        return status

    # =========================================================================
    # Media Operations
    # =========================================================================

    async def process(
        self,
        *,
        operation: str,
        params: Dict[str, Any],
        timeout: int = 600,
    ) -> Dict[str, Any]:
        """
        Execute a media operation.

        Args:
            operation: Operation name (probe, extract_audio, combine, etc.)
            params: Operation-specific parameters
            timeout: Maximum wait time in seconds

        Returns:
            Dict with operation results (output_file, metadata, etc.)
        """
        if not await self.health_check():
            raise MediaToolkitError("Media Toolkit server is not running")

        try:
            payload = {"operation": operation, **params}
            async with httpx.AsyncClient(timeout=timeout + 10) as client:
                response = await client.post(
                    f"{self.base_url}/process",
                    json=payload,
                )

                if response.status_code != 200:
                    error_text = response.text
                    logger.error(f"Media operation failed: {response.status_code} - {error_text}")
                    raise MediaToolkitError(
                        f"Operation '{operation}' failed: {response.status_code} - {error_text}"
                    )

                result = response.json()

                # Convert relative output URL to absolute
                output_file = result.get("output_file")
                if output_file and output_file.startswith("/"):
                    result["output_file"] = f"{self.base_url}{output_file}"

                return result

        except httpx.RequestError as e:
            raise MediaToolkitError(f"Request failed: {e}")


# Global service instance
_media_toolkit_service: Optional[MediaToolkitService] = None


def get_media_toolkit_service() -> MediaToolkitService:
    """Get the global Media Toolkit service instance."""
    global _media_toolkit_service
    if _media_toolkit_service is None:
        _media_toolkit_service = MediaToolkitService()
    return _media_toolkit_service


async def ensure_media_toolkit_running() -> bool:
    """Ensure Media Toolkit server is running if enabled."""
    if not settings.use_media_toolkit:
        return False

    if not settings.media_toolkit_auto_start:
        service = get_media_toolkit_service()
        return await service.health_check()

    service = get_media_toolkit_service()
    return await service.start()
