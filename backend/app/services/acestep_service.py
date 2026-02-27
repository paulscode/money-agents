"""
ACE-Step Service - Manages the ACE-Step local music generation server.

This service handles:
- Cloning and setting up ACE-Step from GitHub
- Starting/stopping the ACE-Step server
- Health checks and status monitoring
- Music generation task submission and result retrieval

ACE-Step 1.5: https://github.com/ace-step/ACE-Step-1.5
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# ACE-Step project paths - clone to project root's acestep/ directory
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent  # project root
ACESTEP_DIR = Path(os.environ.get("ACESTEP_DIR", str(PROJECT_ROOT / "acestep")))
ACESTEP_REPO = "https://github.com/ace-step/ACE-Step-1.5.git"


class ACEStepError(Exception):
    """Exception for ACE-Step related errors."""
    pass


class ACEStepService:
    """Service for managing ACE-Step local music generation."""
    
    def __init__(self):
        self.api_url = settings.acestep_api_url
        self.api_port = settings.acestep_api_port
        self.api_key = settings.acestep_api_key
        self.model = settings.acestep_model
        self.max_steps = settings.acestep_max_steps
        self.default_steps = settings.acestep_default_steps
        self.download_source = settings.acestep_download_source
        self._process: Optional[subprocess.Popen] = None
    
    @property
    def base_url(self) -> str:
        """Get the base URL for ACE-Step API."""
        # Remove port from api_url if present, then add port
        url = self.api_url.rstrip('/')
        if ':' in url.split('/')[-1]:
            # URL already has port, use as-is
            return url
        return f"{url}:{self.api_port}"
    
    @property
    def headers(self) -> Dict[str, str]:
        """Get headers for API requests."""
        headers = {"Content-Type": "application/json"}
        if settings.gpu_service_api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        # SGA-M7: Use .get_secret_value() for SecretStr field
        gpu_key = settings.gpu_service_api_key.get_secret_value()
        if gpu_key:
            headers["X-API-Key"] = gpu_key
        return headers
    
    # =========================================================================
    # Installation & Setup
    # =========================================================================
    
    async def is_installed(self) -> bool:
        """Check if ACE-Step is installed."""
        # Check for pyproject.toml (the project uses uv with pyproject.toml)
        return (ACESTEP_DIR / "pyproject.toml").exists()
    
    async def install(self) -> bool:
        """
        Clone and install ACE-Step.
        
        Returns True if successful, False otherwise.
        """
        if await self.is_installed():
            logger.info("ACE-Step is already installed")
            return True
        
        logger.info(f"Cloning ACE-Step to {ACESTEP_DIR}...")
        
        try:
            # Create parent directory
            ACESTEP_DIR.parent.mkdir(parents=True, exist_ok=True)
            
            # Clone repository
            result = subprocess.run(
                ["git", "clone", "--depth=1", ACESTEP_REPO, str(ACESTEP_DIR)],
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            if result.returncode != 0:
                logger.error(f"Git clone failed: {result.stderr}")
                return False
            
            logger.info("ACE-Step cloned successfully")
            
            # Check if uv is available (cross-platform)
            uv_available = shutil.which("uv") is not None
            
            if uv_available:
                # Use uv sync (the recommended way for ACE-Step)
                logger.info("Installing dependencies with uv sync...")
                result = subprocess.run(
                    ["uv", "sync"],
                    cwd=ACESTEP_DIR,
                    capture_output=True,
                    text=True,
                    timeout=900  # 15 minute timeout for first install
                )
                
                if result.returncode != 0:
                    logger.error(f"uv sync failed: {result.stderr}")
                    return False
            else:
                # Fall back to pip with requirements.txt
                logger.warning("uv not available, falling back to pip (slower)")
                logger.info("Installing dependencies with pip...")
                
                # Create venv
                venv_path = ACESTEP_DIR / ".venv"
                result = subprocess.run(
                    [sys.executable, "-m", "venv", str(venv_path)],
                    cwd=ACESTEP_DIR,
                    capture_output=True,
                    text=True
                )
                
                if result.returncode != 0:
                    logger.error(f"venv creation failed: {result.stderr}")
                    return False
                
                # Install requirements (cross-platform venv path)
                if sys.platform == "win32":
                    pip_path = venv_path / "Scripts" / "pip.exe"
                else:
                    pip_path = venv_path / "bin" / "pip"
                result = subprocess.run(
                    [str(pip_path), "install", "-r", "requirements.txt"],
                    cwd=ACESTEP_DIR,
                    capture_output=True,
                    text=True,
                    timeout=900
                )
                
                if result.returncode != 0:
                    logger.error(f"pip install failed: {result.stderr}")
                    return False
            
            logger.info("ACE-Step installed successfully")
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("ACE-Step installation timed out")
            return False
        except Exception as e:
            logger.error(f"ACE-Step installation failed: {e}")
            return False
    
    # =========================================================================
    # Server Management
    # =========================================================================
    
    async def start(self) -> bool:
        """
        Start the ACE-Step API server.
        
        Returns True if started successfully.
        """
        if not await self.is_installed():
            logger.info("ACE-Step not installed, installing now...")
            if not await self.install():
                return False
        
        if await self.health_check():
            logger.info("ACE-Step server is already running")
            return True
        
        logger.info(f"Starting ACE-Step server on port {self.api_port}...")
        
        try:
            # Check if uv is available (cross-platform)
            use_uv = shutil.which("uv") is not None
            
            # Build command based on available tools
            if use_uv:
                # Use uv run acestep-api
                cmd = ["uv", "run", "acestep-api"]
            else:
                # Use venv python directly (cross-platform path)
                if sys.platform == "win32":
                    venv_python = ACESTEP_DIR / ".venv" / "Scripts" / "python.exe"
                else:
                    venv_python = ACESTEP_DIR / ".venv" / "bin" / "python"
                cmd = [str(venv_python), "acestep/api_server.py"]
            
            # Add common options
            cmd.extend(["--port", str(self.api_port)])
            
            # Note: The server auto-selects the best LM model based on GPU VRAM.
            # No tier-specific CLI args needed.
            
            # Add download source if specified
            if self.download_source and self.download_source != "auto":
                cmd.extend(["--download-source", self.download_source])
            
            # Add API key if configured
            if self.api_key:
                cmd.extend(["--api-key", self.api_key])
            
            # Build environment
            env = os.environ.copy()
            # Load both turbo and base models for per-request model selection
            env.setdefault("ACESTEP_CONFIG_PATH", "acestep-v15-turbo")
            env.setdefault("ACESTEP_CONFIG_PATH2", "acestep-v15-base")
            
            logger.info(f"Running command: {' '.join(cmd)}")
            
            # Start process (cross-platform detach)
            popen_kwargs = {
                "cwd": ACESTEP_DIR,
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
            
            # Wait for server to start (models may need to download on first run)
            logger.info("Waiting for ACE-Step server to start (may download models on first run)...")
            for attempt in range(120):  # 2 minute timeout (models may need to download)
                await asyncio.sleep(1)
                if await self.health_check():
                    logger.info("ACE-Step server started successfully")
                    return True
                
                # Check if process died
                if self._process.poll() is not None:
                    stderr = self._process.stderr.read().decode() if self._process.stderr else ""
                    stdout = self._process.stdout.read().decode() if self._process.stdout else ""
                    logger.error(f"ACE-Step server died.\nSTDOUT: {stdout}\nSTDERR: {stderr}")
                    return False
                
                # Log progress every 10 seconds
                if attempt > 0 and attempt % 10 == 0:
                    logger.info(f"Still waiting for ACE-Step server... ({attempt}s)")
            
            logger.error("ACE-Step server failed to start within timeout")
            return False
            
        except Exception as e:
            logger.error(f"Failed to start ACE-Step server: {e}")
            return False
    
    async def stop(self) -> bool:
        """Stop the ACE-Step server."""
        if self._process:
            logger.info("Stopping ACE-Step server...")
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("ACE-Step server did not stop gracefully, killing...")
                self._process.kill()
            self._process = None
            logger.info("ACE-Step server stopped")
            return True
        return False
    
    async def health_check(self) -> bool:
        """Check if ACE-Step server is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                # Try the health endpoint
                response = await client.get(
                    f"{self.base_url}/health",
                    headers=self.headers
                )
                return response.status_code == 200
        except Exception:
            return False
    
    async def get_status(self) -> Dict[str, Any]:
        """Get detailed status of ACE-Step server."""
        is_healthy = await self.health_check()
        
        return {
            "enabled": settings.use_acestep,
            "installed": await self.is_installed(),
            "running": is_healthy,
            "url": self.base_url,
            "model": self.model,
            "max_steps": self.max_steps,
            "default_steps": self.default_steps,
            "install_path": str(ACESTEP_DIR),
        }
    
    # =========================================================================
    # Music Generation
    # =========================================================================
    
    async def generate_music(
        self,
        *,
        lyrics: str = "",
        style: str = "pop",
        duration: float = 60.0,
        steps: Optional[int] = None,
        model: Optional[str] = None,
        instrumental: bool = False,
        temperature: float = 0.95,
        guidance_scale: float = 3.5,
        batch_size: int = 1,
        seed: Optional[int] = None,
        retake: bool = True,
        timeout: int = 300,
    ) -> Dict[str, Any]:
        """
        Generate music using ACE-Step.
        
        Args:
            lyrics: Song lyrics (blank for instrumental)
            style: Music style/genre description
            duration: Duration in seconds (10-600)
            steps: Number of inference steps (None = use default for model)
            model: DiT model variant: "turbo" or "base" (None = use server default)
            instrumental: Generate instrumental only
            temperature: Sampling temperature (0.7-1.5)
            guidance_scale: CFG scale (1.0-10.0)
            batch_size: Number of variations to generate (1-8)
            seed: Random seed for reproducibility
            retake: Whether to regenerate with same seed
            timeout: Maximum wait time in seconds
            
        Returns:
            Dict with task_id, status, audio_urls, etc.
        """
        if not await self.health_check():
            raise ACEStepError("ACE-Step server is not running")
        
        # Resolve model-specific steps defaults and limits
        is_base = (model == "base") if model else (self.model == "base")
        max_steps = 300 if is_base else 8
        default_steps = 45 if is_base else 8
        
        # Auto-adjust timeout for base model (much slower than turbo)
        # Base model docs say ~2-4 min per minute of audio
        if is_base and timeout <= 300:
            # Scale timeout by duration: base needs ~4 min/min of audio + buffer
            estimated_time = max(600, int(duration * 5) + 120)
            timeout = max(timeout, estimated_time)
            logger.info(f"Auto-adjusted timeout to {timeout}s for base model")
        
        if steps is None:
            steps = default_steps
        steps = max(1, min(steps, max_steps))
        
        duration = max(10.0, min(duration, 600.0))  # 10s to 10min
        temperature = max(0.7, min(temperature, 1.5))
        guidance_scale = max(1.0, min(guidance_scale, 10.0))
        batch_size = max(1, min(batch_size, 8))
        
        # Build request payload for ACE-Step API
        # Endpoint: POST /release_task
        # See: https://github.com/ace-step/ACE-Step-1.5
        payload = {
            "prompt": style,
            "lyrics": "" if instrumental else lyrics,
            "audio_duration": duration,
            "inference_steps": steps,
            "guidance_scale": guidance_scale,
            "batch_size": batch_size,
            "use_random_seed": seed is None,
            "seed": seed if seed is not None else -1,
            "audio_format": "mp3",
        }
        
        # Map user-facing model name to ACE-Step config name
        if model:
            model_map = {
                "turbo": "acestep-v15-turbo",
                "base": "acestep-v15-base",
            }
            payload["model"] = model_map.get(model, model)
        
        logger.info(f"Submitting music generation task: style='{style[:50]}...', duration={duration}s, steps={steps}")
        
        try:
            async with httpx.AsyncClient(timeout=timeout + 30) as client:
                # Submit task via /release_task endpoint
                response = await client.post(
                    f"{self.base_url}/release_task",
                    headers=self.headers,
                    json=payload
                )
                
                if response.status_code != 200:
                    error_text = response.text
                    logger.error(f"Task submission failed: {response.status_code} - {error_text}")
                    raise ACEStepError(f"Task submission failed: {response.status_code} - {error_text}")
                
                result = response.json()
                
                # ACE-Step returns task_id for async processing
                task_id = result.get("data", {}).get("task_id") or result.get("task_id")
                
                if not task_id:
                    # Check for direct audio response (rare)
                    if "audio" in result or "audio_urls" in result:
                        audio_urls = result.get("audio_urls", [])
                        if not audio_urls and result.get("audio"):
                            audio_urls = [result["audio"]]
                        return {
                            "success": True,
                            "task_id": "direct",
                            "status": "completed",
                            "audio_urls": audio_urls,
                            "duration": duration,
                            "style": style,
                            "instrumental": instrumental,
                            "steps": steps,
                            "generation_time": 0,
                        }
                    raise ACEStepError(f"No task_id returned: {result}")
                
                logger.info(f"Task submitted: {task_id}")
                
                # Poll for completion via /query_result
                start_time = time.time()
                while time.time() - start_time < timeout:
                    await asyncio.sleep(3)  # Poll every 3 seconds
                    
                    poll_response = await client.post(
                        f"{self.base_url}/query_result",
                        headers=self.headers,
                        json={"task_id_list": [task_id]}  # API expects task_id_list
                    )
                    
                    if poll_response.status_code != 200:
                        continue
                    
                    poll_result = poll_response.json()
                    
                    # ACE-Step response format:
                    # {code: 200, data: [{task_id, result (JSON string), status}]}
                    data_list = poll_result.get("data", [])
                    
                    if not data_list:
                        # Task not ready yet
                        continue
                    
                    task_result = data_list[0]
                    status = task_result.get("status")  # 1 = completed
                    
                    if status == 1:  # Completed
                        # Parse the result JSON string
                        result_str = task_result.get("result", "[]")
                        try:
                            result_data = json.loads(result_str)
                        except json.JSONDecodeError:
                            result_data = []
                        
                        if not result_data:
                            raise ACEStepError("No audio files in result")
                        
                        # Extract audio URLs
                        audio_urls = []
                        for item in result_data:
                            file_url = item.get("file", "")
                            if file_url:
                                # URL is already formatted like /v1/audio?path=...
                                full_url = f"{self.base_url}{file_url}" if file_url.startswith("/") else file_url
                                audio_urls.append(full_url)
                        
                        return {
                            "success": True,
                            "task_id": task_id,
                            "status": "completed",
                            "audio_urls": audio_urls,
                            "duration": duration,
                            "style": style,
                            "instrumental": instrumental,
                            "steps": steps,
                            "generation_time": time.time() - start_time,
                            "metadata": result_data[0].get("metas", {}) if result_data else {},
                            "generation_info": result_data[0].get("generation_info", "") if result_data else "",
                        }
                    
                    elif status == 2:  # Failed
                        # Try to extract error details from the response
                        error_detail = task_result.get("error", "")
                        if error_detail:
                            # Truncate long tracebacks for the error message
                            lines = error_detail.strip().splitlines()
                            short_error = lines[-1] if lines else error_detail
                            logger.error(f"ACE-Step task {task_id} failed: {short_error}")
                            raise ACEStepError(f"Generation failed: {short_error}")
                        raise ACEStepError(f"Generation failed for task {task_id}")
                    
                    # status == 0 means still processing, continue polling
                
                raise ACEStepError(f"Generation timed out after {timeout}s")
                
        except httpx.RequestError as e:
            raise ACEStepError(f"Request failed: {e}")
    
    async def download_audio(self, audio_url: str) -> bytes:
        """Download generated audio file."""
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(audio_url, headers=self.headers)
                if response.status_code == 200:
                    return response.content
                else:
                    raise ACEStepError(f"Failed to download audio: {response.status_code}")
        except httpx.RequestError as e:
            raise ACEStepError(f"Download failed: {e}")


# Global service instance
_acestep_service: Optional[ACEStepService] = None


def get_acestep_service() -> ACEStepService:
    """Get the global ACE-Step service instance."""
    global _acestep_service
    if _acestep_service is None:
        _acestep_service = ACEStepService()
    return _acestep_service


async def ensure_acestep_running() -> bool:
    """Ensure ACE-Step server is running if enabled."""
    if not settings.use_acestep:
        return False
    
    if not settings.acestep_auto_start:
        # Check if external server is running
        service = get_acestep_service()
        return await service.health_check()
    
    service = get_acestep_service()
    return await service.start()
