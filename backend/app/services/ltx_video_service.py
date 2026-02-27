"""
LTX-2 Video Service - Manages the LTX-2 native video generation server.

This service handles:
- Setting up LTX-2 with its own venv + ltx-pipelines from git
- Starting/stopping the LTX-2 FastAPI server
- Health checks and status monitoring
- Video generation via the local API

Model: Lightricks LTX-2 19B Distilled FP8
(https://huggingface.co/Lightricks/LTX-2)
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

# LTX-2 project paths — standalone server in project root's ltx-video/ directory
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent  # money-agents
LTX_VIDEO_DIR = Path(os.environ.get("LTX_VIDEO_DIR", str(PROJECT_ROOT / "ltx-video")))


class LTXVideoError(Exception):
    """Exception for LTX-2 video related errors."""
    pass


class LTXVideoService:
    """Service for managing LTX-2 local video generation."""

    def __init__(self):
        self.api_url = settings.ltx_video_api_url
        self.api_port = settings.ltx_video_api_port
        self.auto_start = settings.ltx_video_auto_start
        self.idle_timeout = settings.ltx_video_idle_timeout
        self._process: Optional[subprocess.Popen] = None

    @property
    def base_url(self) -> str:
        """Get the base URL for LTX-2 API."""
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
        """Check if LTX-2 venv is set up and ready."""
        venv_dir = LTX_VIDEO_DIR / ".venv"
        app_file = LTX_VIDEO_DIR / "app.py"
        return venv_dir.exists() and app_file.exists()

    async def install(self) -> bool:
        """
        Set up LTX-2 venv and install dependencies.

        Returns True if successful, False otherwise.
        """
        if await self.is_installed():
            logger.info("LTX-2 Video is already installed")
            return True

        if not (LTX_VIDEO_DIR / "app.py").exists():
            logger.error(f"LTX-2 app.py not found at {LTX_VIDEO_DIR}")
            return False

        logger.info(f"Setting up LTX-2 Video at {LTX_VIDEO_DIR}...")

        try:
            # Create venv
            venv_dir = LTX_VIDEO_DIR / ".venv"
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

            # Determine pip path
            if sys.platform == "win32":
                pip_path = venv_dir / "Scripts" / "pip.exe"
            else:
                pip_path = venv_dir / "bin" / "pip"

            # Install requirements.txt
            requirements_file = LTX_VIDEO_DIR / "requirements.txt"
            if requirements_file.exists():
                logger.info("Installing LTX-2 dependencies...")
                result = subprocess.run(
                    [str(pip_path), "install", "-r", str(requirements_file)],
                    cwd=LTX_VIDEO_DIR,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=1800,  # 30 min (PyTorch is large)
                )
                if result.returncode != 0:
                    logger.error(f"pip install requirements failed: {result.stderr}")
                    return False

            # Install ltx-pipelines + ltx-core from git
            logger.info("Installing ltx-pipelines from GitHub...")
            for pkg in ["packages/ltx-core", "packages/ltx-pipelines"]:
                url = f"git+https://github.com/Lightricks/LTX-2.git#subdirectory={pkg}"
                result = subprocess.run(
                    [str(pip_path), "install", url],
                    cwd=LTX_VIDEO_DIR,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=600,
                )
                if result.returncode != 0:
                    logger.error(f"Failed to install {pkg}: {result.stderr}")
                    return False

            logger.info("LTX-2 Video installed successfully")

            # Download model files if missing
            await self._download_models()

            return True

        except subprocess.TimeoutExpired:
            logger.error("LTX-2 installation timed out")
            return False
        except Exception as e:
            logger.error(f"LTX-2 installation failed: {e}")
            return False

    async def _download_models(self) -> bool:
        """
        Download LTX-2 model files (~77 GB) from HuggingFace.

        Runs download_models.py inside the LTX-2 venv.
        Does NOT fail the install if download fails — models can be retried later.
        """
        download_script = LTX_VIDEO_DIR / "download_models.py"
        if not download_script.exists():
            logger.warning("download_models.py not found, skipping model download")
            return False

        venv_dir = LTX_VIDEO_DIR / ".venv"
        if sys.platform == "win32":
            python_path = venv_dir / "Scripts" / "python.exe"
        else:
            python_path = venv_dir / "bin" / "python"

        # Check what's needed
        try:
            result = subprocess.run(
                [str(python_path), str(download_script), "--check"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30,
            )
            if result.returncode == 0:
                logger.info("All LTX-2 model files already present")
                return True
        except Exception:
            pass

        logger.info("Downloading LTX-2 model files from HuggingFace (~77 GB)...")
        try:
            result = subprocess.run(
                [str(python_path), str(download_script)],
                cwd=str(LTX_VIDEO_DIR),
                capture_output=True, text=True, encoding="utf-8",
                errors="replace",
            )
            if result.returncode == 0:
                logger.info("LTX-2 model files downloaded successfully")
                return True
            else:
                logger.warning(
                    f"Model download incomplete: {result.stderr[-500:] if result.stderr else 'unknown error'}. "
                    "Re-run to resume."
                )
                return False
        except Exception as e:
            logger.warning(f"Model download error: {e}. Re-run to resume.")
            return False

    # =========================================================================
    # Server Management
    # =========================================================================

    async def start(self) -> bool:
        """
        Start the LTX-2 server.

        Returns True if started successfully.
        """
        if not await self.is_installed():
            logger.info("LTX-2 Video not installed, installing now...")
            if not await self.install():
                return False

        if await self.health_check():
            logger.info("LTX-2 Video server is already running")
            return True

        logger.info(f"Starting LTX-2 Video server on port {self.api_port}...")

        try:
            venv_dir = LTX_VIDEO_DIR / ".venv"
            if sys.platform == "win32":
                venv_python = venv_dir / "Scripts" / "python.exe"
            else:
                venv_python = venv_dir / "bin" / "python"

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

            env = os.environ.copy()
            # Critical for 24GB VRAM management
            env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

            popen_kwargs = {
                "cwd": str(LTX_VIDEO_DIR),
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

            # Wait for server to come up (model loads lazily, so server itself starts fast)
            logger.info("Waiting for LTX-2 Video server to start...")
            for attempt in range(60):  # 60 second timeout for server start
                await asyncio.sleep(1)
                if await self.health_check():
                    logger.info("LTX-2 Video server started successfully")
                    return True

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
                        f"LTX-2 Video server died.\nSTDOUT: {stdout}\nSTDERR: {stderr}"
                    )
                    return False

                if attempt > 0 and attempt % 15 == 0:
                    logger.info(
                        f"Still waiting for LTX-2 Video server... ({attempt}s)"
                    )

            logger.error("LTX-2 Video server failed to start within timeout")
            return False

        except Exception as e:
            logger.error(f"Failed to start LTX-2 Video server: {e}")
            return False

    async def stop(self) -> bool:
        """Stop the LTX-2 server."""
        if self._process:
            logger.info("Stopping LTX-2 Video server...")
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "LTX-2 Video server did not stop gracefully, killing..."
                )
                self._process.kill()
            self._process = None
            logger.info("LTX-2 Video server stopped")
            return True
        return False

    async def health_check(self) -> bool:
        """Check if LTX-2 server is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{self.base_url}/health", headers=self.headers
                )
                return response.status_code == 200
        except Exception:
            return False

    async def get_status(self) -> Dict[str, Any]:
        """Get detailed status of LTX-2 server."""
        is_healthy = await self.health_check()

        status = {
            "enabled": settings.use_ltx_video,
            "installed": await self.is_installed(),
            "running": is_healthy,
            "url": self.base_url,
            "model": "ltx-2-19b-distilled-fp8",
        }

        if is_healthy:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.get(
                        f"{self.base_url}/info", headers=self.headers
                    )
                    if response.status_code == 200:
                        status.update(response.json())
            except Exception:
                pass

        return status

    # =========================================================================
    # Video Generation
    # =========================================================================

    async def generate_video(
        self,
        prompt: str,
        width: int = 768,
        height: int = 512,
        num_frames: int = 241,
        fps: int = 24,
        seed: Optional[int] = None,
        enhance_prompt: bool = False,
        timeout: float = 600,
    ) -> Dict[str, Any]:
        """
        Generate video with synchronized audio.

        Args:
            prompt: Text description of the video scene.
            width: Output width (divisible by 32, default 768).
            height: Output height (divisible by 32, default 512).
            num_frames: Frame count, must be (N*8)+1 (default 241 = ~10s).
            fps: Frames per second (default 24).
            seed: Random seed for reproducibility.
            enhance_prompt: Enhance prompt via Ollama before generation.
            timeout: Request timeout in seconds (default 600).

        Returns:
            Dict with video_url, filename, duration_seconds, resolution,
            frames, fps, has_audio, inference_time, seed, model.

        Raises:
            LTXVideoError on failure.
        """
        payload = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_frames": num_frames,
            "fps": fps,
            "enhance_prompt": enhance_prompt,
        }
        if seed is not None:
            payload["seed"] = seed

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{self.base_url}/generate",
                    json=payload,
                    headers=self.headers,
                )

                if response.status_code != 200:
                    error_detail = response.json().get("detail", response.text)
                    raise LTXVideoError(f"Generation failed: {error_detail}")

                result = response.json()

                # Convert localhost URLs to the configured base URL for Docker access
                if "video_url" in result:
                    result["video_url"] = result["video_url"].replace(
                        "http://127.0.0.1:", f"http://host.docker.internal:"
                    )

                return result

        except httpx.TimeoutException:
            raise LTXVideoError(
                f"Video generation timed out after {timeout}s. "
                "Try reducing num_frames or resolution."
            )
        except LTXVideoError:
            raise
        except Exception as e:
            raise LTXVideoError(f"Video generation request failed: {str(e)}")

    async def download_video(self, video_url: str, timeout: float = 30) -> bytes:
        """Download a generated video file by URL."""
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(video_url)
                response.raise_for_status()
                return response.content
        except Exception as e:
            raise LTXVideoError(f"Failed to download video: {e}")


# =============================================================================
# Singleton
# =============================================================================

_ltx_video_service: Optional[LTXVideoService] = None


def get_ltx_video_service() -> LTXVideoService:
    """Get or create singleton LTXVideoService."""
    global _ltx_video_service
    if _ltx_video_service is None:
        _ltx_video_service = LTXVideoService()
    return _ltx_video_service


async def ensure_ltx_video_running() -> bool:
    """Ensure LTX-2 Video server is running. Returns True if healthy."""
    if not settings.use_ltx_video:
        return False
    service = get_ltx_video_service()
    if await service.health_check():
        return True
    if settings.ltx_video_auto_start:
        return await service.start()
    return False
