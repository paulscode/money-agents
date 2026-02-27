"""
SeedVR2 Upscaler Service - Manages the SeedVR2 local upscaling server.

This service handles:
- Setting up SeedVR2 with its own venv (git clone + pip install)
- Starting/stopping the SeedVR2 server
- Health checks and status monitoring
- Image & video upscaling via the native PyTorch inference API

SeedVR2: https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler
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

# SeedVR2 project paths - standalone server in project root's seedvr2-upscaler/ directory
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent  # project root
SEEDVR2_DIR = Path(os.environ.get("SEEDVR2_DIR", str(PROJECT_ROOT / "seedvr2-upscaler")))


class SeedVR2Error(Exception):
    """Exception for SeedVR2 related errors."""
    pass


class SeedVR2Service:
    """Service for managing SeedVR2 local image & video upscaling."""

    def __init__(self):
        self.api_url = settings.seedvr2_api_url
        self.api_port = settings.seedvr2_api_port
        self.model = settings.seedvr2_model
        self.auto_start = settings.seedvr2_auto_start
        self.idle_timeout = settings.seedvr2_idle_timeout
        self._process: Optional[subprocess.Popen] = None

    @property
    def base_url(self) -> str:
        """Get the base URL for SeedVR2 API."""
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
        """Check if SeedVR2 venv is set up and repo is cloned."""
        venv_dir = SEEDVR2_DIR / ".venv"
        app_file = SEEDVR2_DIR / "app.py"
        seedvr2_src = SEEDVR2_DIR / "seedvr2" / "src"
        return venv_dir.exists() and app_file.exists() and seedvr2_src.exists()

    async def install(self) -> bool:
        """
        Set up SeedVR2 venv, clone repo, and install dependencies.

        Returns True if successful, False otherwise.
        """
        if await self.is_installed():
            logger.info("SeedVR2 is already installed")
            return True

        if not (SEEDVR2_DIR / "app.py").exists():
            logger.error(f"SeedVR2 app.py not found at {SEEDVR2_DIR}")
            return False

        logger.info(f"Setting up SeedVR2 at {SEEDVR2_DIR}...")

        try:
            # Clone SeedVR2 repo if needed
            seedvr2_repo = SEEDVR2_DIR / "seedvr2"
            if not seedvr2_repo.exists():
                logger.info("Cloning SeedVR2 repository from GitHub...")
                result = subprocess.run(
                    [
                        "git", "clone", "--depth", "1",
                        "https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler.git",
                        str(seedvr2_repo),
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=300,
                )
                if result.returncode != 0:
                    logger.error(f"git clone failed: {result.stderr}")
                    return False
                logger.info("SeedVR2 repository cloned successfully")

            # Create venv
            venv_dir = SEEDVR2_DIR / ".venv"
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

            # Install our server requirements first
            requirements_file = SEEDVR2_DIR / "requirements.txt"
            if requirements_file.exists():
                logger.info("Installing SeedVR2 server dependencies...")
                result = subprocess.run(
                    [str(pip_path), "install", "-r", str(requirements_file)],
                    cwd=SEEDVR2_DIR,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=1800,
                )
                if result.returncode != 0:
                    logger.error(f"pip install server requirements failed: {result.stderr}")
                    return False

            # Install SeedVR2 upstream requirements
            seedvr2_reqs = seedvr2_repo / "requirements.txt"
            if seedvr2_reqs.exists():
                logger.info("Installing SeedVR2 upstream dependencies (torch, etc.)...")
                result = subprocess.run(
                    [str(pip_path), "install", "-r", str(seedvr2_reqs)],
                    cwd=str(seedvr2_repo),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=1800,
                )
                if result.returncode != 0:
                    logger.error(f"pip install SeedVR2 requirements failed: {result.stderr}")
                    return False

            logger.info("SeedVR2 installed successfully")
            return True

        except subprocess.TimeoutExpired:
            logger.error("SeedVR2 installation timed out")
            return False
        except Exception as e:
            logger.error(f"SeedVR2 installation failed: {e}")
            return False

    # =========================================================================
    # Server Management
    # =========================================================================

    async def start(self) -> bool:
        """
        Start the SeedVR2 server.

        Returns True if started successfully.
        """
        if not await self.is_installed():
            logger.info("SeedVR2 not installed, installing now...")
            if not await self.install():
                return False

        if await self.health_check():
            logger.info("SeedVR2 server is already running")
            return True

        logger.info(f"Starting SeedVR2 server on port {self.api_port}...")

        try:
            venv_dir = SEEDVR2_DIR / ".venv"
            if sys.platform == "win32":
                venv_python = venv_dir / "Scripts" / "python.exe"
            else:
                venv_python = venv_dir / "bin" / "python"

            cmd = [
                str(venv_python), "-m", "uvicorn", "app:app",
                "--host", "0.0.0.0",
                "--port", str(self.api_port),
            ]

            logger.info(f"Running command: {' '.join(cmd)}")

            env = os.environ.copy()

            popen_kwargs = {
                "cwd": str(SEEDVR2_DIR),
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

            # Wait for server to start (model downloads on first run ~4GB)
            logger.info("Waiting for SeedVR2 server to start...")
            for attempt in range(180):
                await asyncio.sleep(1)
                if await self.health_check():
                    logger.info("SeedVR2 server started successfully")
                    return True

                if self._process.poll() is not None:
                    stderr = self._process.stderr.read().decode() if self._process.stderr else ""
                    stdout = self._process.stdout.read().decode() if self._process.stdout else ""
                    logger.error(f"SeedVR2 server died.\nSTDOUT: {stdout}\nSTDERR: {stderr}")
                    return False

                if attempt > 0 and attempt % 15 == 0:
                    logger.info(f"Still waiting for SeedVR2 server... ({attempt}s)")

            logger.error("SeedVR2 server failed to start within timeout")
            return False

        except Exception as e:
            logger.error(f"Failed to start SeedVR2 server: {e}")
            return False

    async def stop(self) -> bool:
        """Stop the SeedVR2 server."""
        if self._process:
            logger.info("Stopping SeedVR2 server...")
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("SeedVR2 server did not stop gracefully, killing...")
                self._process.kill()
            self._process = None
            logger.info("SeedVR2 server stopped")
            return True
        return False

    async def health_check(self) -> bool:
        """Check if SeedVR2 server is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{self.base_url}/health",
                    headers=self.headers,
                )
                return response.status_code == 200
        except Exception:
            return False

    async def get_status(self) -> Dict[str, Any]:
        """Get detailed status of SeedVR2 server."""
        is_healthy = await self.health_check()

        status = {
            "enabled": settings.use_seedvr2,
            "installed": await self.is_installed(),
            "running": is_healthy,
            "url": self.base_url,
            "model": self.model,
            "install_path": str(SEEDVR2_DIR),
        }

        if is_healthy:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    response = await client.get(f"{self.base_url}/info")
                    if response.status_code == 200:
                        info = response.json()
                        status.update({
                            "model_loaded": info.get("model_loaded", False),
                            "dit_model": info.get("dit_model"),
                            "device": info.get("device"),
                            "gpu": info.get("gpu"),
                            "capabilities": info.get("capabilities", []),
                            "defaults": info.get("defaults"),
                        })
            except Exception:
                pass

        return status

    # =========================================================================
    # Image Upscaling
    # =========================================================================

    async def upscale_image(
        self,
        *,
        image_path: Optional[str] = None,
        image_url: Optional[str] = None,
        resolution: int = 1080,
        max_resolution: int = 0,
        color_correction: str = "lab",
        seed: Optional[int] = None,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """
        Upscale an image using SeedVR2.

        Args:
            image_path: Local file path to the image
            image_url: URL to download the image from
            resolution: Target short-side resolution (default 1080)
            max_resolution: Max resolution cap (0 = no limit)
            color_correction: Color correction method
            seed: Random seed for reproducibility
            timeout: Maximum wait time in seconds

        Returns:
            Dict with success, output_url, output_path, resolutions, timing, etc.
        """
        if not await self.health_check():
            raise SeedVR2Error("SeedVR2 server is not running")

        payload: Dict[str, Any] = {
            "resolution": resolution,
            "max_resolution": max_resolution,
            "color_correction": color_correction,
        }

        if image_path:
            from app.core.path_security import validate_tool_file_path
            validated = validate_tool_file_path(image_path, label="image_path")
            payload["image_path"] = str(validated)
        if image_url:
            # SeedVR2 runs on the host, not in Docker, so rewrite
            # host.docker.internal to localhost for resolution
            resolved_url = image_url.replace("host.docker.internal", "localhost")
            payload["image_url"] = resolved_url
        if seed is not None:
            payload["seed"] = seed

        logger.info(
            f"Upscaling image: path={image_path or image_url}, resolution={resolution}"
        )

        try:
            async with httpx.AsyncClient(timeout=timeout + 10) as client:
                response = await client.post(
                    f"{self.base_url}/upscale/image",
                    headers=self.headers,
                    json=payload,
                )

                if response.status_code != 200:
                    error_text = response.text
                    logger.error(
                        f"Upscaling failed: {response.status_code} - {error_text}"
                    )
                    raise SeedVR2Error(
                        f"Upscaling failed: {response.status_code} - {error_text}"
                    )

                result = response.json()

                # Convert relative URLs to absolute
                output_url = result.get("output_url", "")
                if output_url.startswith("/"):
                    output_url = f"{self.base_url}{output_url}"

                return {
                    "success": True,
                    "output_url": output_url,
                    "output_path": result.get("output_path", ""),
                    "input_resolution": result.get("input_resolution", ""),
                    "output_resolution": result.get("output_resolution", ""),
                    "processing_time_seconds": result.get("processing_time_seconds", 0),
                    "model_used": result.get("model_used", ""),
                    "seed": result.get("seed", 0),
                }

        except httpx.RequestError as e:
            raise SeedVR2Error(f"Request failed: {e}")

    # =========================================================================
    # Video Upscaling
    # =========================================================================

    async def upscale_video(
        self,
        *,
        video_path: Optional[str] = None,
        video_url: Optional[str] = None,
        resolution: int = 1080,
        max_resolution: int = 0,
        batch_size: int = 5,
        temporal_overlap: int = 2,
        color_correction: str = "lab",
        seed: Optional[int] = None,
        timeout: int = 1800,
    ) -> Dict[str, Any]:
        """
        Upscale a video using SeedVR2.

        Args:
            video_path: Local file path to the video
            video_url: URL to download the video from
            resolution: Target short-side resolution (default 1080)
            max_resolution: Max resolution cap (0 = no limit)
            batch_size: Frames per batch (must follow 4n+1: 5, 9, 13...)
            temporal_overlap: Temporal overlap frames
            color_correction: Color correction method
            seed: Random seed for reproducibility
            timeout: Maximum wait time in seconds (video takes longer)

        Returns:
            Dict with success, output_url, output_path, resolutions, timing, etc.
        """
        if not video_path and not video_url:
            raise SeedVR2Error("Provide either video_path or video_url")

        if not await self.health_check():
            raise SeedVR2Error("SeedVR2 server is not running")

        payload: Dict[str, Any] = {
            "resolution": resolution,
            "max_resolution": max_resolution,
            "batch_size": batch_size,
            "temporal_overlap": temporal_overlap,
            "color_correction": color_correction,
        }

        if video_path:
            from app.core.path_security import validate_tool_file_path
            validated = validate_tool_file_path(video_path, label="video_path")
            payload["video_path"] = str(validated)
        if video_url:
            # SeedVR2 runs on the host, not in Docker, so rewrite
            # host.docker.internal to localhost for resolution
            resolved_url = video_url.replace("host.docker.internal", "localhost")
            payload["video_url"] = resolved_url

        if seed is not None:
            payload["seed"] = seed

        source = video_url or video_path
        logger.info(f"Upscaling video: source={source}, resolution={resolution}")

        try:
            async with httpx.AsyncClient(timeout=timeout + 10) as client:
                response = await client.post(
                    f"{self.base_url}/upscale/video",
                    headers=self.headers,
                    json=payload,
                )

                if response.status_code != 200:
                    error_text = response.text
                    logger.error(
                        f"Video upscaling failed: {response.status_code} - {error_text}"
                    )
                    raise SeedVR2Error(
                        f"Video upscaling failed: {response.status_code} - {error_text}"
                    )

                result = response.json()

                output_url = result.get("output_url", "")
                if output_url.startswith("/"):
                    output_url = f"{self.base_url}{output_url}"

                return {
                    "success": True,
                    "output_url": output_url,
                    "output_path": result.get("output_path", ""),
                    "input_resolution": result.get("input_resolution", ""),
                    "output_resolution": result.get("output_resolution", ""),
                    "total_frames": result.get("total_frames", 0),
                    "processing_time_seconds": result.get("processing_time_seconds", 0),
                    "model_used": result.get("model_used", ""),
                    "seed": result.get("seed", 0),
                }

        except httpx.RequestError as e:
            raise SeedVR2Error(f"Request failed: {e}")

    async def download_file(self, file_url: str) -> bytes:
        """Download an upscaled file."""
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(file_url, headers=self.headers)
                if response.status_code == 200:
                    return response.content
                else:
                    raise SeedVR2Error(
                        f"Failed to download file: {response.status_code}"
                    )
        except httpx.RequestError as e:
            raise SeedVR2Error(f"Download failed: {e}")


# Global service instance
_seedvr2_service: Optional[SeedVR2Service] = None


def get_seedvr2_service() -> SeedVR2Service:
    """Get the global SeedVR2 service instance."""
    global _seedvr2_service
    if _seedvr2_service is None:
        _seedvr2_service = SeedVR2Service()
    return _seedvr2_service


async def ensure_seedvr2_running() -> bool:
    """Ensure SeedVR2 server is running if enabled."""
    if not settings.use_seedvr2:
        return False

    if not settings.seedvr2_auto_start:
        service = get_seedvr2_service()
        return await service.health_check()

    service = get_seedvr2_service()
    return await service.start()
