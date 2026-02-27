"""
Real-ESRGAN CPU Upscaler Service — Manages the Real-ESRGAN CPU-only upscaler server.

This service handles:
- Setting up the Real-ESRGAN venv (pip install torch CPU + realesrgan etc.)
- Starting/stopping the Real-ESRGAN server
- Health checks and status monitoring
- Dispatching upscale operations (image and video)

CPU-only — no GPU required.  Port 8009.
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

# Real-ESRGAN CPU project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent  # /home/…/money-agents
REALESRGAN_CPU_DIR = Path(os.environ.get("REALESRGAN_CPU_DIR", str(PROJECT_ROOT / "realesrgan-cpu")))


class RealESRGANCpuError(Exception):
    """Exception for Real-ESRGAN CPU related errors."""
    pass


class RealESRGANCpuService:
    """Service for managing the Real-ESRGAN CPU upscaler local server."""

    def __init__(self):
        self.api_url = settings.realesrgan_cpu_api_url
        self.api_port = settings.realesrgan_cpu_api_port
        self.auto_start = settings.realesrgan_cpu_auto_start
        self.model = settings.realesrgan_cpu_model
        self._process: Optional[subprocess.Popen] = None

    @property
    def base_url(self) -> str:
        """Get the base URL for Real-ESRGAN CPU API."""
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
        """Check if Real-ESRGAN CPU venv is set up and ready."""
        venv_dir = REALESRGAN_CPU_DIR / ".venv"
        app_file = REALESRGAN_CPU_DIR / "app.py"
        return venv_dir.exists() and app_file.exists()

    async def install(self) -> bool:
        """Set up Real-ESRGAN CPU venv and install dependencies."""
        if await self.is_installed():
            logger.info("Real-ESRGAN CPU is already installed")
            return True

        if not (REALESRGAN_CPU_DIR / "app.py").exists():
            logger.error(f"Real-ESRGAN CPU app.py not found at {REALESRGAN_CPU_DIR}")
            return False

        logger.info(f"Setting up Real-ESRGAN CPU at {REALESRGAN_CPU_DIR}...")

        try:
            venv_dir = REALESRGAN_CPU_DIR / ".venv"
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

            # Install CPU-only PyTorch first
            logger.info("Installing CPU-only PyTorch...")
            result = subprocess.run(
                [
                    str(pip_path), "install",
                    "torch", "torchvision",
                    "--index-url", "https://download.pytorch.org/whl/cpu",
                ],
                cwd=REALESRGAN_CPU_DIR,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
            if result.returncode != 0:
                logger.error(f"PyTorch CPU install failed: {result.stderr}")
                return False

            # Install remaining requirements
            requirements_file = REALESRGAN_CPU_DIR / "requirements.txt"
            if requirements_file.exists():
                logger.info("Installing Real-ESRGAN CPU dependencies...")
                result = subprocess.run(
                    [str(pip_path), "install", "-r", str(requirements_file)],
                    cwd=REALESRGAN_CPU_DIR,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=600,
                )
                if result.returncode != 0:
                    logger.error(f"pip install requirements failed: {result.stderr}")
                    return False

            logger.info("Real-ESRGAN CPU installed successfully")
            return True

        except subprocess.TimeoutExpired:
            logger.error("Real-ESRGAN CPU installation timed out")
            return False
        except Exception as e:
            logger.error(f"Real-ESRGAN CPU installation failed: {e}")
            return False

    # =========================================================================
    # Server Management
    # =========================================================================

    async def start(self) -> bool:
        """Start the Real-ESRGAN CPU server."""
        if not await self.is_installed():
            logger.info("Real-ESRGAN CPU not installed, installing now...")
            if not await self.install():
                return False

        if await self.health_check():
            logger.info("Real-ESRGAN CPU server is already running")
            return True

        logger.info(f"Starting Real-ESRGAN CPU server on port {self.api_port}...")

        try:
            venv_dir = REALESRGAN_CPU_DIR / ".venv"
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
                "cwd": str(REALESRGAN_CPU_DIR),
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

            # Model loading can take 30-60s on CPU
            logger.info("Waiting for Real-ESRGAN CPU server to start (model loading may take a minute)...")
            for attempt in range(90):  # 90s timeout — model loading on CPU is slow
                await asyncio.sleep(1)
                if await self.health_check():
                    logger.info("Real-ESRGAN CPU server started successfully")
                    return True

                if self._process.poll() is not None:
                    stderr = self._process.stderr.read().decode() if self._process.stderr else ""
                    stdout = self._process.stdout.read().decode() if self._process.stdout else ""
                    logger.error(f"Real-ESRGAN CPU server died.\nSTDOUT: {stdout}\nSTDERR: {stderr}")
                    return False

            logger.error("Real-ESRGAN CPU server failed to start within timeout")
            return False

        except Exception as e:
            logger.error(f"Failed to start Real-ESRGAN CPU server: {e}")
            return False

    async def stop(self) -> bool:
        """Stop the Real-ESRGAN CPU server."""
        if self._process:
            logger.info("Stopping Real-ESRGAN CPU server...")
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Real-ESRGAN CPU server did not stop gracefully, killing...")
                self._process.kill()
            self._process = None
            logger.info("Real-ESRGAN CPU server stopped")
            return True
        return False

    async def health_check(self) -> bool:
        """Check if Real-ESRGAN CPU server is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/health", headers=self.headers)
                return response.status_code == 200
        except Exception:
            return False

    async def get_status(self) -> Dict[str, Any]:
        """Get detailed status of Real-ESRGAN CPU server."""
        is_healthy = await self.health_check()

        status = {
            "enabled": settings.use_realesrgan_cpu,
            "installed": await self.is_installed(),
            "running": is_healthy,
            "url": self.base_url,
            "model": self.model,
            "install_path": str(REALESRGAN_CPU_DIR),
        }

        if is_healthy:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    response = await client.get(f"{self.base_url}/info")
                    if response.status_code == 200:
                        info = response.json()
                        status.update({
                            "model_loaded": info.get("model_loaded"),
                            "device": info.get("device"),
                            "capabilities": info.get("capabilities"),
                            "supported_models": info.get("supported_models"),
                        })
            except Exception:
                pass

        return status

    # =========================================================================
    # Upscale Operations
    # =========================================================================

    async def upscale_image(
        self,
        *,
        image_url: Optional[str] = None,
        image_path: Optional[str] = None,
        scale: Optional[int] = None,
        tile: Optional[int] = None,
        model_name: Optional[str] = None,
        timeout: int = 600,
    ) -> Dict[str, Any]:
        """
        Upscale an image via the Real-ESRGAN CPU server.

        Args:
            image_url: URL of image to upscale
            image_path: Local path to image file
            scale: Upscale factor (2 or 4)
            tile: Tile size for processing
            model_name: Model to use (switches on-the-fly if different)
            timeout: Maximum wait time in seconds

        Returns:
            Dict with output file path, dimensions, and timing info
        """
        if not await self.health_check():
            raise RealESRGANCpuError("Real-ESRGAN CPU server is not running")

        try:
            data = {}
            files = {}

            if image_path:
                from app.core.path_security import validate_tool_file_path
                validated = validate_tool_file_path(image_path, label="image_path")
                files["file"] = open(str(validated), "rb")
            elif image_url:
                data["image_url"] = image_url

            if scale:
                data["scale"] = str(scale)
            if tile is not None:
                data["tile"] = str(tile)
            if model_name:
                data["model_name"] = model_name

            async with httpx.AsyncClient(timeout=timeout + 10) as client:
                response = await client.post(
                    f"{self.base_url}/upscale/image",
                    data=data,
                    files=files if files else None,
                )

            if files:
                for f in files.values():
                    f.close()

            if response.status_code != 200:
                error_text = response.text
                logger.error(f"Image upscale failed: {response.status_code} - {error_text}")
                raise RealESRGANCpuError(f"Image upscale failed: {response.status_code} - {error_text}")

            result = response.json()

            # Convert relative URL to absolute
            output_file = result.get("output_file")
            if output_file and output_file.startswith("/"):
                result["output_file"] = f"{self.base_url}{output_file}"

            return result

        except httpx.RequestError as e:
            raise RealESRGANCpuError(f"Request failed: {e}")

    async def upscale_video(
        self,
        *,
        video_url: Optional[str] = None,
        video_path: Optional[str] = None,
        scale: Optional[int] = None,
        tile: Optional[int] = None,
        model_name: Optional[str] = None,
        timeout: int = 3600,
    ) -> Dict[str, Any]:
        """
        Upscale a video via the Real-ESRGAN CPU server.

        WARNING: CPU video upscaling is SLOW. Use for short clips only.

        Args:
            video_url: URL of video to upscale
            video_path: Local path to video file
            scale: Upscale factor (2 or 4)
            tile: Tile size for processing
            model_name: Model to use (switches on-the-fly if different)
            timeout: Maximum wait time in seconds

        Returns:
            Dict with output file path, dimensions, frame count, and timing info
        """
        if not await self.health_check():
            raise RealESRGANCpuError("Real-ESRGAN CPU server is not running")

        try:
            data = {}
            files = {}

            if video_path:
                from app.core.path_security import validate_tool_file_path
                validated = validate_tool_file_path(video_path, label="video_path")
                files["file"] = open(str(validated), "rb")
            elif video_url:
                data["video_url"] = video_url

            if scale:
                data["scale"] = str(scale)
            if tile is not None:
                data["tile"] = str(tile)
            if model_name:
                data["model_name"] = model_name

            async with httpx.AsyncClient(timeout=timeout + 10) as client:
                response = await client.post(
                    f"{self.base_url}/upscale/video",
                    data=data,
                    files=files if files else None,
                )

            if files:
                for f in files.values():
                    f.close()

            if response.status_code != 200:
                error_text = response.text
                logger.error(f"Video upscale failed: {response.status_code} - {error_text}")
                raise RealESRGANCpuError(f"Video upscale failed: {response.status_code} - {error_text}")

            result = response.json()

            # Convert relative URL to absolute
            output_file = result.get("output_file")
            if output_file and output_file.startswith("/"):
                result["output_file"] = f"{self.base_url}{output_file}"

            return result

        except httpx.RequestError as e:
            raise RealESRGANCpuError(f"Request failed: {e}")


# Global service instance
_realesrgan_cpu_service: Optional[RealESRGANCpuService] = None


def get_realesrgan_cpu_service() -> RealESRGANCpuService:
    """Get the global Real-ESRGAN CPU service instance."""
    global _realesrgan_cpu_service
    if _realesrgan_cpu_service is None:
        _realesrgan_cpu_service = RealESRGANCpuService()
    return _realesrgan_cpu_service


async def ensure_realesrgan_cpu_running() -> bool:
    """Ensure Real-ESRGAN CPU server is running if enabled."""
    if not settings.use_realesrgan_cpu:
        return False

    if not settings.realesrgan_cpu_auto_start:
        service = get_realesrgan_cpu_service()
        return await service.health_check()

    service = get_realesrgan_cpu_service()
    return await service.start()
