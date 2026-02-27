"""
AudioSR Service - Manages the AudioSR audio super-resolution server.

This service handles:
- Setting up AudioSR with its own venv (pip install audiosr)
- Starting/stopping the AudioSR server
- Health checks and status monitoring
- Audio super-resolution (upscaling to 48kHz) via the local API

Model: AudioSR (https://github.com/haoheliu/versatile_audio_super_resolution)
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

# AudioSR project paths — standalone server in project root's audiosr/ directory
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent  # project root
AUDIOSR_DIR = Path(os.environ.get("AUDIOSR_DIR", str(PROJECT_ROOT / "audiosr")))


class AudioSRError(Exception):
    """Exception for AudioSR related errors."""
    pass


class AudioSRService:
    """Service for managing AudioSR local audio super-resolution."""

    def __init__(self):
        self.api_url = settings.audiosr_api_url
        self.api_port = settings.audiosr_api_port
        self.auto_start = settings.audiosr_auto_start
        self.idle_timeout = settings.audiosr_idle_timeout
        self._process: Optional[subprocess.Popen] = None

    @property
    def base_url(self) -> str:
        """Get the base URL for AudioSR API."""
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
        """Check if AudioSR venv is set up and ready."""
        venv_dir = AUDIOSR_DIR / ".venv"
        app_file = AUDIOSR_DIR / "app.py"
        return venv_dir.exists() and app_file.exists()

    async def install(self) -> bool:
        """
        Set up AudioSR venv and install dependencies.

        Returns True if successful, False otherwise.
        """
        if await self.is_installed():
            logger.info("AudioSR is already installed")
            return True

        if not (AUDIOSR_DIR / "app.py").exists():
            logger.error(f"AudioSR app.py not found at {AUDIOSR_DIR}")
            return False

        logger.info(f"Setting up AudioSR at {AUDIOSR_DIR}...")

        try:
            # Create venv
            venv_dir = AUDIOSR_DIR / ".venv"
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

            # Install requirements.txt
            # AudioSR==0.0.7 pins numpy<=1.23.5 which is incompatible with Python 3.12.
            # Install audiosr with --no-deps first, then install relaxed deps from requirements.txt.
            logger.info("Installing AudioSR package (no-deps for Python 3.12 compat)...")
            result = subprocess.run(
                [str(pip_path), "install", "--no-deps", "audiosr==0.0.7"],
                cwd=AUDIOSR_DIR,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
            if result.returncode != 0:
                logger.error(f"audiosr package install failed: {result.stderr}")
                return False

            requirements_file = AUDIOSR_DIR / "requirements.txt"
            if requirements_file.exists():
                logger.info("Installing AudioSR dependencies (this may take a while — PyTorch + diffusion models)...")
                result = subprocess.run(
                    [str(pip_path), "install", "-r", str(requirements_file)],
                    cwd=AUDIOSR_DIR,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=3600,  # 60 minute timeout
                )
                if result.returncode != 0:
                    logger.error(f"pip install requirements failed: {result.stderr}")
                    return False

            logger.info("AudioSR installed successfully")
            return True

        except subprocess.TimeoutExpired:
            logger.error("AudioSR installation timed out")
            return False
        except Exception as e:
            logger.error(f"AudioSR installation failed: {e}")
            return False

    # =========================================================================
    # Server Management
    # =========================================================================

    async def start(self) -> bool:
        """
        Start the AudioSR server.

        Returns True if started successfully.
        """
        if not await self.is_installed():
            logger.info("AudioSR not installed, installing now...")
            if not await self.install():
                return False

        if await self.health_check():
            logger.info("AudioSR server is already running")
            return True

        logger.info(f"Starting AudioSR server on port {self.api_port}...")

        try:
            venv_dir = AUDIOSR_DIR / ".venv"
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
                "cwd": str(AUDIOSR_DIR),
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
            logger.info("Waiting for AudioSR server to start...")
            for attempt in range(300):  # 5 minute timeout
                await asyncio.sleep(1)
                if await self.health_check():
                    logger.info("AudioSR server started successfully")
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
                        f"AudioSR server died.\nSTDOUT: {stdout}\nSTDERR: {stderr}"
                    )
                    return False

                if attempt > 0 and attempt % 15 == 0:
                    logger.info(
                        f"Still waiting for AudioSR server... ({attempt}s)"
                    )

            logger.error("AudioSR server failed to start within timeout")
            return False

        except Exception as e:
            logger.error(f"Failed to start AudioSR server: {e}")
            return False

    async def stop(self) -> bool:
        """Stop the AudioSR server."""
        if self._process:
            logger.info("Stopping AudioSR server...")
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "AudioSR server did not stop gracefully, killing..."
                )
                self._process.kill()
            self._process = None
            logger.info("AudioSR server stopped")
            return True
        return False

    async def health_check(self) -> bool:
        """Check if AudioSR server is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{self.base_url}/health", headers=self.headers
                )
                return response.status_code == 200
        except Exception:
            return False

    async def get_status(self) -> Dict[str, Any]:
        """Get detailed status of AudioSR server."""
        is_healthy = await self.health_check()

        status = {
            "enabled": settings.use_audiosr,
            "installed": await self.is_installed(),
            "running": is_healthy,
            "url": self.base_url,
            "install_path": str(AUDIOSR_DIR),
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
                                "model_variant": info.get("model_variant"),
                                "device": info.get("device"),
                                "gpu": info.get("gpu"),
                                "output_sample_rate": info.get("output_sample_rate"),
                                "max_audio_duration": info.get("max_audio_duration"),
                            }
                        )
            except Exception:
                pass

        return status

    # =========================================================================
    # Audio Enhancement
    # =========================================================================

    async def enhance(
        self,
        *,
        audio_data: Optional[bytes] = None,
        audio_url: Optional[str] = None,
        filename: str = "audio.wav",
        ddim_steps: int = 50,
        guidance_scale: float = 3.5,
        seed: Optional[int] = None,
        model_name: Optional[str] = None,
        timeout: int = 300,
    ) -> Dict[str, Any]:
        """
        Enhance audio using AudioSR super-resolution.

        Args:
            audio_data: Raw audio file bytes (for file-based enhancement)
            audio_url: URL of audio file to enhance
            filename: Original filename (for format detection)
            ddim_steps: Number of diffusion denoising steps
            guidance_scale: Classifier-free guidance scale
            seed: Random seed for reproducibility
            model_name: Model variant ('basic' or 'speech') — switches on-the-fly
            timeout: Maximum wait time in seconds

        Returns:
            Dict with output_url, output_sample_rate, duration_seconds, processing_time_seconds, etc.
        """
        if not await self.health_check():
            raise AudioSRError("AudioSR server is not running")

        try:
            async with httpx.AsyncClient(timeout=timeout + 10) as client:
                if audio_data:
                    # File upload
                    files = {
                        "file": (filename, audio_data, "audio/wav"),
                    }
                    data = {
                        "ddim_steps": str(ddim_steps),
                        "guidance_scale": str(guidance_scale),
                    }
                    if seed is not None:
                        data["seed"] = str(seed)
                    if model_name:
                        data["model_name"] = model_name

                    response = await client.post(
                        f"{self.base_url}/enhance",
                        files=files,
                        data=data,
                    )
                elif audio_url:
                    # URL-based
                    # AudioSR runs on the host, so rewrite Docker-internal
                    # hostnames to localhost so it can resolve them.
                    resolved_url = audio_url.replace(
                        "host.docker.internal", "localhost"
                    )
                    # AudioSR /enhance uses Form() params, so send as form data
                    data = {
                        "audio_url": resolved_url,
                        "ddim_steps": str(ddim_steps),
                        "guidance_scale": str(guidance_scale),
                    }
                    if seed is not None:
                        data["seed"] = str(seed)
                    if model_name:
                        data["model_name"] = model_name

                    response = await client.post(
                        f"{self.base_url}/enhance",
                        data=data,
                    )
                else:
                    raise AudioSRError(
                        "Either audio_data or audio_url must be provided"
                    )

                if response.status_code != 200:
                    error_text = response.text
                    logger.error(
                        f"Enhancement failed: {response.status_code} - {error_text}"
                    )
                    raise AudioSRError(
                        f"Enhancement failed: {response.status_code} - {error_text}"
                    )

                result = response.json()

                # Convert relative output path to absolute URL
                # Server returns output_file like "/output/AUDIOSR_abc123.wav"
                output_file = result.get("output_file")
                if output_file and output_file.startswith("/"):
                    output_file = f"{self.base_url}{output_file}"

                return {
                    "success": True,
                    "output_url": output_file,
                    "output_sample_rate": result.get("output_sample_rate", 48000),
                    "input_sample_rate": result.get("input_sample_rate"),
                    "duration_seconds": result.get("output_duration_seconds", result.get("input_duration_seconds", 0)),
                    "processing_time_seconds": result.get("processing_time_seconds", 0),
                    "model_variant": result.get("model_variant"),
                    "ddim_steps": result.get("ddim_steps"),
                    "guidance_scale": result.get("guidance_scale"),
                    "seed": result.get("seed"),
                    "chunking_used": result.get("chunking_used", False),
                }

        except httpx.RequestError as e:
            raise AudioSRError(f"Request failed: {e}")


# Global service instance
_audiosr_service: Optional[AudioSRService] = None


def get_audiosr_service() -> AudioSRService:
    """Get the global AudioSR service instance."""
    global _audiosr_service
    if _audiosr_service is None:
        _audiosr_service = AudioSRService()
    return _audiosr_service


async def ensure_audiosr_running() -> bool:
    """Ensure AudioSR server is running if enabled."""
    if not settings.use_audiosr:
        return False

    if not settings.audiosr_auto_start:
        # Check if external server is running
        service = get_audiosr_service()
        return await service.health_check()

    service = get_audiosr_service()
    return await service.start()
