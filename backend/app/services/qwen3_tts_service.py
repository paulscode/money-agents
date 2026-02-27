"""
Qwen3-TTS Service - Manages the Qwen3-TTS local voice generation server.

This service handles:
- Setting up Qwen3-TTS with its own venv
- Starting/stopping the Qwen3-TTS server
- Health checks and status monitoring
- Voice generation (custom voice, clone, design)
- Voice sample management

Qwen3-TTS: https://github.com/QwenLM/Qwen3-TTS
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
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Qwen3-TTS project paths - standalone server in project root's qwen3-tts/ directory
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent  # project root
QWEN3_TTS_DIR = Path(os.environ.get("QWEN3_TTS_DIR", str(PROJECT_ROOT / "qwen3-tts")))


class Qwen3TTSError(Exception):
    """Exception for Qwen3-TTS related errors."""
    pass


class Qwen3TTSService:
    """Service for managing Qwen3-TTS local voice generation."""
    
    def __init__(self):
        self.api_url = settings.qwen3_tts_api_url
        self.api_port = settings.qwen3_tts_api_port
        self.tier = settings.qwen3_tts_tier
        self.auto_start = settings.qwen3_tts_auto_start
        self.idle_timeout = settings.qwen3_tts_idle_timeout
        self._process: Optional[subprocess.Popen] = None
    
    @property
    def base_url(self) -> str:
        """Get the base URL for Qwen3-TTS API."""
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
        """Check if Qwen3-TTS venv is set up and ready."""
        venv_dir = QWEN3_TTS_DIR / ".venv"
        app_file = QWEN3_TTS_DIR / "app.py"
        return venv_dir.exists() and app_file.exists()
    
    async def install(self) -> bool:
        """
        Set up Qwen3-TTS venv and install dependencies.
        
        Returns True if successful, False otherwise.
        """
        if await self.is_installed():
            logger.info("Qwen3-TTS is already installed")
            return True
        
        if not (QWEN3_TTS_DIR / "app.py").exists():
            logger.error(f"Qwen3-TTS app.py not found at {QWEN3_TTS_DIR}")
            return False
        
        logger.info(f"Setting up Qwen3-TTS at {QWEN3_TTS_DIR}...")
        
        try:
            venv_dir = QWEN3_TTS_DIR / ".venv"
            
            # Create venv
            logger.info("Creating Python venv...")
            result = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                capture_output=True,
                text=True,
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
            
            requirements_file = QWEN3_TTS_DIR / "requirements.txt"
            if not requirements_file.exists():
                logger.error("requirements.txt not found")
                return False
            
            logger.info("Installing Qwen3-TTS dependencies (this may take several minutes)...")
            result = subprocess.run(
                [str(pip_path), "install", "-r", str(requirements_file)],
                cwd=QWEN3_TTS_DIR,
                capture_output=True,
                text=True,
                timeout=1800  # 30 minute timeout (torch is large)
            )
            
            if result.returncode != 0:
                logger.error(f"pip install failed: {result.stderr}")
                return False
            
            logger.info("Qwen3-TTS installed successfully")
            return True
            
        except subprocess.TimeoutExpired:
            logger.error("Qwen3-TTS installation timed out")
            return False
        except Exception as e:
            logger.error(f"Qwen3-TTS installation failed: {e}")
            return False
    
    # =========================================================================
    # Server Management
    # =========================================================================
    
    async def start(self) -> bool:
        """
        Start the Qwen3-TTS server.
        
        Returns True if started successfully.
        """
        if not await self.is_installed():
            logger.info("Qwen3-TTS not installed, installing now...")
            if not await self.install():
                return False
        
        if await self.health_check():
            logger.info("Qwen3-TTS server is already running")
            return True
        
        logger.info(f"Starting Qwen3-TTS server on port {self.api_port}...")
        
        try:
            venv_dir = QWEN3_TTS_DIR / ".venv"
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
            
            # Build environment
            env = os.environ.copy()
            
            # Pass tier and idle timeout via environment or let config.yaml handle it
            # The app.py reads config.yaml, but we can override via CLI
            if self.tier and self.tier != "auto":
                # Override tier: modify the config at startup
                pass  # Uses config.yaml which has the tier setting
            
            logger.info(f"Running command: {' '.join(cmd)}")
            
            # Start process
            popen_kwargs = {
                "cwd": str(QWEN3_TTS_DIR),
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
            logger.info("Waiting for Qwen3-TTS server to start (may download models on first run)...")
            for attempt in range(180):  # 3 minute timeout (model download can be slow)
                await asyncio.sleep(1)
                if await self.health_check():
                    logger.info("Qwen3-TTS server started successfully")
                    return True
                
                # Check if process died
                if self._process.poll() is not None:
                    stderr = self._process.stderr.read().decode() if self._process.stderr else ""
                    stdout = self._process.stdout.read().decode() if self._process.stdout else ""
                    logger.error(f"Qwen3-TTS server died.\nSTDOUT: {stdout}\nSTDERR: {stderr}")
                    return False
                
                if attempt > 0 and attempt % 15 == 0:
                    logger.info(f"Still waiting for Qwen3-TTS server... ({attempt}s)")
            
            logger.error("Qwen3-TTS server failed to start within timeout")
            return False
            
        except Exception as e:
            logger.error(f"Failed to start Qwen3-TTS server: {e}")
            return False
    
    async def stop(self) -> bool:
        """Stop the Qwen3-TTS server."""
        if self._process:
            logger.info("Stopping Qwen3-TTS server...")
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Qwen3-TTS server did not stop gracefully, killing...")
                self._process.kill()
            self._process = None
            logger.info("Qwen3-TTS server stopped")
            return True
        return False
    
    async def health_check(self) -> bool:
        """Check if Qwen3-TTS server is healthy."""
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
        """Get detailed status of Qwen3-TTS server."""
        is_healthy = await self.health_check()
        
        status = {
            "enabled": settings.use_qwen3_tts,
            "installed": await self.is_installed(),
            "running": is_healthy,
            "url": self.base_url,
            "tier": self.tier,
            "install_path": str(QWEN3_TTS_DIR),
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
                            "model_tier": info.get("model_tier"),
                            "capabilities": info.get("capabilities", []),
                            "device": info.get("device"),
                            "gpu": info.get("gpu"),
                        })
            except Exception:
                pass
        
        return status
    
    # =========================================================================
    # Voice Generation
    # =========================================================================
    
    async def generate_speech(
        self,
        *,
        text: str,
        mode: str = "custom_voice",
        voice: Optional[str] = None,
        instruct: Optional[str] = None,
        reference_audio: Optional[str] = None,
        reference_text: Optional[str] = None,
        voice_description: Optional[str] = None,
        output_format: str = "wav",
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """
        Generate speech using Qwen3-TTS.
        
        Args:
            text: Text to convert to speech
            mode: Generation mode (custom_voice, voice_clone, voice_design, voice_design_clone)
            voice: Built-in voice name (for custom_voice mode)
            instruct: Voice instruction (for custom_voice mode)
            reference_audio: Uploaded voice filename (for voice_clone mode)
            reference_text: Transcript of reference audio (improves clone quality)
            voice_description: Text description of desired voice (for voice_design modes)
            output_format: Output format (wav or mp3)
            timeout: Maximum wait time in seconds
            
        Returns:
            Dict with success, audio_url, duration, etc.
        """
        if not await self.health_check():
            raise Qwen3TTSError("Qwen3-TTS server is not running")
        
        # Build request payload
        payload = {
            "text": text,
            "mode": mode,
            "output_format": output_format,
        }
        
        if voice:
            payload["voice"] = voice
        if instruct:
            payload["instruct"] = instruct
        if reference_audio:
            payload["reference_audio"] = reference_audio
        if reference_text:
            payload["reference_text"] = reference_text
        if voice_description:
            payload["voice_description"] = voice_description
        
        logger.info(f"Generating speech: mode={mode}, text='{text[:50]}...'")
        
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
                    raise Qwen3TTSError(f"Generation failed: {response.status_code} - {error_text}")
                
                result = response.json()
                
                # Convert relative audio_url to absolute
                audio_url = result.get("audio_url", "")
                if audio_url.startswith("/"):
                    audio_url = f"{self.base_url}{audio_url}"
                
                return {
                    "success": True,
                    "mode": result.get("mode", mode),
                    "audio_url": audio_url,
                    "duration_seconds": result.get("duration_seconds", 0),
                    "sample_rate": result.get("sample_rate", 24000),
                    "generation_time_seconds": result.get("generation_time_seconds", 0),
                    "model_tier": result.get("model_tier", "unknown"),
                }
                
        except httpx.RequestError as e:
            raise Qwen3TTSError(f"Request failed: {e}")
    
    async def upload_voice(
        self,
        audio_data: bytes,
        filename: str,
        name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload a voice sample for cloning."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                files = {"file": (filename, audio_data)}
                params = {}
                if name:
                    params["name"] = name
                
                response = await client.post(
                    f"{self.base_url}/upload_voice",
                    files=files,
                    params=params,
                )
                
                if response.status_code != 200:
                    raise Qwen3TTSError(f"Upload failed: {response.status_code} - {response.text}")
                
                return response.json()
                
        except httpx.RequestError as e:
            raise Qwen3TTSError(f"Upload failed: {e}")
    
    async def list_voices(self) -> Dict[str, Any]:
        """List available voices (built-in + uploaded)."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(
                    f"{self.base_url}/voices",
                    headers=self.headers,
                )
                
                if response.status_code != 200:
                    raise Qwen3TTSError(f"List voices failed: {response.status_code}")
                
                return response.json()
                
        except httpx.RequestError as e:
            raise Qwen3TTSError(f"Request failed: {e}")
    
    async def download_audio(self, audio_url: str) -> bytes:
        """Download generated audio file."""
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.get(audio_url, headers=self.headers)
                if response.status_code == 200:
                    return response.content
                else:
                    raise Qwen3TTSError(f"Failed to download audio: {response.status_code}")
        except httpx.RequestError as e:
            raise Qwen3TTSError(f"Download failed: {e}")


# Global service instance
_qwen3_tts_service: Optional[Qwen3TTSService] = None


def get_qwen3_tts_service() -> Qwen3TTSService:
    """Get the global Qwen3-TTS service instance."""
    global _qwen3_tts_service
    if _qwen3_tts_service is None:
        _qwen3_tts_service = Qwen3TTSService()
    return _qwen3_tts_service


async def ensure_qwen3_tts_running() -> bool:
    """Ensure Qwen3-TTS server is running if enabled."""
    if not settings.use_qwen3_tts:
        return False
    
    if not settings.qwen3_tts_auto_start:
        # Check if external server is running
        service = get_qwen3_tts_service()
        return await service.health_check()
    
    service = get_qwen3_tts_service()
    return await service.start()
