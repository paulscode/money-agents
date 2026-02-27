"""
Cross-platform system capabilities detection.

Detects CPU, memory, GPU, storage, and network information
on both Linux and Windows systems.
"""
import os
import platform
import subprocess
import socket
import json
from typing import Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime

import psutil


@dataclass
class CPUInfo:
    """CPU information."""
    cores_physical: int
    cores_logical: int
    model: str
    architecture: str
    frequency_mhz: Optional[float] = None
    
    
@dataclass
class MemoryInfo:
    """Memory information."""
    total_bytes: int
    available_bytes: int
    used_bytes: int
    percent_used: float
    
    @property
    def total_gb(self) -> float:
        return self.total_bytes / (1024**3)
    
    @property
    def available_gb(self) -> float:
        return self.available_bytes / (1024**3)


@dataclass
class GPUInfo:
    """GPU information (NVIDIA)."""
    name: str
    memory_total_mb: int
    memory_free_mb: int
    memory_used_mb: int
    driver_version: str
    cuda_version: Optional[str] = None
    temperature_c: Optional[int] = None
    utilization_percent: Optional[int] = None
    index: int = 0


@dataclass
class StorageInfo:
    """Storage volume information."""
    path: str
    total_bytes: int
    free_bytes: int
    used_bytes: int
    percent_used: float
    filesystem: Optional[str] = None
    
    @property
    def total_gb(self) -> float:
        return self.total_bytes / (1024**3)
    
    @property
    def free_gb(self) -> float:
        return self.free_bytes / (1024**3)


@dataclass
class NetworkInfo:
    """Network information."""
    hostname: str
    ip_address: str
    interfaces: list[dict] = field(default_factory=list)


@dataclass
class PlatformInfo:
    """Operating system information."""
    system: str  # 'Linux', 'Windows', 'Darwin'
    release: str
    version: str
    machine: str  # 'x86_64', 'AMD64', etc.


@dataclass
class OllamaInfo:
    """Ollama local LLM capability information."""
    enabled: bool = False
    base_url: str = ""
    available_models: list[str] = field(default_factory=list)
    model_tiers: dict[str, str] = field(default_factory=dict)  # tier -> model
    context_lengths: dict[str, int] = field(default_factory=dict)  # tier -> context length
    max_concurrent: int = 1


@dataclass
class Capabilities:
    """Complete system capabilities snapshot."""
    cpu: CPUInfo
    memory: MemoryInfo
    gpus: list[GPUInfo]
    storage: list[StorageInfo]
    network: NetworkInfo
    platform: PlatformInfo
    ollama: Optional[OllamaInfo] = None  # Ollama LLM capability
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    @property
    def has_gpu(self) -> bool:
        return len(self.gpus) > 0
    
    @property
    def total_gpu_memory_mb(self) -> int:
        return sum(gpu.memory_total_mb for gpu in self.gpus)
    
    @property
    def has_ollama(self) -> bool:
        """Check if Ollama is enabled and has models available."""
        return self.ollama is not None and self.ollama.enabled and len(self.ollama.available_models) > 0


def get_cpu_info() -> CPUInfo:
    """Get CPU information (cross-platform)."""
    # Get CPU model name
    model = "Unknown"
    if platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo", "r") as f:
                for line in f:
                    if line.startswith("model name"):
                        model = line.split(":")[1].strip()
                        break
        except Exception:
            pass
    elif platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            )
            model = winreg.QueryValueEx(key, "ProcessorNameString")[0]
            winreg.CloseKey(key)
        except Exception:
            pass
    
    # Get frequency
    freq = psutil.cpu_freq()
    frequency = freq.current if freq else None
    
    return CPUInfo(
        cores_physical=psutil.cpu_count(logical=False) or 1,
        cores_logical=psutil.cpu_count(logical=True) or 1,
        model=model,
        architecture=platform.machine(),
        frequency_mhz=frequency
    )


def get_memory_info() -> MemoryInfo:
    """Get memory information (cross-platform)."""
    mem = psutil.virtual_memory()
    return MemoryInfo(
        total_bytes=mem.total,
        available_bytes=mem.available,
        used_bytes=mem.used,
        percent_used=mem.percent
    )


def get_gpu_info() -> list[GPUInfo]:
    """
    Get GPU information using nvidia-smi (cross-platform).
    
    Returns empty list if no NVIDIA GPU or nvidia-smi not found.
    """
    gpus = []
    
    try:
        # nvidia-smi works on both Linux and Windows
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.free,memory.used,driver_version,temperature.gpu,utilization.gpu",
                "--format=csv,noheader,nounits"
            ],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 6:
                    gpus.append(GPUInfo(
                        index=int(parts[0]),
                        name=parts[1],
                        memory_total_mb=int(parts[2]),
                        memory_free_mb=int(parts[3]),
                        memory_used_mb=int(parts[4]),
                        driver_version=parts[5],
                        temperature_c=int(parts[6]) if len(parts) > 6 and parts[6] != "[N/A]" else None,
                        utilization_percent=int(parts[7]) if len(parts) > 7 and parts[7] != "[N/A]" else None
                    ))
            
            # Get CUDA version separately
            cuda_result = subprocess.run(
                ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=5
            )
            # Try to get CUDA version from nvidia-smi output
            try:
                version_result = subprocess.run(
                    ["nvidia-smi"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                for line in version_result.stdout.split("\n"):
                    if "CUDA Version" in line:
                        cuda_ver = line.split("CUDA Version:")[1].strip().split()[0]
                        for gpu in gpus:
                            gpu.cuda_version = cuda_ver
                        break
            except Exception:
                pass
                
    except FileNotFoundError:
        # nvidia-smi not found - no NVIDIA GPU or drivers not installed
        pass
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass
    
    return gpus


def get_storage_info(include_paths: Optional[list[str]] = None) -> list[StorageInfo]:
    """
    Get storage information for mounted volumes.
    
    Args:
        include_paths: Optional list of specific paths to check.
                      If None, returns all mounted partitions.
    """
    storage = []
    
    if include_paths:
        # Check specific paths
        for path in include_paths:
            if os.path.exists(path):
                try:
                    usage = psutil.disk_usage(path)
                    storage.append(StorageInfo(
                        path=path,
                        total_bytes=usage.total,
                        free_bytes=usage.free,
                        used_bytes=usage.used,
                        percent_used=usage.percent
                    ))
                except Exception:
                    pass
    else:
        # Get all mounted partitions
        seen_devices = set()
        for part in psutil.disk_partitions(all=False):
            # Skip duplicates (same device mounted multiple times)
            if part.device in seen_devices:
                continue
            seen_devices.add(part.device)
            
            # Skip special filesystems
            if part.fstype in ("squashfs", "tmpfs", "devtmpfs", "overlay"):
                continue
            
            try:
                usage = psutil.disk_usage(part.mountpoint)
                storage.append(StorageInfo(
                    path=part.mountpoint,
                    total_bytes=usage.total,
                    free_bytes=usage.free,
                    used_bytes=usage.used,
                    percent_used=usage.percent,
                    filesystem=part.fstype
                ))
            except (PermissionError, OSError):
                pass
    
    return storage


def get_network_info() -> NetworkInfo:
    """Get network information."""
    hostname = socket.gethostname()
    
    # Try to get IP address
    ip_address = "127.0.0.1"
    try:
        # Connect to a public DNS to find outbound IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip_address = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    
    # Get interface details
    interfaces = []
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        for name, addr_list in addrs.items():
            if name in stats and stats[name].isup:
                iface = {"name": name, "addresses": []}
                for addr in addr_list:
                    if addr.family == socket.AF_INET:
                        iface["addresses"].append({
                            "type": "ipv4",
                            "address": addr.address
                        })
                if iface["addresses"]:
                    interfaces.append(iface)
    except Exception:
        pass
    
    return NetworkInfo(
        hostname=hostname,
        ip_address=ip_address,
        interfaces=interfaces
    )


def get_platform_info() -> PlatformInfo:
    """Get operating system information."""
    return PlatformInfo(
        system=platform.system(),
        release=platform.release(),
        version=platform.version(),
        machine=platform.machine()
    )


def get_ollama_info(config=None) -> Optional[OllamaInfo]:
    """
    Get Ollama capability information.
    
    Checks if Ollama is configured and running, and fetches available models.
    
    Args:
        config: CampaignWorkerConfig with Ollama settings. If None, returns None.
    
    Returns:
        OllamaInfo if Ollama is enabled and reachable, None otherwise.
    """
    if config is None or not config.use_ollama:
        return None
    
    import httpx
    
    base_url = config.ollama_base_url.rstrip('/')
    available_models = []
    
    try:
        # Check if Ollama is running and get available models
        response = httpx.get(f"{base_url}/api/tags", timeout=5.0)
        if response.status_code == 200:
            data = response.json()
            available_models = [m.get('name', '') for m in data.get('models', [])]
    except Exception as e:
        # Ollama not reachable - still report as configured but with empty models
        pass
    
    return OllamaInfo(
        enabled=True,
        base_url=base_url,
        available_models=available_models,
        model_tiers=config.ollama_model_tiers_dict,
        context_lengths=config.ollama_context_lengths_dict,
        max_concurrent=config.ollama_max_concurrent,
    )


def detect_capabilities(storage_paths: Optional[list[str]] = None, config=None) -> Capabilities:
    """
    Detect all system capabilities.
    
    Args:
        storage_paths: Optional list of storage paths to include.
                      If None, auto-detects all mounted volumes.
        config: CampaignWorkerConfig for Ollama detection.
    
    Returns:
        Complete capabilities snapshot.
    """
    return Capabilities(
        cpu=get_cpu_info(),
        memory=get_memory_info(),
        gpus=get_gpu_info(),
        storage=get_storage_info(storage_paths),
        network=get_network_info(),
        platform=get_platform_info(),
        ollama=get_ollama_info(config),
    )


def get_live_stats() -> dict:
    """
    Get current live statistics (for heartbeat updates).
    
    Returns frequently changing values like memory/GPU usage.
    """
    stats = {
        "timestamp": datetime.utcnow().isoformat(),
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory": {
            "available_bytes": psutil.virtual_memory().available,
            "percent_used": psutil.virtual_memory().percent
        }
    }
    
    # Get GPU stats if available
    gpus = get_gpu_info()
    if gpus:
        stats["gpus"] = [
            {
                "index": gpu.index,
                "memory_free_mb": gpu.memory_free_mb,
                "memory_used_mb": gpu.memory_used_mb,
                "utilization_percent": gpu.utilization_percent,
                "temperature_c": gpu.temperature_c
            }
            for gpu in gpus
        ]
    
    return stats


if __name__ == "__main__":
    # Test capabilities detection
    print("Detecting system capabilities...")
    caps = detect_capabilities()
    print(json.dumps(caps.to_dict(), indent=2))
    print(f"\nHas GPU: {caps.has_gpu}")
    if caps.has_gpu:
        print(f"Total GPU Memory: {caps.total_gpu_memory_mb} MB")
