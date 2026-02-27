"""
Cross-platform utilities for Money Agents host-side scripts.

Used by start.py, add-comfy-api.py, and other host-side tools.
Provides OS-aware helpers for venv paths, process management,
executable discovery, and more.
"""
import os
import sys
import shutil
import signal
import socket
import subprocess
import platform
from pathlib import Path
from typing import Optional, List, Tuple


IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


# =============================================================================
# System Info Helpers
# =============================================================================

def get_os_description() -> str:
    """Return a human-readable OS description string."""
    if IS_WINDOWS:
        ver = platform.version()
        return f"Windows {platform.release()} ({ver})"
    elif IS_MACOS:
        ver = platform.mac_ver()[0]
        arch = platform.machine()
        chip = "Apple Silicon" if arch == "arm64" else "Intel"
        return f"macOS {ver} ({chip})"
    else:
        # Try to get distro info
        try:
            import distro  # type: ignore
            return f"{distro.name()} {distro.version()} ({platform.machine()})"
        except ImportError:
            pass
        # Fallback: /etc/os-release
        try:
            with open("/etc/os-release") as f:
                info = {}
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        info[k] = v.strip('"')
                name = info.get("PRETTY_NAME", info.get("NAME", "Linux"))
                return f"{name} ({platform.machine()})"
        except Exception:
            return f"Linux ({platform.machine()})"


def get_disk_free_gb(path: str = ".") -> float:
    """Return free disk space in GB for the given path."""
    try:
        usage = shutil.disk_usage(path)
        return usage.free / (1024 ** 3)
    except Exception:
        return -1.0


def get_total_ram_gb() -> float:
    """Return total system RAM in GB."""
    try:
        if IS_WINDOWS:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullTotalPhys / (1024 ** 3)
        else:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / (1024 ** 2)
    except Exception:
        pass
    # macOS: use sysctl (no /proc/meminfo)
    if IS_MACOS:
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return int(result.stdout.strip()) / (1024 ** 3)
        except Exception:
            pass
    return -1.0


def check_docker_installed() -> Tuple[bool, str]:
    """
    Check if the Docker CLI is installed.
    Returns (installed: bool, version_or_error: str).
    """
    docker_path = find_executable("docker")
    if not docker_path:
        return False, "docker command not found"
    try:
        result = subprocess.run(
            ["docker", "--version"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip()
    except Exception as e:
        return False, str(e)


def check_docker_compose_installed() -> Tuple[bool, str]:
    """
    Check if Docker Compose V2 (plugin) is available.
    Returns (installed: bool, version_or_error: str).
    """
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
    except Exception:
        pass
    # Fallback: check for standalone docker-compose
    if find_executable("docker-compose"):
        try:
            result = subprocess.run(
                ["docker-compose", "--version"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                return False, f"Found standalone docker-compose ({result.stdout.strip()}) but 'docker compose' (V2) is required"
        except Exception:
            pass
    return False, "docker compose command not found"


def check_docker_daemon_running() -> Tuple[bool, str]:
    """
    Check if the Docker daemon is running.
    Returns (running: bool, detail: str).
    """
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            return True, "Docker daemon is running"
        # Parse common error messages
        err = result.stderr.lower()
        if "permission denied" in err or "connect: permission denied" in err:
            return False, "permission_denied"
        if "connection refused" in err or "cannot connect" in err:
            return False, "not_running"
        return False, result.stderr.strip()
    except FileNotFoundError:
        return False, "docker not installed"
    except Exception as e:
        return False, str(e)


def try_start_docker() -> bool:
    """
    Attempt to start the Docker daemon.
    Returns True if Docker appears to have started successfully.
    """
    import time
    try:
        if IS_MACOS:
            # Try opening Docker Desktop
            subprocess.run(["open", "-a", "Docker"], capture_output=True, timeout=10)
        elif IS_LINUX:
            # Try systemctl
            subprocess.run(
                ["sudo", "systemctl", "start", "docker"],
                capture_output=True, timeout=30
            )
        elif IS_WINDOWS:
            # Try starting Docker Desktop via its path
            docker_desktop = shutil.which("Docker Desktop")
            if not docker_desktop:
                # Common install locations
                for path in [
                    r"C:\Program Files\Docker\Docker\Docker Desktop.exe",
                    os.path.expandvars(r"%ProgramFiles%\Docker\Docker\Docker Desktop.exe"),
                ]:
                    if os.path.exists(path):
                        docker_desktop = path
                        break
            if docker_desktop:
                subprocess.Popen(
                    [docker_desktop],
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
                )
        else:
            return False

        # Wait for Docker to be ready (up to 30 seconds)
        for _ in range(15):
            time.sleep(2)
            running, _ = check_docker_daemon_running()
            if running:
                return True
    except Exception:
        pass
    return False


def check_git_installed() -> Tuple[bool, str]:
    """Check if git is installed. Returns (installed, version_or_error)."""
    git_path = find_executable("git")
    if not git_path:
        return False, "git command not found"
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip()
    except Exception as e:
        return False, str(e)


def check_python_version(min_major: int = 3, min_minor: int = 10) -> Tuple[bool, str]:
    """
    Check if the current Python version meets the minimum requirement.
    Returns (meets_requirement, version_string).
    """
    ver = sys.version_info
    version_str = f"Python {ver.major}.{ver.minor}.{ver.micro}"
    if ver.major > min_major or (ver.major == min_major and ver.minor >= min_minor):
        return True, version_str
    return False, f"{version_str} (requires {min_major}.{min_minor}+)"


def check_ports_available(ports: List[int]) -> List[Tuple[int, bool]]:
    """
    Check if the given ports are available.
    Returns list of (port, is_available) tuples.
    """
    results = []
    for port in ports:
        results.append((port, not is_port_in_use(port)))
    return results


def get_docker_install_url() -> str:
    """Return the appropriate Docker install URL for the current OS."""
    if IS_WINDOWS:
        return "https://docs.docker.com/desktop/setup/install/windows-install/"
    elif IS_MACOS:
        return "https://docs.docker.com/desktop/setup/install/mac-install/"
    else:
        return "https://docs.docker.com/engine/install/"


def get_git_install_url() -> str:
    """Return the appropriate Git install URL for the current OS."""
    if IS_WINDOWS:
        return "https://git-scm.com/download/win"
    elif IS_MACOS:
        return "https://git-scm.com/download/mac"
    else:
        return "https://git-scm.com/download/linux"


# =============================================================================
# Virtual Environment Helpers
# =============================================================================

def get_venv_bin_dir(venv_path: Path) -> Path:
    """Get the bin/ (Unix) or Scripts/ (Windows) directory inside a venv."""
    if IS_WINDOWS:
        return venv_path / "Scripts"
    return venv_path / "bin"


def get_venv_python(venv_path: Path) -> Path:
    """Get path to the Python executable inside a venv."""
    bin_dir = get_venv_bin_dir(venv_path)
    if IS_WINDOWS:
        return bin_dir / "python.exe"
    return bin_dir / "python"


def get_venv_pip(venv_path: Path) -> Path:
    """Get path to the pip executable inside a venv."""
    bin_dir = get_venv_bin_dir(venv_path)
    if IS_WINDOWS:
        return bin_dir / "pip.exe"
    return bin_dir / "pip"


def get_venv_activate_cmd(venv_path: Path) -> str:
    """Get the shell command to activate a venv (for display purposes)."""
    if IS_WINDOWS:
        return f"{venv_path}\\Scripts\\activate"
    return f"source {venv_path}/bin/activate"


# =============================================================================
# Executable Discovery
# =============================================================================

def find_executable(name: str) -> Optional[str]:
    """
    Cross-platform replacement for the 'which' command.
    Returns the full path to the executable, or None if not found.
    """
    return shutil.which(name)


def get_python_executable() -> str:
    """Get the current Python executable path."""
    return sys.executable


# =============================================================================
# Network / Port Utilities
# =============================================================================

def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a TCP port is in use (cross-platform)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        return sock.connect_ex((host, port)) == 0


def kill_process_on_port(port: int) -> bool:
    """
    Kill any process listening on the given port.
    
    Cross-platform:
    - Unix: uses lsof + SIGTERM
    - Windows: uses netstat + taskkill
    
    Returns True if a process was found and killed, False otherwise.
    """
    try:
        if IS_WINDOWS:
            # Use netstat to find PID on Windows
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return False
            for line in result.stdout.splitlines():
                # Match lines like: TCP    0.0.0.0:8001    0.0.0.0:0    LISTENING    12345
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    pid = int(parts[-1])
                    if pid > 0:
                        subprocess.run(
                            ["taskkill", "/F", "/PID", str(pid)],
                            capture_output=True, timeout=5
                        )
                        return True
        else:
            # Unix: use lsof
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                for pid_str in result.stdout.strip().split('\n'):
                    try:
                        os.kill(int(pid_str), signal.SIGTERM)
                    except (ValueError, ProcessLookupError):
                        pass
                return True
    except Exception:
        pass
    return False


# =============================================================================
# Process Management
# =============================================================================

def start_background_process(
    cmd: List[str],
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
) -> subprocess.Popen:
    """
    Start a process detached from the current terminal.
    
    Cross-platform:
    - Unix: uses start_new_session (setsid)
    - Windows: uses CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS
    """
    kwargs = {
        "cwd": cwd,
        "stdout": stdout,
        "stderr": stderr,
    }
    if env:
        kwargs["env"] = env

    if IS_WINDOWS:
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True

    return subprocess.Popen(cmd, **kwargs)


# =============================================================================
# uv Package Manager
# =============================================================================

def get_uv_env() -> dict:
    """
    Get an environment dict with uv on PATH.
    Handles different install locations per OS.
    """
    env = os.environ.copy()

    if IS_WINDOWS:
        home = os.environ.get("USERPROFILE", "")
        extra_paths = [
            os.path.join(home, ".local", "bin"),
            os.path.join(home, ".cargo", "bin"),
            os.path.join(os.environ.get("APPDATA", ""), "uv"),
        ]
        separator = ";"
    else:
        home = os.environ.get("HOME", "")
        extra_paths = [
            os.path.join(home, ".local", "bin"),
            os.path.join(home, ".cargo", "bin"),
        ]
        separator = ":"

    existing = env.get("PATH", "")
    env["PATH"] = separator.join(extra_paths + [existing])
    return env


def is_uv_installed() -> bool:
    """Check if uv package manager is available."""
    # First check PATH
    if find_executable("uv"):
        return True
    # Check with uv-aware PATH
    env = get_uv_env()
    try:
        result = subprocess.run(
            ["uv", "--version"],
            capture_output=True, timeout=5,
            env=env
        )
        return result.returncode == 0
    except Exception:
        return False


def install_uv() -> bool:
    """
    Install uv package manager (cross-platform).
    
    - Unix: curl | sh
    - Windows: PowerShell irm | iex
    
    Returns True if installation succeeds.
    """
    if is_uv_installed():
        return True

    try:
        if IS_WINDOWS:
            result = subprocess.run(
                ["powershell", "-ExecutionPolicy", "Bypass", "-Command",
                 "irm https://astral.sh/uv/install.ps1 | iex"],
                capture_output=True, text=True, timeout=120
            )
        else:
            result = subprocess.run(
                ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
                capture_output=True, text=True, timeout=120
            )
        return result.returncode == 0
    except Exception:
        return False


# =============================================================================
# GPU Detection
# =============================================================================

def detect_nvidia_gpu() -> bool:
    """Check if nvidia-smi is available (works on all OSes with NVIDIA drivers)."""
    return find_executable("nvidia-smi") is not None


def detect_apple_silicon() -> bool:
    """Check if running on Apple Silicon (arm64 macOS)."""
    return IS_MACOS and platform.machine() == "arm64"


def detect_apple_gpu_memory_mb() -> int:
    """Detect Apple Silicon unified memory in MB.
    
    Apple Silicon uses unified memory shared between CPU and GPU.
    We report a conservative fraction (~75%) as 'available GPU memory'
    since the OS and apps use some of the unified pool.
    
    Returns 0 if not Apple Silicon or detection fails.
    """
    if not detect_apple_silicon():
        return 0
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            total_bytes = int(result.stdout.strip())
            total_mb = total_bytes // (1024 * 1024)
            # Apple Silicon shares memory between CPU and GPU.
            # Use 75% as effective GPU-available memory — conservative
            # but reflects that most of unified memory is accessible to Metal.
            return int(total_mb * 0.75)
    except Exception:
        pass
    return 0


def detect_gpu_vram_mb() -> int:
    """Detect GPU VRAM in MB.
    
    Checks NVIDIA first (via nvidia-smi), then Apple Silicon unified memory.
    Returns 0 if no supported GPU detected.
    """
    # NVIDIA GPU (Linux, Windows, rare macOS with eGPU)
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip().split('\n')[0])
    except Exception:
        pass
    
    # Apple Silicon unified memory
    apple_mem = detect_apple_gpu_memory_mb()
    if apple_mem > 0:
        return apple_mem
    
    return 0


# =============================================================================
# Docker Compose Override Management
# =============================================================================

def generate_docker_compose_override(
    project_root: Path,
    use_gpu: bool,
    extra_volumes: Optional[List[str]] = None,
    extra_hosts: Optional[List[str]] = None,
):
    """
    Generate docker-compose.override.yml with GPU config, extra volumes,
    and extra_hosts entries.
    
    Reads any existing override to preserve user-defined volume mounts,
    then writes the combined GPU config + volumes + extra_hosts.
    
    Args:
        project_root: Path to the project root (where docker-compose.yml lives)
        use_gpu: Whether to include NVIDIA GPU reservation
        extra_volumes: Additional volume mount strings (e.g., ["/mnt/drive:/mnt/drive:rw"])
        extra_hosts: Additional host:ip mappings for DNS resolution inside containers
                     (e.g., ["myserver.local:192.168.1.50"])
    """
    override_file = project_root / "docker-compose.override.yml"

    # Parse existing override to preserve user volume mounts
    existing_volumes = []
    if override_file.exists():
        try:
            import yaml
            with open(override_file) as f:
                existing = yaml.safe_load(f) or {}
            existing_backend = existing.get("services", {}).get("backend", {})
            existing_volumes = existing_backend.get("volumes", [])
        except Exception:
            # If YAML parsing fails, use a state-machine parser that only
            # captures lines under a "volumes:" section (not GPU device config)
            try:
                in_volumes = False
                with open(override_file) as f:
                    for line in f:
                        stripped = line.strip()
                        # Detect section headers (non-indented or less-indented keys)
                        if stripped.endswith(":") and not stripped.startswith("-"):
                            in_volumes = stripped == "volumes:"
                            continue
                        # Only capture volume-like entries under volumes: section
                        if in_volumes and stripped.startswith("- "):
                            val = stripped[2:].strip()
                            # Volume mounts look like /path:/path or name:/path
                            # Filter out YAML mappings like "driver: nvidia"
                            if "/" in val:
                                existing_volumes.append(val)
            except Exception:
                pass

    # Merge volumes (preserve existing, add extras)
    all_volumes = list(existing_volumes)
    if extra_volumes:
        for vol in extra_volumes:
            if vol not in all_volumes:
                all_volumes.append(vol)

    # Write override file manually (avoid PyYAML dependency for simple output)
    lines = ["# Local docker-compose overrides (gitignored)", ""]
    lines.append("services:")
    lines.append("  backend:")

    if use_gpu and detect_nvidia_gpu():
        lines.append("    deploy:")
        lines.append("      resources:")
        lines.append("        reservations:")
        lines.append("          devices:")
        lines.append("            - driver: nvidia")
        lines.append("              count: all")
        lines.append("              capabilities: [gpu]")

    if all_volumes:
        lines.append("    volumes:")
        for vol in all_volumes:
            lines.append(f"      - {vol}")

    if extra_hosts:
        lines.append("    extra_hosts:")
        for host_entry in extra_hosts:
            lines.append(f"      - \"{host_entry}\"")

    # Celery worker also needs extra_hosts for DNS resolution
    if extra_hosts:
        lines.append("  celery-worker:")
        lines.append("    extra_hosts:")
        for host_entry in extra_hosts:
            lines.append(f"      - \"{host_entry}\"")

    lines.append("")  # trailing newline

    with open(override_file, 'w') as f:
        f.write('\n'.join(lines))
