"""
Z-Image Service - Manages the Z-Image local image generation server.

This service handles:
- Setting up Z-Image with its own venv (git clone + pip install)
- Starting/stopping the Z-Image server
- Health checks and status monitoring
- Image generation via the native PyTorch inference API

Z-Image: https://github.com/Tongyi-MAI/Z-Image
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

# Z-Image project paths - standalone server in project root's z-image/ directory
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent  # project root
ZIMAGE_DIR = Path(os.environ.get("ZIMAGE_DIR", str(PROJECT_ROOT / "z-image")))


class ZImageError(Exception):
    """Exception for Z-Image related errors."""
    pass


class ZImageService:
    """Service for managing Z-Image local image generation."""
    
    def __init__(self):
        self.api_url = settings.zimage_api_url
        self.api_port = settings.zimage_api_port
        self.model = settings.zimage_model
        self.auto_start = settings.zimage_auto_start
        self.idle_timeout = settings.zimage_idle_timeout
        self._process: Optional[subprocess.Popen] = None
    
    @property
    def base_url(self) -> str:
        """Get the base URL for Z-Image API."""
        url = self.api_url.rstrip('/')
        if ':' in url.split('/')[-1]:
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
        """Check if Z-Image venv is set up and ready."""
        venv_dir = ZIMAGE_DIR / ".venv"
        app_file = ZIMAGE_DIR / "app.py"
        zimage_src = ZIMAGE_DIR / "Z-Image" / "src"
        return venv_dir.exists() and app_file.exists() and zimage_src.exists()
    
    async def install(self) -> bool:
        """
        Set up Z-Image venv, clone repo, and install dependencies.
        
        Returns True if successful, False otherwise.
        """
        if await self.is_installed():
            logger.info("Z-Image is already installed")
            return True
        
        if not (ZIMAGE_DIR / "app.py").exists():
            logger.error(f"Z-Image app.py not found at {ZIMAGE_DIR}")
            return False
        
        logger.info(f"Setting up Z-Image at {ZIMAGE_DIR}...")
        
        try:
            # Clone Z-Image repo if needed
            zimage_repo = ZIMAGE_DIR / "Z-Image"
            if not zimage_repo.exists():
                logger.info("Cloning Z-Image repository from GitHub...")
                result = subprocess.run(
                    ["git", "clone", "https://github.com/Tongyi-MAI/Z-Image.git", str(zimage_repo)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=300  # 5 minute timeout for clone
                )
                if result.returncode != 0:
                    logger.error(f"git clone failed: {result.stderr}")
                    return False
                logger.info("Z-Image repository cloned successfully")
            
            # Create venv
            venv_dir = ZIMAGE_DIR / ".venv"
            if not venv_dir.exists():
                logger.info("Creating Python venv...")
                result = subprocess.run(
                    [sys.executable, "-m", "venv", str(venv_dir)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60
                )
                if result.returncode != 0:
                    logger.error(f"venv creation failed: {result.stderr}")
                    return False
            
            # Install dependencies
            if sys.platform == "win32":
                pip_path = venv_dir / "Scripts" / "pip.exe"
            else:
                pip_path = venv_dir / "bin" / "pip"
            
            # Install requirements.txt first (FastAPI, uvicorn, etc.)
            requirements_file = ZIMAGE_DIR / "requirements.txt"
            if requirements_file.exists():
                logger.info("Installing Z-Image server dependencies...")
                result = subprocess.run(
                    [str(pip_path), "install", "-r", str(requirements_file)],
                    cwd=ZIMAGE_DIR,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=1800  # 30 minute timeout (torch is large)
                )
                if result.returncode != 0:
                    logger.error(f"pip install requirements failed: {result.stderr}")
                    return False
            
            # Install Z-Image package in editable mode
            logger.info("Installing Z-Image package...")
            result = subprocess.run(
                [str(pip_path), "install", "-e", str(zimage_repo)],
                cwd=ZIMAGE_DIR,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600  # 10 minute timeout
            )
            if result.returncode != 0:
                logger.error(f"pip install Z-Image failed: {result.stderr}")
                return False
            
            logger.info("Z-Image installed successfully")
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("Z-Image installation timed out")
            return False
        except Exception as e:
            logger.error(f"Z-Image installation failed: {e}")
            return False
    
    # =========================================================================
    # Server Management
    # =========================================================================
    
    async def start(self) -> bool:
        """
        Start the Z-Image server.
        
        Returns True if started successfully.
        """
        if not await self.is_installed():
            logger.info("Z-Image not installed, installing now...")
            if not await self.install():
                return False
        
        if await self.health_check():
            logger.info("Z-Image server is already running")
            return True
        
        logger.info(f"Starting Z-Image server on port {self.api_port}...")
        
        try:
            venv_dir = ZIMAGE_DIR / ".venv"
            if sys.platform == "win32":
                venv_python = venv_dir / "Scripts" / "python.exe"
            else:
                venv_python = venv_dir / "bin" / "python"
            
            # Build command
            cmd = [
                str(venv_python), "-m", "uvicorn", "app:app",
                "--host", "0.0.0.0",
                "--port", str(self.api_port),
            ]
            
            logger.info(f"Running command: {' '.join(cmd)}")
            
            # Build environment
            env = os.environ.copy()
            
            # Start process
            popen_kwargs = {
                "cwd": str(ZIMAGE_DIR),
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
            
            # Wait for server to start (model downloads on first run)
            logger.info("Waiting for Z-Image server to start...")
            for attempt in range(180):  # 3 minute timeout (model download can be slow)
                await asyncio.sleep(1)
                if await self.health_check():
                    logger.info("Z-Image server started successfully")
                    return True
                
                # Check if process died
                if self._process.poll() is not None:
                    stderr = self._process.stderr.read().decode() if self._process.stderr else ""
                    stdout = self._process.stdout.read().decode() if self._process.stdout else ""
                    logger.error(f"Z-Image server died.\nSTDOUT: {stdout}\nSTDERR: {stderr}")
                    return False
                
                if attempt > 0 and attempt % 15 == 0:
                    logger.info(f"Still waiting for Z-Image server... ({attempt}s)")
            
            logger.error("Z-Image server failed to start within timeout")
            return False
            
        except Exception as e:
            logger.error(f"Failed to start Z-Image server: {e}")
            return False
    
    async def stop(self) -> bool:
        """Stop the Z-Image server."""
        if self._process:
            logger.info("Stopping Z-Image server...")
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Z-Image server did not stop gracefully, killing...")
                self._process.kill()
            self._process = None
            logger.info("Z-Image server stopped")
            return True
        return False
    
    async def health_check(self) -> bool:
        """Check if Z-Image server is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(
                    f"{self.base_url}/health",
                    headers=self.headers
                )
                return response.status_code == 200
        except Exception:
            return False
    
    async def get_status(self) -> Dict[str, Any]:
        """Get detailed status of Z-Image server."""
        is_healthy = await self.health_check()
        
        status = {
            "enabled": settings.use_zimage,
            "installed": await self.is_installed(),
            "running": is_healthy,
            "url": self.base_url,
            "model": self.model,
            "install_path": str(ZIMAGE_DIR),
        }
        
        # Get extended info from server if running
        if is_healthy:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    response = await client.get(f"{self.base_url}/info")
                    if response.status_code == 200:
                        info = response.json()
                        status.update({
                            "model_loaded": info.get("model_loaded", False),
                            "model_variant": info.get("model_variant"),
                            "device": info.get("device"),
                            "gpu": info.get("gpu"),
                            "defaults": info.get("defaults"),
                        })
            except Exception:
                pass
        
        return status
    
    # =========================================================================
    # Image Generation
    # =========================================================================
    
    async def generate_image(
        self,
        *,
        prompt: str,
        negative_prompt: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        num_inference_steps: Optional[int] = None,
        guidance_scale: Optional[float] = None,
        seed: Optional[int] = None,
        num_images_per_prompt: int = 1,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """
        Generate image(s) using Z-Image.
        
        Args:
            prompt: Text description of the image to generate
            negative_prompt: What to avoid in the image
            width: Image width (must be divisible by 16)
            height: Image height (must be divisible by 16)
            num_inference_steps: Denoising steps (default varies by model)
            guidance_scale: CFG scale (default varies by model)
            seed: Random seed for reproducibility
            num_images_per_prompt: Number of images to generate (1-4)
            timeout: Maximum wait time in seconds
            
        Returns:
            Dict with success, image_url, image_urls, seed, generation_time, etc.
        """
        if not await self.health_check():
            raise ZImageError("Z-Image server is not running")
        
        # Build request payload
        payload: Dict[str, Any] = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "num_images_per_prompt": num_images_per_prompt,
        }
        
        if negative_prompt:
            payload["negative_prompt"] = negative_prompt
        if num_inference_steps is not None:
            payload["num_inference_steps"] = num_inference_steps
        if guidance_scale is not None:
            payload["guidance_scale"] = guidance_scale
        if seed is not None:
            payload["seed"] = seed
        
        logger.info(f"Generating image: prompt='{prompt[:80]}...', {width}x{height}")
        
        try:
            async with httpx.AsyncClient(timeout=timeout + 10) as client:
                response = await client.post(
                    f"{self.base_url}/generate",
                    headers=self.headers,
                    json=payload
                )
                
                if response.status_code != 200:
                    error_text = response.text
                    logger.error(f"Generation failed: {response.status_code} - {error_text}")
                    raise ZImageError(f"Generation failed: {response.status_code} - {error_text}")
                
                result = response.json()
                
                # Convert relative URLs to absolute
                image_url = result.get("image_url", "")
                if image_url.startswith("/"):
                    image_url = f"{self.base_url}{image_url}"
                
                image_urls = []
                for url in result.get("image_urls", []):
                    if url.startswith("/"):
                        url = f"{self.base_url}{url}"
                    image_urls.append(url)
                
                return {
                    "success": True,
                    "image_url": image_url,
                    "image_urls": image_urls,
                    "seed": result.get("seed", 0),
                    "width": result.get("width", width),
                    "height": result.get("height", height),
                    "num_inference_steps": result.get("num_inference_steps", 0),
                    "guidance_scale": result.get("guidance_scale", 0.0),
                    "generation_time_seconds": result.get("generation_time_seconds", 0),
                    "model_variant": result.get("model_variant", "unknown"),
                }
                
        except httpx.RequestError as e:
            raise ZImageError(f"Request failed: {e}")
    
    async def download_image(self, image_url: str) -> bytes:
        """Download generated image file."""
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(image_url, headers=self.headers)
                if response.status_code == 200:
                    return response.content
                else:
                    raise ZImageError(f"Failed to download image: {response.status_code}")
        except httpx.RequestError as e:
            raise ZImageError(f"Download failed: {e}")


# Global service instance
_zimage_service: Optional[ZImageService] = None


def get_zimage_service() -> ZImageService:
    """Get the global Z-Image service instance."""
    global _zimage_service
    if _zimage_service is None:
        _zimage_service = ZImageService()
    return _zimage_service


async def ensure_zimage_running() -> bool:
    """Ensure Z-Image server is running if enabled."""
    if not settings.use_zimage:
        return False
    
    if not settings.zimage_auto_start:
        # Check if external server is running
        service = get_zimage_service()
        return await service.health_check()
    
    service = get_zimage_service()
    return await service.start()
