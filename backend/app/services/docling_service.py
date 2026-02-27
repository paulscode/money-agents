"""
Docling Document Parser Service — Manages the Docling document parser server.

This service handles:
- Setting up the Docling venv (pip install docling etc.)
- Starting/stopping the Docling server
- Health checks and status monitoring
- Dispatching document parsing operations

CPU-only — no GPU required.  Port 8010.
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

# Docling project paths
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent  # /home/…/money-agents
DOCLING_DIR = Path(os.environ.get("DOCLING_DIR", str(PROJECT_ROOT / "docling-parser")))


class DoclingError(Exception):
    """Exception for Docling related errors."""
    pass


class DoclingService:
    """Service for managing the Docling Document Parser local server."""

    def __init__(self):
        self.api_url = settings.docling_api_url
        self.api_port = settings.docling_api_port
        self.auto_start = settings.docling_auto_start
        self._process: Optional[subprocess.Popen] = None

    @property
    def base_url(self) -> str:
        """Get the base URL for Docling API."""
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
        """Check if Docling venv is set up and ready."""
        venv_dir = DOCLING_DIR / ".venv"
        app_file = DOCLING_DIR / "app.py"
        return venv_dir.exists() and app_file.exists()

    async def install(self) -> bool:
        """Set up Docling venv and install dependencies."""
        if await self.is_installed():
            logger.info("Docling is already installed")
            return True

        if not (DOCLING_DIR / "app.py").exists():
            logger.error(f"Docling app.py not found at {DOCLING_DIR}")
            return False

        logger.info(f"Setting up Docling at {DOCLING_DIR}...")

        try:
            venv_dir = DOCLING_DIR / ".venv"
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

            # Install requirements
            requirements_file = DOCLING_DIR / "requirements.txt"
            if requirements_file.exists():
                logger.info("Installing Docling dependencies (this may take a few minutes)...")
                result = subprocess.run(
                    [str(pip_path), "install", "-r", str(requirements_file)],
                    cwd=DOCLING_DIR,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=600,
                )
                if result.returncode != 0:
                    logger.error(f"pip install requirements failed: {result.stderr}")
                    return False

            logger.info("Docling installed successfully")
            return True

        except subprocess.TimeoutExpired:
            logger.error("Docling installation timed out")
            return False
        except Exception as e:
            logger.error(f"Docling installation failed: {e}")
            return False

    # =========================================================================
    # Server Management
    # =========================================================================

    async def start(self) -> bool:
        """Start the Docling server."""
        if not await self.is_installed():
            logger.info("Docling not installed, installing now...")
            if not await self.install():
                return False

        if await self.health_check():
            logger.info("Docling server is already running")
            return True

        logger.info(f"Starting Docling server on port {self.api_port}...")

        try:
            venv_dir = DOCLING_DIR / ".venv"
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
                "cwd": str(DOCLING_DIR),
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

            # Docling model loading can take 30-60s on first run
            logger.info("Waiting for Docling server to start (model loading may take a minute)...")
            for attempt in range(90):  # 90s timeout
                await asyncio.sleep(1)
                if await self.health_check():
                    logger.info("Docling server started successfully")
                    return True

                if self._process.poll() is not None:
                    stderr = self._process.stderr.read().decode() if self._process.stderr else ""
                    stdout = self._process.stdout.read().decode() if self._process.stdout else ""
                    logger.error(f"Docling server died.\nSTDOUT: {stdout}\nSTDERR: {stderr}")
                    return False

            logger.error("Docling server failed to start within timeout")
            return False

        except Exception as e:
            logger.error(f"Failed to start Docling server: {e}")
            return False

    async def stop(self) -> bool:
        """Stop the Docling server."""
        if self._process:
            logger.info("Stopping Docling server...")
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Docling server did not stop gracefully, killing...")
                self._process.kill()
            self._process = None
            logger.info("Docling server stopped")
            return True
        return False

    async def health_check(self) -> bool:
        """Check if Docling server is healthy."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/health", headers=self.headers)
                return response.status_code == 200
        except Exception:
            return False

    async def get_status(self) -> Dict[str, Any]:
        """Get detailed status of Docling server."""
        is_healthy = await self.health_check()

        status = {
            "enabled": settings.use_docling,
            "installed": await self.is_installed(),
            "running": is_healthy,
            "url": self.base_url,
            "install_path": str(DOCLING_DIR),
        }

        if is_healthy:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    response = await client.get(f"{self.base_url}/info")
                    if response.status_code == 200:
                        info = response.json()
                        status.update({
                            "converter_loaded": info.get("converter_loaded"),
                            "supported_input_formats": info.get("supported_input_formats"),
                            "supported_output_formats": info.get("supported_output_formats"),
                        })
            except Exception:
                pass

        return status

    # =========================================================================
    # Parse Operations
    # =========================================================================

    async def parse_document(
        self,
        *,
        file_path: Optional[str] = None,
        url: Optional[str] = None,
        output_format: str = "markdown",
        timeout: int = 300,
    ) -> Dict[str, Any]:
        """
        Parse a document via the Docling server.

        Args:
            file_path: Local path to document file
            url: URL of document to parse
            output_format: Output format (markdown, json, text)
            timeout: Maximum wait time in seconds

        Returns:
            Dict with parsed content, metadata, and timing info
        """
        if not await self.health_check():
            raise DoclingError("Docling server is not running")

        try:
            data = {}
            files = {}

            if file_path:
                from app.core.path_security import validate_tool_file_path
                validated = validate_tool_file_path(file_path, label="file_path")
                files["file"] = open(str(validated), "rb")
            elif url:
                data["url"] = url

            if output_format:
                data["output_format"] = output_format

            async with httpx.AsyncClient(timeout=timeout + 10) as client:
                response = await client.post(
                    f"{self.base_url}/parse",
                    data=data,
                    files=files if files else None,
                )

            if files:
                for f in files.values():
                    f.close()

            if response.status_code != 200:
                error_text = response.text
                logger.error(f"Document parse failed: {response.status_code} - {error_text}")
                raise DoclingError(f"Document parse failed: {response.status_code} - {error_text}")

            result = response.json()

            # Convert relative URL to absolute
            output_file = result.get("output_file")
            if output_file and output_file.startswith("/"):
                result["output_file"] = f"{self.base_url}{output_file}"

            return result

        except httpx.RequestError as e:
            raise DoclingError(f"Request failed: {e}")


# Global service instance
_docling_service: Optional[DoclingService] = None


def get_docling_service() -> DoclingService:
    """Get the global Docling service instance."""
    global _docling_service
    if _docling_service is None:
        _docling_service = DoclingService()
    return _docling_service


async def ensure_docling_running() -> bool:
    """Ensure Docling server is running if enabled."""
    if not settings.use_docling:
        return False

    if not settings.docling_auto_start:
        service = get_docling_service()
        return await service.health_check()

    service = get_docling_service()
    return await service.start()
