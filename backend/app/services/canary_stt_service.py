"""
Canary-STT Service - Manages the Canary-Qwen speech-to-text server.

This service handles:
- Setting up Canary-STT with its own venv (pip install from NeMo git)
- Starting/stopping the Canary-STT server
- Health checks and status monitoring
- Speech-to-text transcription via the local API

Model: NVIDIA Canary-Qwen-2.5B (https://huggingface.co/nvidia/canary-qwen-2.5b)
"""

import asyncio
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Canary-STT project paths — standalone server in project root's canary-stt/ directory
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent  # project root
CANARY_STT_DIR = Path(os.environ.get("CANARY_STT_DIR", str(PROJECT_ROOT / "canary-stt")))


class CanarySTTError(Exception):
    """Exception for Canary-STT related errors."""
    pass


class CanarySTTService:
    """Service for managing Canary-STT local speech-to-text."""

    def __init__(self):
        self.api_url = settings.canary_stt_api_url
        self.api_port = settings.canary_stt_api_port
        self.auto_start = settings.canary_stt_auto_start
        self.idle_timeout = settings.canary_stt_idle_timeout
        self._process: Optional[subprocess.Popen] = None

    @property
    def base_url(self) -> str:
        """Get the base URL for Canary-STT API."""
        url = self.api_url.rstrip("/")
        if ":" in url.split("/")[-1]:
            return url
        return f"{url}:{self.api_port}"

    @property
    def headers(self) -> Dict[str, str]:
        """Get headers for API requests."""
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
        """Check if Canary-STT venv is set up and ready."""
        venv_dir = CANARY_STT_DIR / ".venv"
        app_file = CANARY_STT_DIR / "app.py"
        return venv_dir.exists() and app_file.exists()

    async def install(self) -> bool:
        """
        Set up Canary-STT venv and install dependencies.

        Returns True if successful, False otherwise.
        """
        if await self.is_installed():
            logger.info("Canary-STT is already installed")
            return True

        if not (CANARY_STT_DIR / "app.py").exists():
            logger.error(f"Canary-STT app.py not found at {CANARY_STT_DIR}")
            return False

        logger.info(f"Setting up Canary-STT at {CANARY_STT_DIR}...")

        try:
            # Create venv
            venv_dir = CANARY_STT_DIR / ".venv"
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

            # Install dependencies
            if sys.platform == "win32":
                pip_path = venv_dir / "Scripts" / "pip.exe"
            else:
                pip_path = venv_dir / "bin" / "pip"

            # Install requirements.txt (includes NeMo from git — may be slow)
            requirements_file = CANARY_STT_DIR / "requirements.txt"
            if requirements_file.exists():
                logger.info("Installing Canary-STT dependencies (this may take a while — NeMo + PyTorch)...")
                result = subprocess.run(
                    [str(pip_path), "install", "-r", str(requirements_file)],
                    cwd=CANARY_STT_DIR,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=3600,  # 60 minute timeout (NeMo + PyTorch are large)
                )
                if result.returncode != 0:
                    logger.error(f"pip install requirements failed: {result.stderr}")
                    return False

            logger.info("Canary-STT installed successfully")
            return True

        except subprocess.TimeoutExpired:
            logger.error("Canary-STT installation timed out")
            return False
        except Exception as e:
            logger.error(f"Canary-STT installation failed: {e}")
            return False

    # =========================================================================
    # Server Management
    # =========================================================================

    async def start(self) -> bool:
        """
        Start the Canary-STT server.

        Returns True if started successfully.
        """
        if not await self.is_installed():
            logger.info("Canary-STT not installed, installing now...")
            if not await self.install():
                return False

        if await self.health_check():
            logger.info("Canary-STT server is already running")
            return True

        logger.info(f"Starting Canary-STT server on port {self.api_port}...")

        try:
            venv_dir = CANARY_STT_DIR / ".venv"
            if sys.platform == "win32":
                venv_python = venv_dir / "Scripts" / "python.exe"
            else:
                venv_python = venv_dir / "bin" / "python"

            # Build command
            cmd = [
                str(venv_python),
                "-m",
                "uvicorn",
                "app:app",
                "--host",
                "0.0.0.0",
                "--port",
                str(self.api_port),
            ]

            logger.info(f"Running command: {' '.join(cmd)}")

            # Build environment
            env = os.environ.copy()

            # Start process
            popen_kwargs = {
                "cwd": str(CANARY_STT_DIR),
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

            # Wait for server to start (model download on first run can be slow)
            logger.info("Waiting for Canary-STT server to start...")
            for attempt in range(300):  # 5 minute timeout (model download can be very slow)
                await asyncio.sleep(1)
                if await self.health_check():
                    logger.info("Canary-STT server started successfully")
                    return True

                # Check if process died
                if self._process.poll() is not None:
                    stderr = (
                        self._process.stderr.read().decode()
                        if self._process.stderr
                        else ""
                    )
                    stdout = (
                        self._process.stdout.read().decode()
                        if self._process.stdout
                        else ""
                    )
                    logger.error(
                        f"Canary-STT server died.\nSTDOUT: {stdout}\nSTDERR: {stderr}"
                    )
                    return False

                if attempt > 0 and attempt % 15 == 0:
                    logger.info(
                        f"Still waiting for Canary-STT server... ({attempt}s)"
                    )

            logger.error("Canary-STT server failed to start within timeout")
            return False

        except Exception as e:
            logger.error(f"Failed to start Canary-STT server: {e}")
            return False

    async def stop(self) -> bool:
        """Stop the Canary-STT server."""
        if self._process:
            logger.info("Stopping Canary-STT server...")
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Canary-STT server did not stop gracefully, killing..."
                )
                self._process.kill()
            self._process = None
            logger.info("Canary-STT server stopped")
            return True
        return False

    async def health_check(self) -> bool:
        """Check if Canary-STT server is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{self.base_url}/health", headers=self.headers
                )
                return response.status_code == 200
        except Exception:
            return False

    async def get_status(self) -> Dict[str, Any]:
        """Get detailed status of Canary-STT server."""
        is_healthy = await self.health_check()

        status = {
            "enabled": settings.use_canary_stt,
            "installed": await self.is_installed(),
            "running": is_healthy,
            "url": self.base_url,
            "install_path": str(CANARY_STT_DIR),
        }

        # Get extended info from server if running
        if is_healthy:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    response = await client.get(f"{self.base_url}/info")
                    if response.status_code == 200:
                        info = response.json()
                        status.update(
                            {
                                "model_loaded": info.get("model_loaded", False),
                                "model_repo": info.get("model_repo"),
                                "device": info.get("device"),
                                "gpu": info.get("gpu"),
                                "dtype": info.get("dtype"),
                                "max_audio_duration": info.get("max_audio_duration"),
                                "supported_formats": info.get("supported_formats"),
                            }
                        )
            except Exception:
                pass

        return status

    # =========================================================================
    # Transcription
    # =========================================================================

    async def transcribe(
        self,
        *,
        audio_data: Optional[bytes] = None,
        audio_url: Optional[str] = None,
        filename: str = "audio.wav",
        save_transcript: bool = False,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """
        Transcribe audio using Canary-STT.

        Args:
            audio_data: Raw audio file bytes (for file-based transcription)
            audio_url: URL of audio file to transcribe
            filename: Original filename (for format detection)
            save_transcript: Whether to save transcript to server
            timeout: Maximum wait time in seconds

        Returns:
            Dict with text, duration_seconds, processing_time_seconds, etc.
        """
        if not await self.health_check():
            raise CanarySTTError("Canary-STT server is not running")

        try:
            async with httpx.AsyncClient(timeout=timeout + 10) as client:
                if audio_data:
                    # File upload
                    files = {
                        "file": (filename, audio_data, "audio/wav"),
                    }
                    data = {}
                    if save_transcript:
                        data["save_transcript"] = "true"

                    response = await client.post(
                        f"{self.base_url}/transcribe",
                        files=files,
                        data=data,
                    )
                elif audio_url:
                    # URL-based — audio_url is a query parameter on the canary-stt endpoint
                    # Canary-STT runs on the host, so rewrite Docker-internal
                    # hostnames to localhost so it can resolve them.
                    resolved_url = audio_url.replace(
                        "host.docker.internal", "localhost"
                    )
                    response = await client.post(
                        f"{self.base_url}/transcribe",
                        params={
                            "audio_url": resolved_url,
                            "save_transcript": str(save_transcript).lower(),
                        },
                    )
                else:
                    raise CanarySTTError(
                        "Either audio_data or audio_url must be provided"
                    )

                if response.status_code != 200:
                    error_text = response.text
                    logger.error(
                        f"Transcription failed: {response.status_code} - {error_text}"
                    )
                    raise CanarySTTError(
                        f"Transcription failed: {response.status_code} - {error_text}"
                    )

                result = response.json()

                # Convert relative transcript URL to absolute
                transcript_file = result.get("transcript_file")
                if transcript_file and transcript_file.startswith("/"):
                    transcript_file = f"{self.base_url}{transcript_file}"

                return {
                    "success": True,
                    "text": result.get("text", ""),
                    "duration_seconds": result.get("duration_seconds", 0),
                    "processing_time_seconds": result.get(
                        "processing_time_seconds", 0
                    ),
                    "audio_file": result.get("audio_file"),
                    "transcript_file": transcript_file,
                }

        except httpx.RequestError as e:
            raise CanarySTTError(f"Request failed: {e}")


# Global service instance
_canary_stt_service: Optional[CanarySTTService] = None


def get_canary_stt_service() -> CanarySTTService:
    """Get the global Canary-STT service instance."""
    global _canary_stt_service
    if _canary_stt_service is None:
        _canary_stt_service = CanarySTTService()
    return _canary_stt_service


async def ensure_canary_stt_running() -> bool:
    """Ensure Canary-STT server is running if enabled."""
    if not settings.use_canary_stt:
        return False

    if not settings.canary_stt_auto_start:
        # Check if external server is running
        service = get_canary_stt_service()
        return await service.health_check()

    service = get_canary_stt_service()
    return await service.start()
