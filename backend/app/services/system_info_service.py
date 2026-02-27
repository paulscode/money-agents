"""
System Information Service - collects host system details for agent context.

Provides information about the system environment to help agents make
better decisions about tool compatibility and recommendations.
"""
import logging
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class GPUInfo:
    """GPU information."""
    name: str
    memory_mb: Optional[int] = None
    driver_version: Optional[str] = None
    cuda_version: Optional[str] = None


@dataclass
class SystemInfo:
    """Complete system information."""
    # OS Info
    os_name: str  # Linux, Windows, Darwin
    os_version: str  # e.g., "22.2" for Mint
    os_release: str  # e.g., "6.8.0-51-generic"
    os_pretty_name: str  # e.g., "Linux Mint 22.2"
    architecture: str  # x86_64, arm64, etc.
    
    # Hardware
    cpu_model: str
    cpu_cores: int
    cpu_threads: int
    cpu_freq_mhz: Optional[float]
    ram_total_gb: float
    ram_available_gb: float
    disk_total_gb: float
    disk_free_gb: float
    
    # GPU
    gpus: List[GPUInfo] = field(default_factory=list)
    has_nvidia: bool = False
    has_cuda: bool = False
    
    # Software Environment
    python_version: str = ""
    docker_available: bool = False
    docker_version: Optional[str] = None
    
    # Network
    has_internet: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "os": {
                "name": self.os_name,
                "version": self.os_version,
                "release": self.os_release,
                "pretty_name": self.os_pretty_name,
                "architecture": self.architecture,
            },
            "hardware": {
                "cpu_model": self.cpu_model,
                "cpu_cores": self.cpu_cores,
                "cpu_threads": self.cpu_threads,
                "cpu_freq_mhz": self.cpu_freq_mhz,
                "ram_total_gb": self.ram_total_gb,
                "ram_available_gb": self.ram_available_gb,
                "disk_total_gb": self.disk_total_gb,
                "disk_free_gb": self.disk_free_gb,
            },
            "gpu": {
                "gpus": [
                    {
                        "name": g.name,
                        "memory_mb": g.memory_mb,
                        "driver_version": g.driver_version,
                        "cuda_version": g.cuda_version,
                    }
                    for g in self.gpus
                ],
                "has_nvidia": self.has_nvidia,
                "has_cuda": self.has_cuda,
            },
            "software": {
                "python_version": self.python_version,
                "docker_available": self.docker_available,
                "docker_version": self.docker_version,
            },
            "network": {
                "has_internet": self.has_internet,
            },
        }
    
    def format_for_prompt(self) -> str:
        """Format system info for LLM prompt context."""
        lines = [
            "## System Environment",
            "",
            f"**Operating System:** {self.os_pretty_name}",
            f"**Architecture:** {self.architecture}",
            f"**Kernel:** {self.os_release}",
            "",
            "### Hardware",
            f"- **CPU:** {self.cpu_model}",
            f"- **Cores/Threads:** {self.cpu_cores} cores / {self.cpu_threads} threads",
        ]
        
        if self.cpu_freq_mhz:
            lines.append(f"- **CPU Frequency:** {self.cpu_freq_mhz:.0f} MHz")
        
        lines.extend([
            f"- **RAM:** {self.ram_total_gb:.1f} GB total ({self.ram_available_gb:.1f} GB available)",
            f"- **Disk:** {self.disk_total_gb:.0f} GB total ({self.disk_free_gb:.0f} GB free)",
        ])
        
        # GPU info
        if self.gpus:
            lines.append("")
            lines.append("### GPU")
            for gpu in self.gpus:
                gpu_line = f"- **{gpu.name}**"
                if gpu.memory_mb:
                    gpu_line += f" ({gpu.memory_mb} MB)"
                lines.append(gpu_line)
            if self.has_cuda:
                cuda_ver = self.gpus[0].cuda_version if self.gpus and self.gpus[0].cuda_version else "available"
                lines.append(f"- **CUDA:** {cuda_ver}")
        else:
            lines.append("")
            lines.append("### GPU")
            lines.append("- No dedicated GPU detected (CPU-only system)")
        
        # Software
        lines.extend([
            "",
            "### Software Environment",
            f"- **Python:** {self.python_version}",
            f"- **Docker:** {'Available' if self.docker_available else 'Not available'}" + 
                (f" (v{self.docker_version})" if self.docker_version else ""),
        ])
        
        return "\n".join(lines)


class SystemInfoService:
    """Service for collecting system information."""
    
    _cached_info: Optional[SystemInfo] = None
    _cache_ttl_seconds: int = 300  # 5 minutes
    _last_collected: Optional[float] = None
    
    @classmethod
    def collect(cls, force_refresh: bool = False) -> SystemInfo:
        """
        Collect system information.
        
        Uses caching to avoid repeated system calls.
        """
        import time
        
        now = time.time()
        if (
            not force_refresh
            and cls._cached_info is not None
            and cls._last_collected is not None
            and (now - cls._last_collected) < cls._cache_ttl_seconds
        ):
            return cls._cached_info
        
        info = cls._collect_info()
        cls._cached_info = info
        cls._last_collected = now
        return info
    
    @classmethod
    def _collect_info(cls) -> SystemInfo:
        """Collect all system information."""
        return SystemInfo(
            # OS
            os_name=platform.system(),
            os_version=cls._get_os_version(),
            os_release=platform.release(),
            os_pretty_name=cls._get_pretty_name(),
            architecture=platform.machine(),
            
            # CPU
            cpu_model=cls._get_cpu_model(),
            cpu_cores=os.cpu_count() or 1,
            cpu_threads=cls._get_cpu_threads(),
            cpu_freq_mhz=cls._get_cpu_freq(),
            
            # Memory
            ram_total_gb=cls._get_ram_total(),
            ram_available_gb=cls._get_ram_available(),
            
            # Disk
            disk_total_gb=cls._get_disk_total(),
            disk_free_gb=cls._get_disk_free(),
            
            # GPU
            gpus=cls._get_gpus(),
            has_nvidia=cls._has_nvidia(),
            has_cuda=cls._has_cuda(),
            
            # Software
            python_version=platform.python_version(),
            docker_available=cls._has_docker(),
            docker_version=cls._get_docker_version(),
            
            # Network
            has_internet=True,  # Assume true for now
        )
    
    @classmethod
    def _get_os_version(cls) -> str:
        """Get OS version."""
        if platform.system() == "Linux":
            try:
                with open("/etc/os-release") as f:
                    for line in f:
                        if line.startswith("VERSION_ID="):
                            return line.split("=")[1].strip().strip('"')
            except Exception:
                pass
        return platform.version()
    
    @classmethod
    def _get_pretty_name(cls) -> str:
        """Get pretty OS name."""
        if platform.system() == "Linux":
            try:
                with open("/etc/os-release") as f:
                    for line in f:
                        if line.startswith("PRETTY_NAME="):
                            return line.split("=", 1)[1].strip().strip('"')
            except Exception:
                pass
        elif platform.system() == "Darwin":
            try:
                result = subprocess.run(
                    ["sw_vers", "-productName"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                name = result.stdout.strip()
                result = subprocess.run(
                    ["sw_vers", "-productVersion"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                version = result.stdout.strip()
                return f"{name} {version}"
            except Exception:
                pass
        return f"{platform.system()} {platform.release()}"
    
    @classmethod
    def _get_cpu_model(cls) -> str:
        """Get CPU model name."""
        if platform.system() == "Linux":
            try:
                with open("/proc/cpuinfo") as f:
                    for line in f:
                        if line.startswith("model name"):
                            return line.split(":")[1].strip()
            except Exception:
                pass
        elif platform.system() == "Darwin":
            try:
                result = subprocess.run(
                    ["sysctl", "-n", "machdep.cpu.brand_string"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                return result.stdout.strip()
            except Exception:
                pass
        return platform.processor() or "Unknown"
    
    @classmethod
    def _get_cpu_threads(cls) -> int:
        """Get number of CPU threads."""
        try:
            # Try psutil first if available
            import psutil
            return psutil.cpu_count(logical=True) or os.cpu_count() or 1
        except ImportError:
            pass
        
        if platform.system() == "Linux":
            try:
                with open("/proc/cpuinfo") as f:
                    return sum(1 for line in f if line.startswith("processor"))
            except Exception:
                pass
        
        return os.cpu_count() or 1
    
    @classmethod
    def _get_cpu_freq(cls) -> Optional[float]:
        """Get CPU frequency in MHz."""
        try:
            import psutil
            freq = psutil.cpu_freq()
            if freq:
                return freq.current
        except ImportError:
            pass
        
        if platform.system() == "Linux":
            try:
                with open("/proc/cpuinfo") as f:
                    for line in f:
                        if line.startswith("cpu MHz"):
                            return float(line.split(":")[1].strip())
            except Exception:
                pass
        
        return None
    
    @classmethod
    def _get_ram_total(cls) -> float:
        """Get total RAM in GB."""
        try:
            import psutil
            return psutil.virtual_memory().total / (1024 ** 3)
        except ImportError:
            pass
        
        if platform.system() == "Linux":
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal"):
                            kb = int(line.split()[1])
                            return kb / (1024 ** 2)
            except Exception:
                pass
        
        return 0.0
    
    @classmethod
    def _get_ram_available(cls) -> float:
        """Get available RAM in GB."""
        try:
            import psutil
            return psutil.virtual_memory().available / (1024 ** 3)
        except ImportError:
            pass
        
        if platform.system() == "Linux":
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemAvailable"):
                            kb = int(line.split()[1])
                            return kb / (1024 ** 2)
            except Exception:
                pass
        
        return 0.0
    
    @classmethod
    def _get_disk_total(cls) -> float:
        """Get total disk space in GB."""
        try:
            total, _, _ = shutil.disk_usage("/")
            return total / (1024 ** 3)
        except Exception:
            return 0.0
    
    @classmethod
    def _get_disk_free(cls) -> float:
        """Get free disk space in GB."""
        try:
            _, _, free = shutil.disk_usage("/")
            return free / (1024 ** 3)
        except Exception:
            return 0.0
    
    @classmethod
    def _get_gpus(cls) -> List[GPUInfo]:
        """Get GPU information."""
        gpus = []
        
        # Try nvidia-smi for NVIDIA GPUs
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if line:
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) >= 3:
                            gpus.append(GPUInfo(
                                name=parts[0],
                                memory_mb=int(float(parts[1])) if parts[1] else None,
                                driver_version=parts[2] if parts[2] else None,
                                cuda_version=cls._get_cuda_version(),
                            ))
        except Exception:
            pass
        
        # Try lspci for other GPUs if no NVIDIA found
        if not gpus and platform.system() == "Linux":
            try:
                result = subprocess.run(
                    ["lspci"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    for line in result.stdout.split("\n"):
                        if "VGA" in line or "3D" in line or "Display" in line:
                            # Extract GPU name from lspci output
                            parts = line.split(": ", 1)
                            if len(parts) > 1:
                                gpus.append(GPUInfo(name=parts[1].strip()))
            except Exception:
                pass
        
        return gpus
    
    @classmethod
    def _has_nvidia(cls) -> bool:
        """Check if NVIDIA GPU is available."""
        try:
            result = subprocess.run(
                ["nvidia-smi"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False
    
    @classmethod
    def _has_cuda(cls) -> bool:
        """Check if CUDA is available."""
        try:
            result = subprocess.run(
                ["nvcc", "--version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            pass
        
        # Check for CUDA libraries
        cuda_paths = [
            "/usr/local/cuda",
            "/opt/cuda",
        ]
        return any(os.path.exists(p) for p in cuda_paths)
    
    @classmethod
    def _get_cuda_version(cls) -> Optional[str]:
        """Get CUDA version."""
        try:
            result = subprocess.run(
                ["nvcc", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if "release" in line.lower():
                        # Extract version like "12.1" from "Cuda compilation tools, release 12.1, V12.1.105"
                        import re
                        match = re.search(r"release (\d+\.\d+)", line)
                        if match:
                            return match.group(1)
        except Exception:
            pass
        return None
    
    @classmethod
    def _has_docker(cls) -> bool:
        """Check if Docker is available."""
        try:
            result = subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False
    
    @classmethod
    def _get_docker_version(cls) -> Optional[str]:
        """Get Docker version."""
        try:
            result = subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                # Parse "Docker version 24.0.5, build ..."
                import re
                match = re.search(r"version (\d+\.\d+\.\d+)", result.stdout)
                if match:
                    return match.group(1)
        except Exception:
            pass
        return None


# Singleton instance for convenience
system_info_service = SystemInfoService()
