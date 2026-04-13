#!/usr/bin/env python3
"""
Money Agents Setup Wizard

Interactive TUI to configure the Money Agents application.
This script:
1. Collects configuration (API keys, settings)
2. Creates/updates .env file from .env.example
3. Creates the admin user account
4. Supports re-running for password reset or config updates

Usage:
    python start.py

First run: Full setup wizard
Subsequent runs: Choose to reset password or update configuration
"""

import os
import sys
import re
import getpass
import subprocess
import shutil
import secrets
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List

# Ensure subprocess output is decoded as UTF-8 on all platforms
# (Windows defaults to cp1252 which can't decode Docker's UTF-8 output)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.platform == "win32":
    os.environ["PYTHONUTF8"] = "1"

# Cross-platform utilities
from platform_utils import (
    IS_WINDOWS, IS_MACOS, IS_LINUX,
    get_venv_python, get_venv_pip, get_venv_activate_cmd,
    find_executable, is_port_in_use, kill_process_on_port,
    start_background_process, get_uv_env,
    is_uv_installed as _platform_is_uv_installed,
    install_uv as _platform_install_uv,
    detect_gpu_vram_mb, detect_nvidia_gpu, detect_apple_silicon,
    generate_docker_compose_override,
    get_os_description, get_disk_free_gb, get_total_ram_gb,
    check_docker_installed, check_docker_compose_installed,
    check_docker_daemon_running, try_start_docker,
    check_git_installed, check_python_version,
    check_ports_available, get_docker_install_url, get_git_install_url,
)

# Project root
PROJECT_ROOT = Path(__file__).resolve().parent
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
ENV_FILE = PROJECT_ROOT / ".env"

# Command-line flags (set by main())
ENABLE_ALL_TOOLS = False  # --all flag: enable all tools with sensible defaults


def resolve_hostname(hostname: str) -> Optional[str]:
    """Resolve a hostname to an IP address.
    
    Useful for .local (mDNS) hostnames that Docker containers can't resolve.
    Returns the IP string, or None if resolution fails.
    """
    import socket
    try:
        return socket.gethostbyname(hostname)
    except socket.gaierror:
        return None


def build_extra_hosts_from_env(env_values: Dict[str, str], quiet: bool = False) -> List[str]:
    """Build extra_hosts entries for hostnames Docker containers can't resolve.
    
    Detects .local (mDNS) hostnames in service URLs and resolves them to IPs
    so Docker containers can reach them via extra_hosts.
    
    Returns list of "hostname:ip" strings for docker-compose extra_hosts.
    """
    from urllib.parse import urlparse
    extra_hosts = []
    
    # URLs that might contain .local hostnames (e.g., Start9 services)
    url_keys = [
        "SERPER_CLONE_URL", "OLLAMA_BASE_URL", "ACESTEP_API_URL",
        "LND_REST_URL", "LND_MEMPOOL_URL",
    ]
    
    for key in url_keys:
        url = env_values.get(key, "")
        if not url:
            continue
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname
            if not hostname:
                continue
            # Only resolve .local (mDNS) hostnames — Docker can't do mDNS
            if hostname.endswith(".local"):
                ip = resolve_hostname(hostname)
                if ip:
                    entry = f"{hostname}:{ip}"
                    if entry not in extra_hosts:
                        extra_hosts.append(entry)
                        if not quiet:
                            print(f"  {Colors.DIM}Resolved {hostname} -> {ip} (for Docker DNS){Colors.RESET}")
                else:
                    if not quiet:
                        print(f"  {Colors.YELLOW}WARNING: Could not resolve {hostname} — service may be unreachable from Docker{Colors.RESET}")
        except Exception:
            pass
    
    return extra_hosts

# ANSI colors
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'

def clear_screen():
    """Clear terminal screen."""
    os.system('clear' if os.name != 'nt' else 'cls')

def print_header():
    """Print the Money Agents banner."""
    print(f"""
{Colors.CYAN}{Colors.BOLD}╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║   ⚡  M O N E Y   A G E N T S  ⚡                                ║
║                                                                  ║
║   AI-Powered Opportunity Discovery & Campaign Execution          ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝{Colors.RESET}
""")

def print_section(title: str):
    """Print a section header."""
    print(f"\n{Colors.BLUE}{Colors.BOLD}{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}{Colors.RESET}\n")

def print_info(msg: str):
    """Print info message."""
    print(f"{Colors.GREEN}✓{Colors.RESET} {msg}")

def print_warning(msg: str):
    """Print warning message."""
    print(f"{Colors.YELLOW}⚠{Colors.RESET} {msg}")

def print_error(msg: str):
    """Print error message."""
    print(f"{Colors.RED}✗{Colors.RESET} {msg}")


def masked_input(prompt_text: str) -> str:
    """Read input from the user while displaying asterisks instead of characters.

    Works cross-platform (Linux/macOS via termios, Windows via msvcrt).
    Supports backspace to delete characters.  Falls back to getpass if
    the terminal does not support raw mode (e.g. piped stdin).
    """
    import sys
    sys.stdout.write(prompt_text)
    sys.stdout.flush()

    chars: list[str] = []

    if IS_WINDOWS:
        # Windows: use msvcrt for character-at-a-time reading
        import msvcrt
        while True:
            ch = msvcrt.getwch()
            if ch in ('\r', '\n'):
                sys.stdout.write('\n')
                sys.stdout.flush()
                break
            elif ch == '\x08' or ch == '\x7f':  # Backspace / Delete
                if chars:
                    chars.pop()
                    sys.stdout.write('\b \b')
                    sys.stdout.flush()
            elif ch == '\x03':  # Ctrl-C
                raise KeyboardInterrupt
            elif ch == '\x1a':  # Ctrl-Z (EOF on Windows)
                break
            else:
                chars.append(ch)
                sys.stdout.write('*')
                sys.stdout.flush()
    else:
        # Unix (Linux / macOS): use termios for raw mode
        try:
            import tty
            import termios
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                while True:
                    ch = sys.stdin.read(1)
                    if ch in ('\r', '\n', ''):
                        sys.stdout.write('\n')
                        sys.stdout.flush()
                        break
                    elif ch == '\x7f' or ch == '\x08':  # Backspace
                        if chars:
                            chars.pop()
                            sys.stdout.write('\b \b')
                            sys.stdout.flush()
                    elif ch == '\x03':  # Ctrl-C
                        sys.stdout.write('\n')
                        sys.stdout.flush()
                        raise KeyboardInterrupt
                    elif ch == '\x04':  # Ctrl-D (EOF)
                        sys.stdout.write('\n')
                        sys.stdout.flush()
                        break
                    else:
                        chars.append(ch)
                        sys.stdout.write('*')
                        sys.stdout.flush()
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except (ImportError, termios.error, ValueError):
            # Fallback: no terminal available (piped stdin, etc.)
            # Use getpass which also hides input
            return getpass.getpass(prompt_text)

    return ''.join(chars)


def prompt(question: str, default: str = "", secret: bool = False, password: bool = False, required: bool = False) -> str:
    """
    Prompt user for input.
    
    Args:
        question: Question to display
        default: Default value (shown in brackets)
        secret: Hide the default value display (shows "current value set" instead)
        password: Use hidden password input (for actual passwords, not API keys)
        required: Whether a value is required
        
    Returns:
        User input or default value
    """
    # Build the hint text based on whether there's an existing value
    if default:
        if secret:
            hint = f" [{Colors.DIM}current value set - Enter to keep, 'c' to clear{Colors.RESET}]"
        else:
            hint = f" [{default}]"
    else:
        hint = f" [{Colors.DIM}Enter to skip{Colors.RESET}]" if not required else ""
    
    prompt_text = f"{Colors.CYAN}?{Colors.RESET} {question}{hint}: "
    
    while True:
        if password:
            value = getpass.getpass(prompt_text)
        elif secret:
            value = masked_input(prompt_text)
        else:
            value = input(prompt_text)
        
        value = value.strip()
        
        # Check for clear command (only for secret fields with existing value)
        if secret and default and value.lower() in ('c', 'd', 'clear', 'delete'):
            print(f"  {Colors.DIM}Value cleared{Colors.RESET}")
            return ""
        
        # Use default if empty
        if not value and default:
            return default
        
        # Check required
        if required and not value:
            print_error("This field is required. Please enter a value.")
            continue
            
        return value

def prompt_yes_no(question: str, default: bool = True) -> bool:
    """Prompt for yes/no answer."""
    default_str = "Y/n" if default else "y/N"
    prompt_text = f"{Colors.CYAN}?{Colors.RESET} {question} [{default_str}]: "
    
    while True:
        value = input(prompt_text).strip().lower()
        
        if not value:
            return default
        if value in ('y', 'yes'):
            return True
        if value in ('n', 'no'):
            return False
        
        print_error("Please enter 'y' or 'n'")

def prompt_choice(question: str, choices: list, default: int = 1) -> int:
    """Prompt user to select from numbered choices."""
    print(f"\n{Colors.CYAN}?{Colors.RESET} {question}")
    for i, choice in enumerate(choices, 1):
        print(f"  {i}. {choice}")
    
    while True:
        value = input(f"\n  Enter choice [1-{len(choices)}] (default: {default}): ").strip()
        
        if not value:
            return default
        
        try:
            num = int(value)
            if 1 <= num <= len(choices):
                return num
        except ValueError:
            pass
        
        print_error(f"Please enter a number between 1 and {len(choices)}")

def validate_email(email: str) -> bool:
    """Validate email format."""
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

def validate_password(password: str) -> Tuple[bool, str]:
    """Validate password meets complexity requirements.
    
    Matches the backend API's UserCreate schema validation:
    at least 8 chars, one uppercase, one lowercase, one digit, one special char.
    """
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter"
    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter"
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one digit"
    if not any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?/~`' for c in password):
        return False, "Password must contain at least one special character"
    return True, ""

def load_current_env() -> Dict[str, str]:
    """Load current .env file values."""
    values = {}
    if ENV_FILE.exists():
        with open(ENV_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, value = line.partition('=')
                    values[key.strip()] = value.strip()
    return values

def save_env_file(values: Dict[str, str]):
    """Create .env file from template with provided values."""
    # Read template
    with open(ENV_EXAMPLE, 'r') as f:
        template = f.read()
    
    # Replace values in template
    lines = []
    for line in template.split('\n'):
        if '=' in line and not line.strip().startswith('#'):
            key = line.split('=')[0].strip()
            if key in values:
                # Preserve any inline comment
                if '#' in line:
                    _, _, comment = line.partition('#')
                    lines.append(f"{key}={values[key]}  #{comment}")
                else:
                    lines.append(f"{key}={values[key]}")
            else:
                lines.append(line)
        else:
            lines.append(line)
    
    # Write .env file
    with open(ENV_FILE, 'w') as f:
        f.write('\n'.join(lines))
    
    print_info(f"Configuration saved to {ENV_FILE}")


# Known insecure default SECRET_KEY values (must match backend/app/core/config.py)
_INSECURE_SECRET_KEYS = {
    "dev_secret_key_change_in_production",
    "your_super_secret_key_here_change_this_in_production",
    "changeme",
    "secret",
    "",
}


def ensure_secure_secret_key(env_values: Dict[str, str]) -> Dict[str, str]:
    """Generate a secure SECRET_KEY if the current one is a known insecure default.

    Updates both the in-memory dict and the .env file on disk.
    Returns the (possibly updated) env_values dict.
    """
    current_key = env_values.get("SECRET_KEY", "")
    if current_key not in _INSECURE_SECRET_KEYS:
        return env_values

    new_key = secrets.token_urlsafe(32)
    env_values["SECRET_KEY"] = new_key

    # Update .env file on disk if it exists
    if ENV_FILE.exists():
        content = ENV_FILE.read_text()
        # Replace the SECRET_KEY line (preserving any inline comment)
        import re as _re
        new_content = _re.sub(
            r'^(SECRET_KEY[ \t]*=[ \t]*).*$',
            f'\\1{new_key}',
            content,
            flags=_re.MULTILINE,
        )
        if new_content != content:
            ENV_FILE.write_text(new_content)

    print_info("Generated secure SECRET_KEY (auto-generated, unique to this install)")
    return env_values


# Insecure default values for passwords that should be auto-rotated
_INSECURE_PASSWORDS = {
    "changeme_in_production",
    "changeme",
    "money_agents_dev_password",
    "CHANGE_ME_generate_a_secure_password",
    "",
}


def ensure_secure_passwords(env_values: Dict[str, str]) -> Dict[str, str]:
    """Generate secure random passwords for REDIS_PASSWORD, FLOWER_PASSWORD,
    and POSTGRES_PASSWORD if they still have a known insecure default value.

    When POSTGRES_PASSWORD is rotated, DATABASE_URL is also updated to match.

    Updates both the in-memory dict and the .env file on disk.
    Returns the (possibly updated) env_values dict.
    """
    import re as _re

    keys_to_check = ["REDIS_PASSWORD", "FLOWER_PASSWORD", "POSTGRES_PASSWORD"]
    updated = []

    for key in keys_to_check:
        current = env_values.get(key, "")
        if current not in _INSECURE_PASSWORDS:
            continue

        new_password = secrets.token_urlsafe(24)
        env_values[key] = new_password

        # Update .env file on disk if it exists
        if ENV_FILE.exists():
            content = ENV_FILE.read_text()
            new_content = _re.sub(
                rf'^({_re.escape(key)}[ \t]*=[ \t]*).*$',
                lambda m: f'{m.group(1)}{new_password}',
                content,
                flags=_re.MULTILINE,
            )
            if new_content != content:
                ENV_FILE.write_text(new_content)

        updated.append(key)

        # When Postgres password changes, also update DATABASE_URL to match
        if key == "POSTGRES_PASSWORD":
            old_db_url = env_values.get("DATABASE_URL", "")
            if old_db_url:
                # Replace password in postgresql+asyncpg://user:PASSWORD@host/db
                new_db_url = _re.sub(
                    r'(postgresql\+asyncpg://[^:]+:)[^@]+(@)',
                    lambda m: f'{m.group(1)}{new_password}{m.group(2)}',
                    old_db_url,
                )
                env_values["DATABASE_URL"] = new_db_url
                if ENV_FILE.exists():
                    content = ENV_FILE.read_text()
                    new_content = _re.sub(
                        r'^(DATABASE_URL[ \t]*=[ \t]*).*$',
                        lambda m: f'{m.group(1)}{new_db_url}',
                        content,
                        flags=_re.MULTILINE,
                    )
                    if new_content != content:
                        ENV_FILE.write_text(new_content)

    if updated:
        print_info(f"Generated secure {', '.join(updated)} (auto-generated, unique to this install)")

    return env_values


def ensure_service_api_keys(env_values: Dict[str, str]) -> Dict[str, str]:
    """Generate GPU_SERVICE_API_KEY and SERVICE_MANAGER_API_KEY if not set.

    These keys protect the host-side GPU/CPU tool services and the service
    manager from unauthenticated access.  Without them, every service bound
    to 0.0.0.0 accepts requests from any machine on the network.

    The keys are written to .env AND exported into ``os.environ`` so child
    processes (GPU services, service manager) inherit them automatically.
    """
    import re as _re

    # GAP-4/GAP-5: Also auto-generate GPU_INTERNAL_API_KEY for management endpoints
    keys_to_ensure = ["GPU_SERVICE_API_KEY", "GPU_INTERNAL_API_KEY", "SERVICE_MANAGER_API_KEY"]
    generated = []

    for key in keys_to_ensure:
        current = env_values.get(key, "")
        if current:
            # Already set — just make sure it's in os.environ for children
            os.environ[key] = current
            continue

        new_key = secrets.token_urlsafe(32)
        env_values[key] = new_key
        os.environ[key] = new_key

        # Update .env file on disk
        if ENV_FILE.exists():
            content = ENV_FILE.read_text()
            # Try to replace existing (empty) line
            new_content = _re.sub(
                rf'^({_re.escape(key)}[ \t]*=[ \t]*).*$',
                lambda m, k=new_key: f'{m.group(1)}{k}',
                content,
                flags=_re.MULTILINE,
            )
            if new_content != content:
                ENV_FILE.write_text(new_content)
            else:
                # Key line doesn't exist yet — append it
                with open(ENV_FILE, 'a') as f:
                    f.write(f"\n{key}={new_key}\n")

        generated.append(key)

    if generated:
        print_info(f"Generated secure {', '.join(generated)} (auto-generated, unique to this install)")

    return env_values


def ensure_service_manager_url(env_values: Dict[str, str]) -> Dict[str, str]:
    """Derive SERVICE_MANAGER_URL from SERVICE_MANAGER_PORT if not explicitly set.

    This keeps the docker-compose SERVICE_MANAGER_URL in sync when the user
    changes SERVICE_MANAGER_PORT in .env without having to manually update
    the full URL as well.
    """
    import re as _re

    port = env_values.get("SERVICE_MANAGER_PORT", "9100")
    current_url = env_values.get("SERVICE_MANAGER_URL", "")
    expected_url = f"http://host.docker.internal:{port}"

    # Export the port so child processes and stop_service_manager() can read it
    os.environ["SERVICE_MANAGER_PORT"] = str(port)

    if current_url and current_url != expected_url:
        # User set an explicit custom URL — don't override it
        return env_values

    env_values["SERVICE_MANAGER_URL"] = expected_url
    os.environ["SERVICE_MANAGER_URL"] = expected_url

    # Persist to .env on disk
    if ENV_FILE.exists():
        content = ENV_FILE.read_text()
        new_content = _re.sub(
            r'^(SERVICE_MANAGER_URL[ \t]*=[ \t]*).*$',
            lambda m: f'{m.group(1)}{expected_url}',
            content,
            flags=_re.MULTILINE,
        )
        if new_content != content:
            ENV_FILE.write_text(new_content)
        else:
            # Line doesn't exist yet — append it
            with open(ENV_FILE, 'a') as f:
                f.write(f"\nSERVICE_MANAGER_URL={expected_url}\n")

    return env_values


# =============================================================================
# Prerequisite Checking
# =============================================================================

def check_docker_running() -> bool:
    """Check if Docker is running (legacy — use check_prerequisites instead)."""
    running, _ = check_docker_daemon_running()
    return running


def _check_label(label: str, width: int = 24) -> str:
    """Format a check label with padding."""
    return f"  {label:<{width}}"


def check_prerequisites(
    require_docker_running: bool = True,
    check_ports: bool = False,
    verbose: bool = True,
) -> bool:
    """
    Check all system prerequisites and provide helpful guidance.
    
    Checks (in order):
      HARD prerequisites (fatal if missing):
        - Python 3.10+
        - Docker installed
        - Docker Compose V2
        - Docker daemon running (if require_docker_running=True)
        - .env.example exists
      SOFT prerequisites (warnings):
        - git (needed for ACE-Step)
        - Disk space (warn < 5 GB)
        - Port availability (5173, 8000)
      INFO items (informational):
        - System info (OS, RAM)
        - NVIDIA GPU detection
    
    Returns True if all HARD prerequisites pass.
    """
    if verbose:
        print_section("System Prerequisite Check")
        print(f"  {Colors.DIM}Checking your system for required and optional dependencies...{Colors.RESET}\n")

    hard_failures = []
    soft_warnings = []
    auto_fixes = []
    
    # ── System Info (informational) ──────────────────────────────────────
    os_desc = get_os_description()
    ram_gb = get_total_ram_gb()
    ram_str = f"{ram_gb:.1f} GB" if ram_gb > 0 else "unknown"
    if verbose:
        print(f"{_check_label('Operating System')}{Colors.CYAN}{os_desc}{Colors.RESET}")
        print(f"{_check_label('System RAM')}{Colors.CYAN}{ram_str}{Colors.RESET}")
    
    # ── Python Version ───────────────────────────────────────────────────
    py_ok, py_ver = check_python_version(3, 10)
    if verbose:
        if py_ok:
            print(f"{_check_label('Python')}{Colors.GREEN}✓ {py_ver}{Colors.RESET}")
        else:
            print(f"{_check_label('Python')}{Colors.RED}✗ {py_ver}{Colors.RESET}")
    if not py_ok:
        hard_failures.append({
            "name": "Python 3.10+",
            "detail": py_ver,
            "fix": "Download Python 3.10 or newer from https://www.python.org/downloads/",
        })
    
    # ── .env.example ─────────────────────────────────────────────────────
    env_example_ok = ENV_EXAMPLE.exists()
    if verbose:
        if env_example_ok:
            print(f"{_check_label('.env.example')}{Colors.GREEN}✓ Found{Colors.RESET}")
        else:
            print(f"{_check_label('.env.example')}{Colors.RED}✗ Missing{Colors.RESET}")
    if not env_example_ok:
        hard_failures.append({
            "name": ".env.example",
            "detail": "Template file missing — the repository may be incomplete",
            "fix": "Re-clone the repo: git clone <repo-url> && cd money-agents",
        })
    
    # ── Docker Installed ─────────────────────────────────────────────────
    docker_ok, docker_ver = check_docker_installed()
    if verbose:
        if docker_ok:
            print(f"{_check_label('Docker')}{Colors.GREEN}✓ {docker_ver}{Colors.RESET}")
        else:
            print(f"{_check_label('Docker')}{Colors.RED}✗ Not installed{Colors.RESET}")
    if not docker_ok:
        docker_url = get_docker_install_url()
        install_hint = ""
        if IS_LINUX:
            install_hint = (
                f"\n    Quick install: curl -fsSL https://get.docker.com | sh"
                f"\n    Then add your user to the docker group: sudo usermod -aG docker $USER"
                f"\n    Log out and back in for group changes to take effect."
            )
        elif IS_MACOS:
            install_hint = (
                f"\n    Install Docker Desktop for Mac from: {docker_url}"
                f"\n    Or with Homebrew: brew install --cask docker"
            )
        elif IS_WINDOWS:
            install_hint = (
                f"\n    Install Docker Desktop for Windows from: {docker_url}"
                f"\n    Ensure WSL 2 backend is enabled during installation."
                f"\n    Or with winget: winget install Docker.DockerDesktop"
            )
        hard_failures.append({
            "name": "Docker",
            "detail": docker_ver,
            "fix": f"Install Docker from: {docker_url}{install_hint}",
        })
    
    # ── Docker Compose V2 ────────────────────────────────────────────────
    if docker_ok:
        compose_ok, compose_ver = check_docker_compose_installed()
        if verbose:
            if compose_ok:
                print(f"{_check_label('Docker Compose')}{Colors.GREEN}✓ {compose_ver}{Colors.RESET}")
            else:
                print(f"{_check_label('Docker Compose')}{Colors.RED}✗ {compose_ver}{Colors.RESET}")
        if not compose_ok:
            docker_url = get_docker_install_url()
            compose_hint = ""
            if IS_LINUX:
                compose_hint = (
                    f"\n    Install the Docker Compose plugin:"
                    f"\n      sudo apt-get update && sudo apt-get install docker-compose-plugin"
                    f"\n    Or see: https://docs.docker.com/compose/install/linux/"
                )
            else:
                compose_hint = (
                    f"\n    Docker Compose V2 is included with Docker Desktop."
                    f"\n    Please update Docker Desktop to the latest version."
                )
            hard_failures.append({
                "name": "Docker Compose V2",
                "detail": compose_ver,
                "fix": f"'docker compose' (V2 plugin format) is required.{compose_hint}",
            })
    else:
        if verbose:
            print(f"{_check_label('Docker Compose')}{Colors.DIM}— Skipped (Docker not installed){Colors.RESET}")
    
    # ── Docker Daemon Running ────────────────────────────────────────────
    docker_running = False
    if require_docker_running and docker_ok:
        running, detail = check_docker_daemon_running()
        docker_running = running
        if verbose:
            if running:
                print(f"{_check_label('Docker Daemon')}{Colors.GREEN}✓ Running{Colors.RESET}")
            else:
                print(f"{_check_label('Docker Daemon')}{Colors.RED}✗ Not running{Colors.RESET}")
        if not running:
            if detail == "permission_denied":
                if IS_LINUX:
                    hard_failures.append({
                        "name": "Docker Permissions",
                        "detail": "Permission denied when connecting to Docker",
                        "fix": (
                            "Add your user to the docker group:\n"
                            "    sudo usermod -aG docker $USER\n"
                            "    Then LOG OUT and log back in (or run: newgrp docker)"
                        ),
                    })
                else:
                    hard_failures.append({
                        "name": "Docker Permissions",
                        "detail": "Permission denied when connecting to Docker",
                        "fix": "Try running this script as Administrator (Windows) or check Docker Desktop settings.",
                    })
            else:
                # Offer to auto-start
                auto_fixes.append({
                    "name": "Docker Daemon",
                    "action": "start Docker",
                    "func": try_start_docker,
                })
    elif require_docker_running and not docker_ok:
        if verbose:
            print(f"{_check_label('Docker Daemon')}{Colors.DIM}— Skipped (Docker not installed){Colors.RESET}")
    
    # ── Git ──────────────────────────────────────────────────────────────
    git_ok, git_ver = check_git_installed()
    if verbose:
        if git_ok:
            print(f"{_check_label('Git')}{Colors.GREEN}✓ {git_ver}{Colors.RESET}")
        else:
            print(f"{_check_label('Git')}{Colors.YELLOW}⚠ Not installed (optional){Colors.RESET}")
    if not git_ok:
        git_url = get_git_install_url()
        install_hint = ""
        if IS_LINUX:
            install_hint = "\n    Quick install: sudo apt-get install git"
        elif IS_MACOS:
            install_hint = "\n    Quick install: xcode-select --install  (or: brew install git)"
        elif IS_WINDOWS:
            install_hint = f"\n    Download from: {git_url}\n    Or: winget install Git.Git"
        soft_warnings.append({
            "name": "Git",
            "detail": "Git is needed to install ACE-Step (local music generation)",
            "fix": f"Install from: {git_url}{install_hint}",
        })
    
    # ── GPU ────────────────────────────────────────────────────────────────
    has_nvidia = detect_nvidia_gpu()
    has_apple = detect_apple_silicon()
    vram = detect_gpu_vram_mb()  # handles both NVIDIA and Apple Silicon
    if verbose:
        if has_nvidia:
            vram_str = f" ({vram} MB VRAM)" if vram > 0 else ""
            print(f"{_check_label('NVIDIA GPU')}{Colors.GREEN}✓ Detected{vram_str}{Colors.RESET}")
        elif has_apple:
            unified_gb = vram // 1024 if vram > 0 else 0
            print(f"{_check_label('Apple Silicon')}{Colors.GREEN}✓ Detected (~{unified_gb} GB unified memory for GPU){Colors.RESET}")
        else:
            if IS_MACOS:
                print(f"{_check_label('GPU')}{Colors.DIM}— Intel Mac (no GPU acceleration){Colors.RESET}")
            else:
                print(f"{_check_label('NVIDIA GPU')}{Colors.DIM}— Not detected (GPU features will be disabled){Colors.RESET}")
    
    # ── Disk Space ───────────────────────────────────────────────────────
    free_gb = get_disk_free_gb(str(PROJECT_ROOT))
    if verbose:
        if free_gb > 10:
            print(f"{_check_label('Disk Space')}{Colors.GREEN}✓ {free_gb:.1f} GB free{Colors.RESET}")
        elif free_gb > 5:
            print(f"{_check_label('Disk Space')}{Colors.YELLOW}⚠ {free_gb:.1f} GB free (may be low){Colors.RESET}")
        elif free_gb > 0:
            print(f"{_check_label('Disk Space')}{Colors.RED}⚠ {free_gb:.1f} GB free (low!){Colors.RESET}")
        else:
            print(f"{_check_label('Disk Space')}{Colors.DIM}— Could not determine{Colors.RESET}")
    if 0 < free_gb < 5:
        soft_warnings.append({
            "name": "Disk Space",
            "detail": f"Only {free_gb:.1f} GB free — Docker images + data need ~5 GB minimum",
            "fix": "Free up disk space before continuing. Docker images alone require ~3 GB.",
        })
    
    # ── Port Availability ────────────────────────────────────────────────
    if check_ports:
        important_ports = [5173, 8000]
        port_results = check_ports_available(important_ports)
        port_names = {5173: "Frontend", 8000: "Backend API"}
        all_ports_ok = True
        for port, available in port_results:
            name = port_names.get(port, f"Port {port}")
            if verbose:
                if available:
                    print(f"{_check_label(f'Port {port} ({name})')}{Colors.GREEN}✓ Available{Colors.RESET}")
                else:
                    print(f"{_check_label(f'Port {port} ({name})')}{Colors.YELLOW}⚠ In use{Colors.RESET}")
                    all_ports_ok = False
        if not all_ports_ok:
            busy_ports = [str(p) for p, a in port_results if not a]
            soft_warnings.append({
                "name": "Port Conflict",
                "detail": f"Port(s) {', '.join(busy_ports)} already in use",
                "fix": "Stop the other services using those ports, or Docker will fail to bind. "
                       "Check what's using a port with:\n"
                       + ("    lsof -i :<port>  (Linux/macOS)" if not IS_WINDOWS else "    netstat -ano | findstr :<port>  (Windows)"),
            })
    
    if verbose:
        print()  # blank line after checks
    
    # ── Auto-fix offers ──────────────────────────────────────────────────
    if auto_fixes and not hard_failures:
        for fix in auto_fixes:
            print(f"{Colors.YELLOW}Docker is not running.{Colors.RESET}")
            try:
                answer = input(f"  Would you like to start Docker automatically? [Y/n] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = "n"
            
            if answer in ("", "y", "yes"):
                print(f"  {Colors.DIM}Starting Docker...{Colors.RESET}", end="", flush=True)
                if fix["func"]():
                    print(f"\r{_check_label('Docker Daemon')}{Colors.GREEN}✓ Started successfully{Colors.RESET}")
                    docker_running = True
                else:
                    print(f"\r{_check_label('Docker Daemon')}{Colors.RED}✗ Could not start automatically{Colors.RESET}")
                    start_hint = ""
                    if IS_MACOS:
                        start_hint = "Open Docker Desktop from Applications, or run: open -a Docker"
                    elif IS_LINUX:
                        start_hint = "Run: sudo systemctl start docker"
                    elif IS_WINDOWS:
                        start_hint = "Open Docker Desktop from the Start menu"
                    hard_failures.append({
                        "name": "Docker Daemon",
                        "detail": "Docker is installed but not running",
                        "fix": f"Start Docker manually and try again.\n    {start_hint}",
                    })
            else:
                start_hint = ""
                if IS_MACOS:
                    start_hint = "Open Docker Desktop from Applications, or run: open -a Docker"
                elif IS_LINUX:
                    start_hint = "Run: sudo systemctl start docker"
                elif IS_WINDOWS:
                    start_hint = "Open Docker Desktop from the Start menu"
                hard_failures.append({
                    "name": "Docker Daemon",
                    "detail": "Docker is installed but not running",
                    "fix": f"Start Docker and try again.\n    {start_hint}",
                })
        print()
    elif auto_fixes and hard_failures:
        # Docker not running but there are also other hard failures — don't offer auto-start
        for fix in auto_fixes:
            start_hint = ""
            if IS_MACOS:
                start_hint = "Open Docker Desktop from Applications, or run: open -a Docker"
            elif IS_LINUX:
                start_hint = "Run: sudo systemctl start docker"
            elif IS_WINDOWS:
                start_hint = "Open Docker Desktop from the Start menu"
            hard_failures.append({
                "name": "Docker Daemon",
                "detail": "Docker is installed but not running",
                "fix": f"Start Docker and try again.\n    {start_hint}",
            })
    
    # ── Report Results ───────────────────────────────────────────────────
    if soft_warnings and verbose:
        print(f"{Colors.YELLOW}{Colors.BOLD}  Warnings:{Colors.RESET}")
        for warn in soft_warnings:
            print(f"  {Colors.YELLOW}⚠ {warn['name']}:{Colors.RESET} {warn['detail']}")
            if warn.get("fix"):
                for line in warn["fix"].split("\n"):
                    print(f"    {Colors.DIM}{line}{Colors.RESET}")
            print()
    
    if hard_failures:
        if verbose:
            print(f"{Colors.RED}{Colors.BOLD}  ✗ Prerequisites not met — cannot continue{Colors.RESET}\n")
            for fail in hard_failures:
                print(f"  {Colors.RED}✗ {fail['name']}:{Colors.RESET} {fail['detail']}")
                if fail.get("fix"):
                    for line in fail["fix"].split("\n"):
                        print(f"    {Colors.DIM}{line}{Colors.RESET}")
                print()
            print(f"  {Colors.DIM}Fix the issues above and run this script again.{Colors.RESET}")
            print(f"  {Colors.DIM}For help: see README.md{Colors.RESET}\n")
        return False
    
    if verbose and not soft_warnings:
        print(f"  {Colors.GREEN}{Colors.BOLD}✓ All prerequisites met!{Colors.RESET}\n")
    elif verbose:
        print(f"  {Colors.GREEN}{Colors.BOLD}✓ Required prerequisites met (see warnings above).{Colors.RESET}\n")
    
    return True

def run_docker_compose_command(cmd: list, capture_output: bool = False) -> subprocess.CompletedProcess:
    """Run a docker compose command."""
    full_cmd = ["docker", "compose"] + cmd
    return subprocess.run(
        full_cmd,
        cwd=PROJECT_ROOT,
        capture_output=capture_output,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

def is_services_running() -> bool:
    """Check if Money Agents services are running."""
    result = run_docker_compose_command(["ps", "-q"], capture_output=True)
    return bool(result.stdout.strip())

def start_services_if_needed() -> bool:
    """
    Start docker services if not running.
    
    Re-resolves .local hostnames before starting to handle IP changes.
    
    Returns:
        True if services were started (weren't running before)
        False if services were already running
    """
    if not is_services_running():
        refresh_docker_dns()
        print_info("Starting Docker services...")
        run_docker_compose_command(["up", "-d"])
        print_info("Waiting for services to be ready...")
        import time
        time.sleep(10)  # Give services time to start
        return True
    return False


def refresh_docker_dns():
    """Re-resolve .local hostnames and update docker-compose.override.yml.
    
    Called before starting/restarting services so that IP changes on
    mDNS-advertised servers (e.g., Start9) are picked up automatically.
    """
    if not ENV_FILE.exists():
        return
    try:
        current_env = load_current_env()
        extra_hosts = build_extra_hosts_from_env(current_env, quiet=True)
        if extra_hosts:
            use_gpu = current_env.get('USE_GPU', 'false').lower() == 'true'
            generate_docker_compose_override(
                PROJECT_ROOT, use_gpu=use_gpu, extra_hosts=extra_hosts
            )
    except Exception:
        pass  # Best-effort; don't block service startup

def create_admin_user(email: str, username: str, password: str) -> bool:
    """Create admin user via the backend container."""
    # GAP-3: Pass credentials via stdin (JSON) to avoid /proc exposure
    python_code = '''
import asyncio
import sys
import json
from sqlalchemy import select
from app.core.database import get_session_maker
from app.core.security import get_password_hash
from app.models import User, UserRole

async def create_admin():
    creds = json.loads(sys.stdin.read())
    email = creds["email"]
    username = creds["username"]
    password = creds["password"]
    async with get_session_maker()() as db:
        # Check if user exists
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        
        if user:
            print("USER_EXISTS")
            return
        
        # Check username
        result = await db.execute(select(User).where(User.username == username))
        if result.scalar_one_or_none():
            print("USERNAME_EXISTS")
            return
        
        # Create admin user
        user = User(
            email=email,
            username=username,
            password_hash=get_password_hash(password),
            role=UserRole.ADMIN.value,
            is_active=True,
            is_superuser=True
        )
        db.add(user)
        await db.commit()
        print("SUCCESS")

asyncio.run(create_admin())
'''
    
    import json as _json
    stdin_data = _json.dumps({"email": email, "username": username, "password": password})
    result = subprocess.run(
        [
            "docker", "compose", "exec", "-T",
            "backend", "python", "-c", python_code,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        input=stdin_data,
        encoding="utf-8",
        errors="replace"
    )
    
    output = result.stdout.strip()
    
    if "SUCCESS" in output:
        return True
    elif "USER_EXISTS" in output:
        print_warning(f"User with email {email} already exists.")
        return True
    elif "USERNAME_EXISTS" in output:
        print_error(f"Username '{username}' is already taken.")
        return False
    else:
        print_error(f"Failed to create user: {result.stderr}")
        return False

def reset_admin_password(identifier: str, new_password: str) -> bool:
    """Reset admin password via the backend container."""
    # GAP-3: Pass credentials via stdin (JSON) to avoid /proc exposure
    python_code = '''
import asyncio
import sys
import json
from datetime import datetime, timezone
from sqlalchemy import select, or_
from app.core.database import get_session_maker
from app.core.security import get_password_hash
from app.models import User

async def reset_password():
    creds = json.loads(sys.stdin.read())
    identifier = creds["identifier"]
    new_password = creds["password"]
    async with get_session_maker()() as db:
        result = await db.execute(
            select(User).where(
                or_(User.email == identifier, User.username == identifier)
            )
        )
        user = result.scalar_one_or_none()
        
        if not user:
            print("USER_NOT_FOUND")
            return
        
        user.password_hash = get_password_hash(new_password)
        # GAP-3: Invalidate existing sessions by updating password_changed_at
        user.password_changed_at = datetime.now(timezone.utc)
        await db.commit()
        print("SUCCESS")

asyncio.run(reset_password())
'''
    
    import json as _json
    stdin_data = _json.dumps({"identifier": identifier, "password": new_password})
    result = subprocess.run(
        [
            "docker", "compose", "exec", "-T",
            "backend", "python", "-c", python_code,
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        input=stdin_data,
        encoding="utf-8",
        errors="replace"
    )
    
    output = result.stdout.strip()
    
    if "SUCCESS" in output:
        return True
    elif "USER_NOT_FOUND" in output:
        print_error(f"User '{identifier}' not found.")
        return False
    else:
        print_error(f"Failed to reset password: {result.stderr}")
        return False

def check_admin_exists() -> bool:
    """Check if any admin user exists."""
    python_code = '''
import asyncio
from sqlalchemy import select
from app.core.database import get_session_maker
from app.models import User, UserRole

async def check():
    async with get_session_maker()() as db:
        result = await db.execute(
            select(User).where(User.role == UserRole.ADMIN.value)
        )
        user = result.scalar_one_or_none()
        print("EXISTS" if user else "NONE")

asyncio.run(check())
'''
    
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "backend", "python", "-c", python_code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    
    return "EXISTS" in result.stdout

def run_init_tools_catalog():
    """Run the tools catalog initialization."""
    print_info("Initializing tools catalog...")
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "backend", "python", "init_tools_catalog.py"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    if result.returncode == 0:
        print_info("Tools catalog initialized successfully")
    else:
        print_warning(f"Tools initialization had issues: {result.stderr[:200] if result.stderr else 'unknown'}")

def run_resource_detection():
    """Run resource detection via API or direct call."""
    print_info("Detecting system resources...")
    python_code = '''
import asyncio
from app.core.database import get_session_maker
from app.services import resource_service

async def detect():
    async with get_session_maker()() as db:
        result = await resource_service.initialize_system_resources(db)
        await db.commit()
        print(f"Created {result['created']}, updated {result['updated']} resources. Types: {result['types']}")

asyncio.run(detect())
'''
    
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "backend", "python", "-c", python_code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    
    if result.returncode == 0:
        print_info(f"Resource detection: {result.stdout.strip()}")
    else:
        print_warning(f"Resource detection had issues: {result.stderr[:200] if result.stderr else 'unknown'}")

def collect_admin_credentials(current_env: Dict[str, str]) -> Dict[str, str]:
    """Collect admin user credentials."""
    print_section("Admin Account Setup")
    
    print(f"{Colors.DIM}Create your admin account to access the application.")
    print(f"You can run this setup script again to reset your password if needed.{Colors.RESET}\n")
    
    while True:
        email = prompt("Admin email address", required=True)
        if not validate_email(email):
            print_error("Please enter a valid email address")
            continue
        break
    
    while True:
        username = prompt("Admin username", required=True)
        if len(username) < 2:
            print_error("Username must be at least 2 characters")
            continue
        if not re.match(r'^[a-zA-Z0-9_]+$', username):
            print_error("Username can only contain letters, numbers, and underscores")
            continue
        break
    
    while True:
        password = prompt("Password (min 8 characters)", password=True, required=True)
        valid, msg = validate_password(password)
        if not valid:
            print_error(msg)
            continue
        
        confirm = prompt("Confirm password", password=True, required=True)
        if password != confirm:
            print_error("Passwords do not match")
            continue
        break
    
    return {
        "email": email,
        "username": username,
        "password": password
    }

def collect_llm_api_keys(current_env: Dict[str, str]) -> Dict[str, str]:
    """Collect LLM API keys configuration."""
    print_section("LLM Provider Configuration")
    
    print(f"{Colors.DIM}Configure your LLM providers. At least one is required for the system to work.")
    print(f"The system uses a tiered approach: fast (cheap) → reasoning → quality.")
    print(f"It will automatically failover between providers.{Colors.RESET}\n")
    
    values = {}
    
    # OpenAI
    print(f"\n{Colors.BOLD}OpenAI (GPT-4o, o1){Colors.RESET}")
    print(f"{Colors.DIM}Get your API key at: https://platform.openai.com/api-keys{Colors.RESET}")
    current_openai = current_env.get("OPENAI_API_KEY", "")
    has_current = current_openai and current_openai != "your_openai_api_key_here"
    openai_key = prompt(
        "OpenAI API Key (optional, press Enter to skip)",
        default=current_openai if has_current else "",
        secret=True
    )
    values["OPENAI_API_KEY"] = openai_key if openai_key else ""
    
    # Anthropic
    print(f"\n{Colors.BOLD}Anthropic (Claude Opus 4, Sonnet 4, Haiku){Colors.RESET}")
    print(f"{Colors.DIM}Get your API key at: https://console.anthropic.com/settings/keys{Colors.RESET}")
    current_anthropic = current_env.get("ANTHROPIC_API_KEY", "")
    has_current = current_anthropic and current_anthropic != "your_anthropic_api_key_here"
    anthropic_key = prompt(
        "Anthropic API Key (optional, press Enter to skip)",
        default=current_anthropic if has_current else "",
        secret=True
    )
    values["ANTHROPIC_API_KEY"] = anthropic_key if anthropic_key else ""
    
    # Z.ai
    print(f"\n{Colors.BOLD}Z.ai / Zhipu AI (GLM-4.7){Colors.RESET}")
    print(f"{Colors.DIM}Get your API key at: https://open.bigmodel.cn/")
    print(f"⭐ Has FREE flash tier! Recommended as primary provider.{Colors.RESET}")
    current_zai = current_env.get("Z_AI_API_KEY", "")
    has_current = current_zai and current_zai != "your_zai_api_key_here"
    zai_key = prompt(
        "Z.ai API Key (optional, press Enter to skip)",
        default=current_zai if has_current else "",
        secret=True
    )
    values["Z_AI_API_KEY"] = zai_key if zai_key else ""
    
    return values

def collect_ollama_config(current_env: Dict[str, str], has_cloud_llm: bool) -> Dict[str, str]:
    """Collect Ollama configuration."""
    print_section("Ollama (Local LLM) Configuration")
    
    print(f"{Colors.DIM}Ollama allows you to run LLMs locally on your machine.")
    print(f"It's great for offline use or if you have a powerful GPU.{Colors.RESET}\n")
    
    current_use_ollama = current_env.get("USE_OLLAMA", "false").lower() == "true"
    
    use_ollama = prompt_yes_no(
        "Enable Ollama for local LLM processing?",
        default=current_use_ollama
    )
    
    values = {"USE_OLLAMA": "true" if use_ollama else "false"}
    
    if not use_ollama:
        if not has_cloud_llm:
            print()
            print_warning("⚠️  WARNING: No LLM provider configured!")
            print_warning("   The application will NOT work without at least one LLM.")
            print_warning("   Please run this setup script again or edit .env to:")
            print_warning("   - Enable Ollama, OR")
            print_warning("   - Add an API key for OpenAI, Anthropic, or Z.ai")
            print()
            input(f"{Colors.YELLOW}Press Enter to continue...{Colors.RESET}")
        return values
    
    # Ollama URL
    print(f"\n{Colors.DIM}Ollama API endpoint (use host.docker.internal for Docker containers){Colors.RESET}")
    current_url = current_env.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    values["OLLAMA_BASE_URL"] = prompt("Ollama API URL", default=current_url)
    
    # Model tiers
    print(f"\n{Colors.BOLD}Model Tiers{Colors.RESET}")
    print(f"{Colors.DIM}Configure which models to use for each tier.")
    print(f"These models must be pulled in Ollama first: ollama pull <model>")
    print(f"Note: glm-4.7-flash requires Ollama v0.14.3 or higher{Colors.RESET}\n")
    
    current_tiers = current_env.get("OLLAMA_MODEL_TIERS", "hf.co/mradermacher/Nanbeige4.1-3B-GGUF:Q8_0,mistral-nemo:12b,glm-4.7-flash:latest").split(",", 2)
    
    fast_model = prompt(
        "Fast tier model (quick responses, high volume)",
        default=current_tiers[0].strip() if current_tiers else "hf.co/mradermacher/Nanbeige4.1-3B-GGUF:Q8_0"
    )
    
    reasoning_model = prompt(
        "Reasoning tier model (complex tasks)",
        default=current_tiers[1].strip() if len(current_tiers) > 1 else "mistral-nemo:12b"
    )
    
    quality_model = prompt(
        "Quality tier model (best output)",
        default=current_tiers[2].strip() if len(current_tiers) > 2 else "glm-4.7-flash:latest"
    )
    
    values["OLLAMA_MODEL_TIERS"] = f"{fast_model},{reasoning_model},{quality_model}"
    
    # Context lengths
    print(f"\n{Colors.BOLD}Context Window Sizes{Colors.RESET}")
    print(f"{Colors.DIM}Configure context window size for each tier (in tokens).{Colors.RESET}\n")
    
    current_lengths = current_env.get("OLLAMA_CONTEXT_LENGTHS", "262144,65536,8192").split(",")
    
    fast_ctx = prompt(
        "Fast tier context length",
        default=current_lengths[0].strip() if current_lengths else "262144"
    )
    
    reasoning_ctx = prompt(
        "Reasoning tier context length",
        default=current_lengths[1].strip() if len(current_lengths) > 1 else "65536"
    )
    
    quality_ctx = prompt(
        "Quality tier context length",
        default=current_lengths[2].strip() if len(current_lengths) > 2 else "8192"
    )
    
    values["OLLAMA_CONTEXT_LENGTHS"] = f"{fast_ctx},{reasoning_ctx},{quality_ctx}"
    
    # Max concurrent
    print(f"\n{Colors.DIM}Ollama is typically rate-limited. Use 1 unless you have a powerful system.{Colors.RESET}")
    current_concurrent = current_env.get("OLLAMA_MAX_CONCURRENT", "1")
    values["OLLAMA_MAX_CONCURRENT"] = prompt(
        "Max concurrent Ollama requests",
        default=current_concurrent
    )
    
    # Check if models are downloaded and offer to pull missing ones
    check_and_pull_ollama_models(values)
    
    return values


def check_and_pull_ollama_models(ollama_config: Dict[str, str]):
    """Check if configured Ollama models are downloaded, offer to pull missing ones.
    
    Queries the Ollama API to see which models are already available locally.
    If any are missing, prompts the user to download them.
    """
    import urllib.request
    import json as json_module
    
    base_url = ollama_config.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    tiers_str = ollama_config.get("OLLAMA_MODEL_TIERS", "")
    
    if not tiers_str:
        return
    
    # Parse tier models
    tier_parts = tiers_str.split(",", 2)
    tier_labels = ["fast", "reasoning", "quality"]
    models = []
    for i, part in enumerate(tier_parts):
        label = tier_labels[i] if i < len(tier_labels) else f"tier-{i}"
        models.append((label, part.strip()))
    
    if not models:
        return
    
    # Try to connect to Ollama and get available models
    # Use localhost if URL contains host.docker.internal (we're running on host, not in Docker)
    check_url = base_url.replace("host.docker.internal", "localhost")
    
    print(f"\n{Colors.BOLD}Checking Ollama Models{Colors.RESET}")
    print(f"{Colors.DIM}Querying {check_url} for available models...{Colors.RESET}")
    
    try:
        req = urllib.request.Request(f"{check_url}/api/tags", method="GET")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json_module.loads(resp.read().decode())
            available = {m['name'] for m in data.get('models', [])}
    except Exception as e:
        print(f"  {Colors.YELLOW}Could not connect to Ollama at {check_url}: {e}{Colors.RESET}")
        print(f"  {Colors.DIM}Make sure Ollama is running. You can pull models later with: ollama pull <model>{Colors.RESET}\n")
        return
    
    # Check which models are missing
    missing = []
    present = []
    for tier_label, model_name in models:
        # Check exact match first, then base name match
        if model_name in available:
            present.append((tier_label, model_name))
        else:
            # Check partial match (e.g., model without tag might match)
            base_name = model_name.split(':')[0] if ':' in model_name else model_name
            if any(a.startswith(base_name) for a in available):
                present.append((tier_label, model_name))
            else:
                missing.append((tier_label, model_name))
    
    # Show status
    for tier_label, model_name in present:
        print(f"  {Colors.GREEN}✓{Colors.RESET} {tier_label}: {model_name} {Colors.DIM}(already downloaded){Colors.RESET}")
    for tier_label, model_name in missing:
        print(f"  {Colors.YELLOW}✗{Colors.RESET} {tier_label}: {model_name} {Colors.DIM}(not found){Colors.RESET}")
    
    if not missing:
        print(f"\n  {Colors.GREEN}All configured models are available!{Colors.RESET}\n")
        return
    
    # Ask user if they want to download missing models
    print()
    if len(missing) == 1:
        msg = f"Download the missing model ({missing[0][1]})?"
    else:
        model_list = ", ".join(m[1] for m in missing)
        msg = f"Download {len(missing)} missing models ({model_list})?"
    
    if not prompt_yes_no(msg, default=True):
        print(f"\n  {Colors.DIM}Skipped. You can pull models later with:{Colors.RESET}")
        for _, model_name in missing:
            print(f"    ollama pull {model_name}")
        print()
        return
    
    # Pull each missing model
    for tier_label, model_name in missing:
        print(f"\n  {Colors.CYAN}Pulling {model_name} ({tier_label} tier)...{Colors.RESET}")
        print(f"  {Colors.DIM}This may take a while depending on model size and connection speed.{Colors.RESET}")
        
        try:
            # Use subprocess to run ollama pull so user sees download progress
            result = subprocess.run(
                ["ollama", "pull", model_name],
                timeout=3600,  # 1 hour timeout for large models
            )
            if result.returncode == 0:
                print(f"  {Colors.GREEN}✓{Colors.RESET} {model_name} downloaded successfully!")
            else:
                print(f"  {Colors.RED}✗{Colors.RESET} Failed to pull {model_name} (exit code {result.returncode})")
                print(f"  {Colors.DIM}You can try manually: ollama pull {model_name}{Colors.RESET}")
        except FileNotFoundError:
            print(f"  {Colors.RED}✗{Colors.RESET} 'ollama' command not found. Install Ollama from https://ollama.ai")
            break
        except subprocess.TimeoutExpired:
            print(f"  {Colors.RED}✗{Colors.RESET} Download timed out for {model_name}")
            print(f"  {Colors.DIM}Try manually: ollama pull {model_name}{Colors.RESET}")
        except Exception as e:
            print(f"  {Colors.RED}✗{Colors.RESET} Error pulling {model_name}: {e}")
            print(f"  {Colors.DIM}Try manually: ollama pull {model_name}{Colors.RESET}")
    
    print()


def collect_other_api_keys(current_env: Dict[str, str]) -> Dict[str, str]:
    """Collect other API keys."""
    print_section("Additional API Keys & Voice Generation")
    
    values = {}
    
    # Qwen3-TTS (local, free) - ask BEFORE ElevenLabs
    # Check if GPU is enabled (needed for VRAM detection)
    gpu_enabled = current_env.get("USE_GPU", "true").lower() == "true"
    qwen3_tts_values = collect_qwen3_tts_config(current_env, gpu_enabled)
    values.update(qwen3_tts_values)
    
    # Z-Image (local, free) - ask after Qwen3-TTS
    zimage_values = collect_zimage_config(current_env, gpu_enabled)
    values.update(zimage_values)
    
    # SeedVR2 Upscaler (local, free) - ask after Z-Image
    seedvr2_values = collect_seedvr2_config(current_env, gpu_enabled)
    values.update(seedvr2_values)
    
    # Canary-STT (local, free) - ask after SeedVR2
    canary_stt_values = collect_canary_stt_config(current_env, gpu_enabled)
    values.update(canary_stt_values)
    
    # AudioSR (local, free) - ask after Canary-STT
    audiosr_values = collect_audiosr_config(current_env, gpu_enabled)
    values.update(audiosr_values)
    
    # Media Toolkit (local, free, CPU-only) - ask after AudioSR
    media_toolkit_values = collect_media_toolkit_config(current_env, gpu_enabled)
    values.update(media_toolkit_values)
    
    # Real-ESRGAN CPU (local, free, CPU-only) - ask after Media Toolkit
    realesrgan_cpu_values = collect_realesrgan_cpu_config(current_env, gpu_enabled)
    values.update(realesrgan_cpu_values)
    
    # Docling Document Parser (local, free, CPU-only) - ask after Real-ESRGAN CPU
    docling_values = collect_docling_config(current_env, gpu_enabled)
    values.update(docling_values)
    
    # LTX-2 Video (local, free) - ask after Docling
    ltx_video_values = collect_ltx_video_config(current_env, gpu_enabled)
    values.update(ltx_video_values)
    
    # ElevenLabs - skip if Qwen3-TTS is enabled
    if qwen3_tts_values.get("USE_QWEN3_TTS") == "true":
        # Qwen3-TTS enabled, skip ElevenLabs question
        print(f"\n{Colors.DIM}ElevenLabs skipped - Qwen3-TTS provides free local voice generation.{Colors.RESET}")
        values["ELEVENLABS_API_KEY"] = current_env.get("ELEVENLABS_API_KEY", "")
    else:
        # Ask about ElevenLabs
        print(f"\n{Colors.BOLD}ElevenLabs (Cloud Voice Generation){Colors.RESET}")
        print(f"{Colors.DIM}Get your API key at: https://elevenlabs.io/app/settings/api-keys{Colors.RESET}")
        current_eleven = current_env.get("ELEVENLABS_API_KEY", "")
        has_current = current_eleven and current_eleven != "your_elevenlabs_api_key_here"
        eleven_key = prompt(
            "ElevenLabs API Key (optional)",
            default=current_eleven if has_current else "",
            secret=True
        )
        values["ELEVENLABS_API_KEY"] = eleven_key if eleven_key else ""
    
    # Serper / Serper Clone — delegate to standalone function
    serper_values = collect_serper_config(current_env)
    values.update(serper_values)
    
    return values


def _add_compose_profile(current_profiles: str, profile: str) -> str:
    """Add a profile to the COMPOSE_PROFILES comma-separated list (idempotent)."""
    profiles = [p.strip() for p in current_profiles.split(",") if p.strip()]
    if profile not in profiles:
        profiles.append(profile)
    return ",".join(profiles)


def _remove_compose_profile(current_profiles: str, profile: str) -> str:
    """Remove a profile from the COMPOSE_PROFILES comma-separated list."""
    profiles = [p.strip() for p in current_profiles.split(",") if p.strip() and p.strip() != profile]
    return ",".join(profiles)


def _parse_lndconnect_uri(uri: str) -> Dict[str, str]:
    """Parse an lndconnect:// URI into components.
    
    Format: lndconnect://host:port?macaroon=<base64url>&cert=<base64url>
    Returns dict with LND_REST_URL, LND_MACAROON_HEX, LND_TLS_CERT (base64).
    
    Always uses https:// — LND serves HTTPS even over Tor (self-signed cert).
    TLS verification is disabled for .onion since the cert won't match.
    """
    import base64
    from urllib.parse import urlparse, parse_qs
    
    result = {}
    
    try:
        # Replace lndconnect:// with https:// for urlparse
        normalized = uri.replace("lndconnect://", "https://")
        parsed = urlparse(normalized)
        
        host = parsed.hostname or "localhost"
        port = parsed.port or 8080
        # LND always serves HTTPS (even over Tor — it generates a self-signed cert)
        result["LND_REST_URL"] = f"https://{host}:{port}"
        
        params = parse_qs(parsed.query)
        
        # Macaroon is base64url-encoded in lndconnect URI → decode to bytes → hex-encode
        if "macaroon" in params:
            mac_b64url = params["macaroon"][0]
            # Add padding if needed
            padding = 4 - len(mac_b64url) % 4
            if padding != 4:
                mac_b64url += "=" * padding
            mac_bytes = base64.urlsafe_b64decode(mac_b64url)
            result["LND_MACAROON_HEX"] = mac_bytes.hex()
        
        # Cert is base64url-encoded in lndconnect URI → keep as standard base64
        if "cert" in params:
            cert_b64url = params["cert"][0]
            padding = 4 - len(cert_b64url) % 4
            if padding != 4:
                cert_b64url += "=" * padding
            cert_bytes = base64.urlsafe_b64decode(cert_b64url)
            # Store as standard base64
            result["LND_TLS_CERT"] = base64.b64encode(cert_bytes).decode()
    except Exception as e:
        print(f"{Colors.RED}Failed to parse lndconnect URI: {e}{Colors.RESET}")
    
    return result


def collect_serper_config(current_env: Dict[str, str]) -> Dict[str, str]:
    """Collect Serper / Serper Clone web search configuration.
    
    Extracted as a standalone function so the 'enable all tools' preset
    can call it directly without going through collect_other_api_keys.
    """
    values = {}
    
    # Serper / Serper Clone
    print(f"\n{Colors.BOLD}Web Search (Serper){Colors.RESET}")
    print(f"{Colors.DIM}Web search is required for Opportunity Scout to discover opportunities.{Colors.RESET}")
    
    # Check if using Serper Clone
    current_use_clone = current_env.get("USE_SERPER_CLONE", "false").lower() == "true"
    use_serper_clone = prompt_yes_no(
        "Are you running Serper Clone (local self-hosted search)?",
        default=current_use_clone
    )
    values["USE_SERPER_CLONE"] = "true" if use_serper_clone else "false"
    
    if use_serper_clone:
        # Serper Clone configuration
        print(f"\n{Colors.DIM}Serper Clone: https://github.com/paulscode/searxng-serper-bridge")
        print(f"For Start9 users: https://github.com/paulscode/serper-startos{Colors.RESET}")
        
        current_clone_url = current_env.get("SERPER_CLONE_URL", "")
        clone_url = prompt(
            "Serper Clone URL (include https://)",
            default=current_clone_url,
            required=True
        )
        values["SERPER_CLONE_URL"] = clone_url
        
        print(f"\n{Colors.GREEN}✓ Serper Clone will use self-signed SSL (verification disabled)")
        print(f"✓ Cost tracking disabled (Serper Clone is free!){Colors.RESET}")
        
        # Still need an API key for Serper Clone
        current_serper = current_env.get("SERPER_API_KEY", "")
        has_current = current_serper and current_serper != "your_serper_api_key_here"
        serper_key = prompt(
            "Serper Clone API Key",
            default=current_serper if has_current else "",
            secret=True,
            required=True
        )
        values["SERPER_API_KEY"] = serper_key
    else:
        # Standard Serper
        print(f"\n{Colors.DIM}Get your API key at: https://serper.dev/")
        print(f"⚠️  Required for Opportunity Scout agent to discover opportunities.{Colors.RESET}")
        current_serper = current_env.get("SERPER_API_KEY", "")
        has_current = current_serper and current_serper != "your_serper_api_key_here"
        serper_key = prompt(
            "Serper API Key",
            default=current_serper if has_current else "",
            secret=True
        )
        
        if not serper_key:
            print()
            print_warning("⚠️  WARNING: Serper API key not configured!")
            print_warning("   The Opportunity Scout agent requires web search to discover opportunities.")
            print_warning("   The system will have LIMITED functionality without this.")
            print_warning("   You can add it later by running this script again or editing .env")
            print()
            input(f"{Colors.YELLOW}Press Enter to continue...{Colors.RESET}")
        
        values["SERPER_API_KEY"] = serper_key if serper_key else ""
        # Preserve clone URL if previously set (user may switch back later)
        values["SERPER_CLONE_URL"] = current_env.get("SERPER_CLONE_URL", "")
    
    return values


def collect_lnd_config(current_env: Dict[str, str]) -> Dict[str, str]:
    """Collect Bitcoin / LND Lightning Node configuration."""
    print_section("Bitcoin / LND Lightning Node")
    
    values = {}
    
    print(f"{Colors.BOLD}LND Node Connection{Colors.RESET}")
    print(f"{Colors.DIM}Connect to your Lightning Network Daemon (LND) node to give Money Agents")
    print(f"access to Bitcoin on-chain and Lightning Network capabilities.")
    print(f"This enables wallet balance tracking, payment sending/receiving,")
    print(f"and future agent autonomy over Bitcoin transactions.{Colors.RESET}")
    print()
    print(f"{Colors.DIM}Supported node software: LND (Lightning Labs)")
    print(f"Compatible with: Start9, Umbrel, RaspiBlitz, Voltage, self-hosted LND")
    print(f"Tor (.onion) nodes supported — a Tor proxy container auto-starts when needed.{Colors.RESET}")
    print()
    
    current_use = current_env.get("USE_LND", "false").lower() == "true"
    use_lnd = prompt_yes_no("Enable Bitcoin wallet (LND)?", default=current_use)
    values["USE_LND"] = "true" if use_lnd else "false"
    
    if not use_lnd:
        # Preserve existing values if user disables
        values["LND_REST_URL"] = current_env.get("LND_REST_URL", "https://host.docker.internal:8080")
        values["LND_MACAROON_HEX"] = current_env.get("LND_MACAROON_HEX", "")
        values["LND_TLS_VERIFY"] = current_env.get("LND_TLS_VERIFY", "false")
        values["LND_TLS_CERT"] = current_env.get("LND_TLS_CERT", "")
        values["LND_TOR_PROXY"] = current_env.get("LND_TOR_PROXY", "")
        # Remove tor profile if LND was the only reason
        values["COMPOSE_PROFILES"] = _remove_compose_profile(
            current_env.get("COMPOSE_PROFILES", ""), "tor"
        )
        return values
    
    # Ask about configuration method
    print(f"\n{Colors.BOLD}Connection Method{Colors.RESET}")
    print(f"  {Colors.CYAN}1{Colors.RESET}) Paste an lndconnect:// URI (easiest)")
    print(f"  {Colors.CYAN}2{Colors.RESET}) Enter REST URL and macaroon manually")
    
    has_lndconnect = current_env.get("LND_MACAROON_HEX", "")
    default_method = "2" if has_lndconnect else "1"
    method = prompt("Choose method (1 or 2)", default=default_method)
    
    if method == "1":
        # lndconnect:// URI
        print(f"\n{Colors.DIM}You can find this in your node's connection settings.")
        print(f"Look for 'REST' or 'lndconnect REST' option.")
        print(f"Tor .onion URIs from Start9 are supported.{Colors.RESET}")
        print()
        
        uri = prompt("lndconnect:// URI", required=True, secret=True)
        parsed = _parse_lndconnect_uri(uri)
        
        if parsed.get("LND_REST_URL"):
            values["LND_REST_URL"] = parsed["LND_REST_URL"]
            values["LND_MACAROON_HEX"] = parsed.get("LND_MACAROON_HEX", "")
            values["LND_TLS_CERT"] = parsed.get("LND_TLS_CERT", "")
            values["LND_TLS_VERIFY"] = "false"  # Self-signed certs are standard for LND
            
            is_onion = ".onion" in values["LND_REST_URL"]
            
            print(f"\n{Colors.GREEN}✓ Parsed lndconnect URI successfully{Colors.RESET}")
            print(f"  REST URL:   {values['LND_REST_URL']}")
            print(f"  Macaroon:   {'✓ Extracted' if values['LND_MACAROON_HEX'] else '✗ Missing'}")
            if is_onion:
                print(f"  Tor:        ✓ .onion address detected (tor-proxy will auto-start)")
                print(f"  TLS Cert:   {'✓ Extracted (defense-in-depth TLS over Tor)' if values['LND_TLS_CERT'] else '○ Not included'}")
            else:
                print(f"  TLS Cert:   {'✓ Extracted' if values['LND_TLS_CERT'] else '○ Not included'}")
        else:
            print(f"{Colors.RED}Failed to parse URI. Falling back to manual entry.{Colors.RESET}")
            method = "2"  # Fall through to manual
    
    if method == "2":
        # Manual configuration
        print(f"\n{Colors.BOLD}LND REST API URL{Colors.RESET}")
        print(f"{Colors.DIM}The REST API runs on port 8080 (HTTPS — even over Tor).")
        print(f"  Clearnet: https://localhost:8080, https://mynode.local:8080")
        print(f"  Tor:      https://abc123...xyz.onion:8080{Colors.RESET}")
        
        current_url = current_env.get("LND_REST_URL", "https://host.docker.internal:8080")
        rest_url = prompt("LND REST URL", default=current_url, required=True)
        
        # Auto-correct http:// to https:// for .onion addresses (LND always serves HTTPS)
        if ".onion" in rest_url and rest_url.startswith("http://"):
            rest_url = rest_url.replace("http://", "https://", 1)
            print(f"{Colors.YELLOW}  → Switched to https:// (LND always serves HTTPS, even over Tor){Colors.RESET}")
        
        values["LND_REST_URL"] = rest_url
        
        print(f"\n{Colors.BOLD}Admin Macaroon (hex-encoded){Colors.RESET}")
        print(f"{Colors.DIM}Generate with: xxd -ps -c 10000 ~/.lnd/data/chain/bitcoin/mainnet/admin.macaroon")
        print(f"Or copy from your node's connection settings.{Colors.RESET}")
        
        current_mac = current_env.get("LND_MACAROON_HEX", "")
        has_current_mac = bool(current_mac)
        if has_current_mac:
            print(f"{Colors.GREEN}Current macaroon: ✓ Configured ({len(current_mac)} chars){Colors.RESET}")
        
        macaroon = prompt(
            "Macaroon (hex)",
            default=current_mac if has_current_mac else "",
            secret=True,
            required=True
        )
        values["LND_MACAROON_HEX"] = macaroon
        
        # TLS verification
        values["LND_TLS_VERIFY"] = "false"  # Self-signed certs are standard for LND
        
        # Optional TLS cert for defense-in-depth verification
        current_cert = current_env.get("LND_TLS_CERT", "")
        is_manual_onion = ".onion" in rest_url
        if current_cert:
            print(f"\n{Colors.GREEN}TLS certificate: ✓ Already configured ({len(current_cert)} chars){Colors.RESET}")
            values["LND_TLS_CERT"] = current_cert
        else:
            cert_label = "defense-in-depth over Tor" if is_manual_onion else "self-signed cert"
            print(f"\n{Colors.DIM}Optional: Paste your LND TLS certificate (base64) for {cert_label} verification.")
            print(f"You can find it at: ~/.lnd/tls.cert (or your node's connection settings)")
            print(f"Press Enter to skip.{Colors.RESET}")
            tls_cert = prompt("TLS cert (base64)", default="", secret=False)
            values["LND_TLS_CERT"] = tls_cert
    
    # Handle Tor proxy for .onion addresses
    is_onion = ".onion" in values.get("LND_REST_URL", "")
    if is_onion:
        values["LND_TOR_PROXY"] = "socks5://tor-proxy:9050"
        values["LND_TLS_VERIFY"] = "false"
        # Keep TLS cert if provided — used for defense-in-depth TLS
        # verification even over Tor (hostname check disabled, cert
        # identity verified).  Only fall back to env if not already set.
        if not values.get("LND_TLS_CERT"):
            values["LND_TLS_CERT"] = current_env.get("LND_TLS_CERT", "")
        values["COMPOSE_PROFILES"] = _add_compose_profile(
            current_env.get("COMPOSE_PROFILES", ""), "tor"
        )
        tls_status = "with TLS cert verification" if values.get("LND_TLS_CERT") else "without TLS cert (verification disabled)"
        print(f"\n{Colors.CYAN}ℹ Tor proxy container will start automatically for .onion routing {tls_status}.{Colors.RESET}")
    else:
        values["LND_TOR_PROXY"] = ""
        values["COMPOSE_PROFILES"] = _remove_compose_profile(
            current_env.get("COMPOSE_PROFILES", ""), "tor"
        )
    
    # Mempool Explorer URL for transaction links
    current_mempool = current_env.get("LND_MEMPOOL_URL", "https://mempool.space")
    mempool_url = prompt_with_default(
        "Mempool Explorer URL (for clickable tx links)",
        current_mempool
    )
    values["LND_MEMPOOL_URL"] = mempool_url.rstrip("/")
    
    # Max payment safety limit
    current_max = current_env.get("LND_MAX_PAYMENT_SATS", "10000")
    max_payment = prompt_with_default(
        "Max payment safety limit (sats, -1 = no limit, 0 = all require approval)",
        current_max
    )
    values["LND_MAX_PAYMENT_SATS"] = max_payment
    
    print(f"\n{Colors.GREEN}✓ LND configuration saved{Colors.RESET}")
    print(f"{Colors.DIM}The backend will connect to your LND node on startup.")
    print(f"You can verify the connection from the Dashboard wallet widget.{Colors.RESET}")
    
    return values


def generate_all_tools_defaults(current_env: Dict[str, str]) -> Dict[str, str]:
    """Generate sensible defaults for all tools based on system capabilities.
    
    Used by the --all flag and the "Enable all tools" preset to skip
    individual tool prompts. VRAM is auto-detected to choose appropriate
    model variants and enable/disable GPU-heavy tools.
    """
    gpu_vram = detect_gpu_vram()
    has_gpu = gpu_vram > 0
    
    values = {
        # GPU master toggle
        "USE_GPU": "true" if has_gpu else "false",
        
        # Ollama (always enable — user still needs to install it separately)
        "USE_OLLAMA": "true",
        "OLLAMA_BASE_URL": current_env.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
        "OLLAMA_MODEL_TIERS": current_env.get("OLLAMA_MODEL_TIERS", "hf.co/mradermacher/Nanbeige4.1-3B-GGUF:Q8_0,mistral-nemo:12b,glm-4.7-flash:latest"),
        "OLLAMA_CONTEXT_LENGTHS": current_env.get("OLLAMA_CONTEXT_LENGTHS", "262144,65536,8192"),
        "OLLAMA_MAX_CONCURRENT": current_env.get("OLLAMA_MAX_CONCURRENT", "1"),
        
        # ACE-Step (≥4GB VRAM)
        "USE_ACESTEP": "true" if gpu_vram >= 4000 else "false",
        "ACESTEP_MODEL": "base" if gpu_vram >= 16000 else "turbo",
        "ACESTEP_AUTO_START": "true",
        "ACESTEP_API_URL": current_env.get("ACESTEP_API_URL", "http://host.docker.internal:8001"),
        "ACESTEP_API_PORT": current_env.get("ACESTEP_API_PORT", "8001"),
        "ACESTEP_DOWNLOAD_SOURCE": current_env.get("ACESTEP_DOWNLOAD_SOURCE", "auto"),
        
        # Qwen3-TTS (≥4GB VRAM)
        "USE_QWEN3_TTS": "true" if gpu_vram >= 4000 else "false",
        "QWEN3_TTS_TIER": "full" if gpu_vram >= 8000 else ("lite" if gpu_vram >= 4000 else "lite"),
        "QWEN3_TTS_AUTO_START": "true",
        "QWEN3_TTS_IDLE_TIMEOUT": "300",
        "QWEN3_TTS_API_URL": current_env.get("QWEN3_TTS_API_URL", "http://host.docker.internal:8002"),
        "QWEN3_TTS_API_PORT": current_env.get("QWEN3_TTS_API_PORT", "8002"),
        
        # Z-Image (≥16GB VRAM)
        "USE_ZIMAGE": "true" if gpu_vram >= 12000 else "false",
        "ZIMAGE_AUTO_START": "true",
        "ZIMAGE_IDLE_TIMEOUT": "300",
        "ZIMAGE_API_URL": current_env.get("ZIMAGE_API_URL", "http://host.docker.internal:8003"),
        "ZIMAGE_API_PORT": current_env.get("ZIMAGE_API_PORT", "8003"),
        "ZIMAGE_MODEL": current_env.get("ZIMAGE_MODEL", "turbo"),
        
        # SeedVR2 (≥8GB VRAM)
        "USE_SEEDVR2": "true" if gpu_vram >= 8000 else "false",
        "SEEDVR2_AUTO_START": "true",
        "SEEDVR2_IDLE_TIMEOUT": "300",
        "SEEDVR2_MODEL": "seedvr2_ema_3b_fp8_e4m3fn.safetensors",
        "SEEDVR2_API_URL": current_env.get("SEEDVR2_API_URL", "http://host.docker.internal:8004"),
        "SEEDVR2_API_PORT": current_env.get("SEEDVR2_API_PORT", "8004"),
        
        # Canary-STT (≥4GB VRAM)
        "USE_CANARY_STT": "true" if gpu_vram >= 4000 else "false",
        "CANARY_STT_AUTO_START": "true",
        "CANARY_STT_IDLE_TIMEOUT": "300",
        "CANARY_STT_API_URL": current_env.get("CANARY_STT_API_URL", "http://host.docker.internal:8005"),
        "CANARY_STT_API_PORT": current_env.get("CANARY_STT_API_PORT", "8005"),
        
        # LTX-2 Video (≥24GB VRAM)
        "USE_LTX_VIDEO": "true" if gpu_vram >= 20000 else "false",
        "LTX_VIDEO_AUTO_START": "true",
        "LTX_VIDEO_IDLE_TIMEOUT": "300",
        "LTX_VIDEO_API_URL": current_env.get("LTX_VIDEO_API_URL", "http://host.docker.internal:8006"),
        "LTX_VIDEO_API_PORT": current_env.get("LTX_VIDEO_API_PORT", "8006"),
        "LTX_VIDEO_MODEL_DIR": current_env.get("LTX_VIDEO_MODEL_DIR", "models/ltx-2"),
        
        # AudioSR (≥4GB VRAM)
        "USE_AUDIOSR": "true" if gpu_vram >= 4000 else "false",
        "AUDIOSR_AUTO_START": "true",
        "AUDIOSR_IDLE_TIMEOUT": "300",
        "AUDIOSR_MODEL": "basic",
        "AUDIOSR_API_URL": current_env.get("AUDIOSR_API_URL", "http://host.docker.internal:8007"),
        "AUDIOSR_API_PORT": current_env.get("AUDIOSR_API_PORT", "8007"),
        
        # Media Toolkit (CPU-only, always enable)
        "USE_MEDIA_TOOLKIT": "true",
        "MEDIA_TOOLKIT_AUTO_START": "true",
        "MEDIA_TOOLKIT_API_URL": current_env.get("MEDIA_TOOLKIT_API_URL", "http://host.docker.internal:8008"),
        "MEDIA_TOOLKIT_API_PORT": current_env.get("MEDIA_TOOLKIT_API_PORT", "8008"),
        
        # Real-ESRGAN CPU (CPU-only, always enable)
        "USE_REALESRGAN_CPU": "true",
        "REALESRGAN_CPU_AUTO_START": "true",
        "REALESRGAN_CPU_MODEL": "realesr-animevideov3",
        "REALESRGAN_CPU_API_URL": current_env.get("REALESRGAN_CPU_API_URL", "http://host.docker.internal:8009"),
        "REALESRGAN_CPU_API_PORT": current_env.get("REALESRGAN_CPU_API_PORT", "8009"),
        
        # Docling (CPU-only, always enable)
        "USE_DOCLING": "true",
        "DOCLING_AUTO_START": "true",
        "DOCLING_API_URL": current_env.get("DOCLING_API_URL", "http://host.docker.internal:8010"),
        "DOCLING_API_PORT": current_env.get("DOCLING_API_PORT", "8010"),
        
        # Dev Sandbox (always enable)
        "USE_DEV_SANDBOX": "true",
        
        # Nostr (always enable)
        "USE_NOSTR": "true",
        
        # Suno disabled when ACE-Step is available
        "USE_SUNO": "false" if gpu_vram >= 4000 else "true",
    }
    
    # Print summary of what was auto-selected
    print_section("All Tools — Auto-Selected Defaults")
    
    if has_gpu:
        if detect_apple_silicon():
            gpu_gb = gpu_vram // 1024
            print(f"  {Colors.GREEN}Apple Silicon detected: ~{gpu_gb} GB unified memory for GPU{Colors.RESET}\n")
        else:
            print(f"  {Colors.GREEN}NVIDIA GPU detected: {gpu_vram} MB VRAM{Colors.RESET}\n")
    else:
        print(f"  {Colors.YELLOW}No GPU detected — GPU tools disabled, CPU tools enabled{Colors.RESET}\n")
    
    # GPU tools table
    gpu_tools = [
        ("Ollama (LLM)", values["USE_OLLAMA"], "Local LLM inference"),
        ("ACE-Step", values["USE_ACESTEP"], f"Music generation ({values['ACESTEP_MODEL']} model)"),
        ("Qwen3-TTS", values["USE_QWEN3_TTS"], f"Voice generation ({values['QWEN3_TTS_TIER']} tier)"),
        ("Z-Image", values["USE_ZIMAGE"], "Image generation (6B DiT)"),
        ("SeedVR2", values["USE_SEEDVR2"], "Image/video upscaling"),
        ("Canary-STT", values["USE_CANARY_STT"], "Speech-to-text"),
        ("AudioSR", values["USE_AUDIOSR"], "Audio super-resolution"),
        ("LTX-2 Video", values["USE_LTX_VIDEO"], "Text-to-video generation"),
    ]
    
    cpu_tools = [
        ("Media Toolkit", values["USE_MEDIA_TOOLKIT"], "FFmpeg media composition"),
        ("Real-ESRGAN CPU", values["USE_REALESRGAN_CPU"], "CPU image/video upscaling"),
        ("Docling", values["USE_DOCLING"], "Document parsing"),
        ("Dev Sandbox", values["USE_DEV_SANDBOX"], "Isolated Docker containers"),
        ("Nostr", values["USE_NOSTR"], "Decentralized social protocol"),
    ]
    
    print(f"  {Colors.BOLD}GPU Tools:{Colors.RESET}")
    for name, enabled, desc in gpu_tools:
        icon = f"{Colors.GREEN}✓{Colors.RESET}" if enabled == "true" else f"{Colors.DIM}✗{Colors.RESET}"
        print(f"    {icon} {name:20s} {Colors.DIM}{desc}{Colors.RESET}")
    
    print(f"\n  {Colors.BOLD}CPU Tools:{Colors.RESET}")
    for name, enabled, desc in cpu_tools:
        icon = f"{Colors.GREEN}✓{Colors.RESET}" if enabled == "true" else f"{Colors.DIM}✗{Colors.RESET}"
        print(f"    {icon} {name:20s} {Colors.DIM}{desc}{Colors.RESET}")
    
    # Download size estimate
    download_sizes = estimate_download_sizes(values)
    if download_sizes["total_gb"] > 0:
        print(f"\n  {Colors.YELLOW}Estimated model downloads: ~{download_sizes['total_gb']}GB{Colors.RESET}")
        for name, size in download_sizes["items"]:
            print(f"    {Colors.DIM}• {name}: ~{size}GB{Colors.RESET}")
    
    print()
    return values


def estimate_download_sizes(values: Dict[str, str]) -> Dict:
    """Estimate total model download sizes based on enabled tools.
    
    Returns dict with 'total_gb' (float) and 'items' (list of (name, size) tuples).
    """
    items = []
    
    if values.get("USE_OLLAMA") == "true":
        # Rough estimate: 3 models, typical sizes
        items.append(("Ollama models (3 models)", 12))
    
    if values.get("USE_ACESTEP") == "true":
        items.append(("ACE-Step", 5))
    
    if values.get("USE_QWEN3_TTS") == "true":
        tier = values.get("QWEN3_TTS_TIER", "full")
        size = 6 if tier == "full" else 3
        items.append(("Qwen3-TTS", size))
    
    if values.get("USE_ZIMAGE") == "true":
        items.append(("Z-Image", 12))
    
    if values.get("USE_SEEDVR2") == "true":
        items.append(("SeedVR2", 4))
    
    if values.get("USE_CANARY_STT") == "true":
        items.append(("Canary-STT", 5))
    
    if values.get("USE_AUDIOSR") == "true":
        items.append(("AudioSR", 4))
    
    if values.get("USE_LTX_VIDEO") == "true":
        items.append(("LTX-2 Video", 42))
    
    if values.get("USE_REALESRGAN_CPU") == "true":
        items.append(("Real-ESRGAN CPU", 0.1))

    total = sum(size for _, size in items)
    return {"total_gb": round(total, 1), "items": items}


def collect_feature_settings(current_env: Dict[str, str]) -> Dict[str, str]:
    """Collect feature toggle settings."""
    print_section("Feature Settings")
    
    values = {}
    
    # GPU (ask first since ACE-Step depends on it)
    print(f"{Colors.BOLD}GPU Acceleration{Colors.RESET}")
    if IS_MACOS and detect_apple_silicon():
        print(f"{Colors.DIM}Apple Silicon detected — GPU tools use Metal/MPS via unified memory.{Colors.RESET}")
    else:
        print(f"{Colors.DIM}Enable if you have a supported GPU (NVIDIA or Apple Silicon).{Colors.RESET}")
    current_gpu = current_env.get("USE_GPU", "true").lower() == "true"
    use_gpu = prompt_yes_no("Enable GPU acceleration?", default=current_gpu)
    values["USE_GPU"] = "true" if use_gpu else "false"
    
    if not use_gpu:
        # Force-disable all GPU tools when GPU acceleration is off
        values["USE_OLLAMA"] = "false"
        values["USE_ACESTEP"] = "false"
        values["USE_QWEN3_TTS"] = "false"
        values["USE_ZIMAGE"] = "false"
        values["USE_SEEDVR2"] = "false"
        values["USE_CANARY_STT"] = "false"
        values["USE_AUDIOSR"] = "false"
        values["USE_LTX_VIDEO"] = "false"
        values["USE_SUNO"] = current_env.get("USE_SUNO", "false")
        print(f"\n  {Colors.DIM}GPU tools disabled (Ollama, ACE-Step, Qwen3-TTS, Z-Image, SeedVR2, Canary-STT, AudioSR, LTX-2 Video){Colors.RESET}")
        return values
    
    # ACE-Step Local Music Generation (ask before Suno)
    acestep_values = collect_acestep_config(current_env, use_gpu)
    values.update(acestep_values)
    
    # Suno - only ask if ACE-Step is disabled (no need for both)
    if acestep_values.get("USE_ACESTEP") == "true":
        # ACE-Step enabled, skip Suno
        values["USE_SUNO"] = "false"
        print(f"\n{Colors.DIM}Suno skipped - ACE-Step provides free local music generation.{Colors.RESET}")
    else:
        # ACE-Step disabled, ask about Suno
        print(f"\n{Colors.BOLD}Suno (AI Music Generation - Cloud){Colors.RESET}")
        print(f"{Colors.DIM}Enable if you want cloud-based AI music generation via Suno.{Colors.RESET}")
        current_suno = current_env.get("USE_SUNO", "true").lower() == "true"
        use_suno = prompt_yes_no("Enable Suno music generation?", default=current_suno)
        values["USE_SUNO"] = "true" if use_suno else "false"
    
    return values


def detect_gpu_vram() -> int:
    """Detect GPU VRAM in MB.
    
    Uses nvidia-smi for NVIDIA GPUs, or Apple Silicon unified memory (75%).
    Returns 0 if no supported GPU detected.
    """
    return detect_gpu_vram_mb()


# =============================================================================
# Firewall Configuration (Linux UFW)
# =============================================================================

_deferred_firewall_services: list = []

def detect_firewall() -> Optional[str]:
    """
    Detect which firewall is active on the system.
    
    Returns:
        'ufw' - Linux UFW firewall
        'firewalld' - Linux firewalld
        'windows' - Windows Firewall
        'macos' - macOS Application Firewall
        None - No firewall detected or not applicable
    """
    import platform
    
    system = platform.system().lower()
    
    if system == 'linux':
        # Check for UFW (Ubuntu/Debian) - use sudo for status check
        try:
            # Use shutil.which (cross-platform) instead of subprocess 'which'
            if find_executable('ufw'):
                # ufw exists, check if it's active (requires sudo)
                result = subprocess.run(
                    ["sudo", "-n", "ufw", "status"],  # -n = non-interactive
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5
                )
                if result.returncode == 0 and 'active' in result.stdout.lower():
                    return 'ufw'
                # If sudo failed (needs password), still return 'ufw' if we know it's installed
                # The configure function will handle sudo with password prompt
                elif result.returncode != 0 and 'sudo' in result.stderr.lower():
                    return 'ufw'
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        # Check for firewalld (RHEL/CentOS/Fedora)
        try:
            result = subprocess.run(
                ["firewall-cmd", "--state"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5
            )
            if result.returncode == 0 and 'running' in result.stdout.lower():
                return 'firewalld'
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        
        # No high-level firewall manager detected
        return None
    
    elif system == 'darwin':  # macOS
        # Check if the macOS Application Firewall is enabled
        try:
            result = subprocess.run(
                ["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
            )
            if result.returncode == 0 and 'enabled' in result.stdout.lower():
                return 'macos'
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None
    
    elif system == 'windows':
        # Check if Windows Firewall is enabled
        try:
            result = subprocess.run(
                ["netsh", "advfirewall", "show", "allprofiles", "state"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
            )
            if result.returncode == 0 and 'ON' in result.stdout.upper():
                return 'windows'
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None
    
    return None


def configure_firewall_for_service(port: int, service_name: str = "ACE-Step") -> bool:
    """
    Configure firewall to allow Docker containers to reach a host service.
    
    Docker containers need to access host services, but firewalls often block this.
    This function adds the necessary rules for the detected firewall.
    
    Supports: UFW (Linux), firewalld (Linux), Windows Firewall, macOS Application Firewall.
    
    Args:
        port: The port the service is running on
        service_name: Human-readable name for log messages (e.g., "ACE-Step", "Qwen3-TTS")
    
    Returns:
        True if firewall was configured successfully or not needed, False on error.
    """
    firewall = detect_firewall()
    
    if firewall is None:
        # No firewall detected, no configuration needed
        return True
    
    # Helper to check if we can run sudo without password
    def can_sudo_without_password():
        try:
            result = subprocess.run(
                ["sudo", "-n", "true"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False
    
    if firewall == 'ufw':
        # Allow Docker networks (172.16.0.0/12 covers Docker's default range)
        ufw_command = f"sudo ufw allow from 172.16.0.0/12 to any port {port} comment 'Docker networks - {service_name}'"
        
        try:
            # Check if rule already exists (try without sudo first, fall back to sudo -n)
            result = subprocess.run(
                ["sudo", "-n", "ufw", "status", "numbered"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10
            )
            
            if result.returncode != 0:
                # sudo requires password — defer to consolidated note
                _deferred_firewall_services.append(service_name)
                return False
            
            # Look for existing rule for this port from Docker networks
            if f"{port}" in result.stdout and "172.16.0.0/12" in result.stdout:
                return True
            
            # Rule doesn't exist - try to add it
            if not can_sudo_without_password():
                _deferred_firewall_services.append(service_name)
                return False
            
            print_info(f"Adding UFW firewall rule to allow Docker access to port {port}...")
            result = subprocess.run(
                ["sudo", "ufw", "allow", "from", "172.16.0.0/12", "to", "any", "port", str(port), 
                 "comment", f"Docker networks - {service_name}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30
            )
            
            if result.returncode == 0:
                print_info(f"Firewall configured: Docker can now access port {port}")
                return True
            else:
                print_warning(f"Failed to configure firewall: {result.stderr}")
                print_warning(f"You can manually run: {ufw_command}")
                return False
                
        except subprocess.TimeoutExpired:
            print_warning("Firewall configuration timed out")
            print_warning(f"You can manually run: {ufw_command}")
            return False
        except Exception as e:
            print_warning(f"Firewall configuration error: {e}")
            print_warning(f"You can manually run: {ufw_command}")
            return False
    
    elif firewall == 'firewalld':
        # For firewalld, we need to add a rich rule
        firewalld_command = (
            f"sudo firewall-cmd --permanent --add-rich-rule "
            f"\"rule family='ipv4' source address='172.16.0.0/12' port port='{port}' protocol='tcp' accept\" && "
            f"sudo firewall-cmd --reload"
        )
        
        try:
            if not can_sudo_without_password():
                _deferred_firewall_services.append(service_name)
                return False
            
            print_info(f"Adding firewalld rule to allow Docker access to port {port}...")
            result = subprocess.run(
                ["sudo", "firewall-cmd", "--permanent", "--add-rich-rule",
                 f"rule family='ipv4' source address='172.16.0.0/12' port port='{port}' protocol='tcp' accept"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30
            )
            
            if result.returncode == 0:
                # Reload to apply
                subprocess.run(["sudo", "firewall-cmd", "--reload"], timeout=10)
                print_info(f"Firewall configured: Docker can now access port {port}")
                return True
            else:
                print_warning(f"Failed to configure firewall: {result.stderr}")
                print_warning(f"You can manually run: {firewalld_command}")
                return False
                
        except subprocess.TimeoutExpired:
            print_warning("Firewall configuration timed out")
            print_warning(f"You can manually run: {firewalld_command}")
            return False
        except Exception as e:
            print_warning(f"Firewall configuration error: {e}")
            print_warning(f"You can manually run: {firewalld_command}")
            return False
    
    elif firewall == 'windows':
        # Windows Firewall: use netsh to add inbound rule
        netsh_command = (
            f'netsh advfirewall firewall add rule name="Docker {service_name} (port {port})" '
            f'dir=in action=allow protocol=tcp localport={port} remoteip=172.16.0.0/12'
        )
        
        try:
            # Check if rule already exists
            result = subprocess.run(
                ["netsh", "advfirewall", "firewall", "show", "rule",
                 f"name=Docker {service_name} (port {port})"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10
            )
            if result.returncode == 0 and f"Docker {service_name}" in result.stdout:
                print_info(f"Windows Firewall rule for port {port} already exists")
                return True
            
            # Add rule (may require Administrator)
            print_info(f"Adding Windows Firewall rule for port {port}...")
            result = subprocess.run(
                ["netsh", "advfirewall", "firewall", "add", "rule",
                 f"name=Docker {service_name} (port {port})",
                 "dir=in", "action=allow", "protocol=tcp",
                 f"localport={port}", "remoteip=172.16.0.0/12"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30
            )
            
            if result.returncode == 0:
                print_info(f"Windows Firewall configured: Docker can access port {port}")
                return True
            else:
                print_warning(f"Failed to configure Windows Firewall (may need Administrator).")
                print_warning(f"Run as Administrator: {netsh_command}")
                return False
        except Exception as e:
            print_warning(f"Windows Firewall configuration error: {e}")
            print_warning(f"Run as Administrator: {netsh_command}")
            return False
    
    elif firewall == 'macos':
        # macOS: Docker Desktop handles host networking transparently.
        # The Application Firewall controls incoming connections to apps,
        # not Docker-to-host communication.
        print_info("macOS detected — Docker Desktop handles host networking automatically.")
        print_info("If you experience connection issues, allow incoming connections in:")
        print_info("  System Settings → Network → Firewall → Options")
        return True
    
    # For other/unknown firewalls, return True but warn user
    print_warning(f"Detected {firewall} firewall but automatic configuration not implemented.")
    print_warning(f"You may need to manually allow Docker networks (172.16.0.0/12) to port {port}")
    return True


def print_deferred_firewall_note():
    """Print a single consolidated note about firewall rules, if needed.

    Called once after all host-side services have started.  Only prints if
    some services could not have their firewall rules auto-configured
    because ``sudo`` requires a password.
    """
    if not _deferred_firewall_services:
        return

    # De-duplicate while preserving order
    seen = set()
    unique = []
    for name in _deferred_firewall_services:
        if name not in seen:
            seen.add(name)
            unique.append(name)

    firewall = detect_firewall()
    fw_label = "UFW" if firewall == "ufw" else "firewalld" if firewall == "firewalld" else "firewall"

    print()
    print_info(
        f"Note: Your {fw_label} firewall may block Docker from reaching "
        f"{len(unique)} host service(s) ({', '.join(unique)})."
    )
    print_info(
        f"If tools are unreachable, re-run {Colors.CYAN}./start.sh{Colors.RESET} "
        f"and select {Colors.BOLD}Add {fw_label} firewall rules{Colors.RESET} when prompted."
    )

    # Clear so subsequent calls don't repeat
    _deferred_firewall_services.clear()


# =============================================================================
# ACE-Step Installation & Management (Host-side)
# =============================================================================

ACESTEP_DIR = PROJECT_ROOT / "acestep"
ACESTEP_REPO = "https://github.com/ace-step/ACE-Step-1.5.git"


def is_acestep_installed() -> bool:
    """Check if ACE-Step is installed."""
    return (ACESTEP_DIR / "pyproject.toml").exists()


def is_uv_installed() -> bool:
    """Check if uv package manager is installed (cross-platform)."""
    return _platform_is_uv_installed()


def install_uv() -> bool:
    """Install uv package manager (cross-platform)."""
    print_info("Installing uv package manager...")
    if _platform_install_uv():
        print_info("uv installed successfully!")
        return True
    else:
        print_warning("uv installation failed")
        return False


def ensure_acestep_output_symlink():
    """Create an acestep/output symlink pointing to the generated audio folder.
    
    ACE-Step saves generated audio deep inside .cache/acestep/tmp/api_audio/.
    This symlink provides a convenient output/ shortcut consistent with
    z-image/output/ and qwen3-tts/output/.
    
    On Windows, creating symlinks may require admin privileges or Developer Mode.
    """
    link_path = ACESTEP_DIR / "output"
    target_dir = ACESTEP_DIR / ".cache" / "acestep" / "tmp" / "api_audio"
    
    # Already exists (symlink or real dir)
    if link_path.exists() or link_path.is_symlink():
        return
    
    # Ensure target directory exists
    target_dir.mkdir(parents=True, exist_ok=True)
    
    try:
        if IS_WINDOWS:
            # Windows: use directory junction (no admin rights needed, unlike symlinks)
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link_path), str(target_dir)],
                capture_output=True, timeout=10,
            )
        else:
            # Linux/macOS: relative symlink so it works if the repo moves
            link_path.symlink_to(Path(".cache", "acestep", "tmp", "api_audio"))
    except Exception:
        # Non-critical — don't block startup
        pass


def _apply_acestep_patches():
    """Apply cooperative VRAM unload/reload patches to ACE-Step.

    Runs scripts/patch_acestep.py to add /unload, /reload endpoints and
    enhanced /health to the upstream ACE-Step code.  Idempotent — safe to
    call every startup.
    """
    patch_script = PROJECT_ROOT / "scripts" / "patch_acestep.py"
    if not patch_script.exists():
        return
    try:
        # Import and run directly (faster than subprocess, same Python)
        import importlib.util
        spec = importlib.util.spec_from_file_location("patch_acestep", str(patch_script))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.patch_acestep(ACESTEP_DIR)
    except Exception as e:
        print_warning(f"ACE-Step patch failed (non-critical): {e}")


def install_acestep() -> bool:
    """Clone and install ACE-Step."""
    if is_acestep_installed():
        print_info("ACE-Step is already installed")
        ensure_acestep_output_symlink()
        _apply_acestep_patches()
        return True
    
    print_info("Cloning ACE-Step repository...")
    try:
        result = subprocess.run(
            ["git", "clone", "--depth=1", ACESTEP_REPO, str(ACESTEP_DIR)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300
        )
        if result.returncode != 0:
            print_warning(f"Git clone failed: {result.stderr}")
            return False
        
        print_info("ACE-Step cloned. Installing dependencies with uv...")
        
        # Ensure uv is installed
        if not is_uv_installed():
            if not install_uv():
                print_warning("Cannot install ACE-Step without uv package manager")
                return False
        
        # Run uv sync
        result = subprocess.run(
            ["uv", "sync"],
            cwd=ACESTEP_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,  # 15 min for first install
            env=get_uv_env()
        )
        if result.returncode != 0:
            print_warning(f"uv sync failed: {result.stderr}")
            return False
        
        ensure_acestep_output_symlink()
        _apply_acestep_patches()
        print_info("ACE-Step installed successfully!")
        return True
        
    except subprocess.TimeoutExpired:
        print_warning("ACE-Step installation timed out")
        return False
    except Exception as e:
        print_warning(f"ACE-Step installation failed: {e}")
        return False


def is_acestep_running(port: int = 8001) -> bool:
    """Check if ACE-Step server is running."""
    return is_port_in_use(port)


def start_acestep_server(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Start ACE-Step server in background."""
    port = int(env_values.get('ACESTEP_API_PORT', '8001'))
    
    if is_acestep_running(port):
        print_info(f"ACE-Step server already running on port {port}")
        return None
    
    if not is_acestep_installed():
        print_warning("ACE-Step not installed, cannot start server")
        return None
    
    # Ensure convenience output symlink exists
    ensure_acestep_output_symlink()
    
    print_info(f"Starting ACE-Step server on port {port}...")
    
    # Build command - bind to 0.0.0.0 so Docker containers can reach it
    # Note: acestep-api auto-selects LM model based on GPU tier
    cmd = ["uv", "run", "acestep-api", "--host", "0.0.0.0", "--port", str(port)]
    
    # Pass download source if configured
    download_source = env_values.get('ACESTEP_DOWNLOAD_SOURCE', 'auto')
    if download_source and download_source != 'auto':
        cmd.extend(["--download-source", download_source])
    
    # Pass API key if configured
    api_key = env_values.get('ACESTEP_API_KEY', '')
    if api_key:
        cmd.extend(["--api-key", api_key])
    
    try:
        env = get_uv_env()
        # Defer heavy model loading to first request — cuts startup by ~30-40s.
        # Models are loaded on-demand and will be evicted cooperatively anyway.
        env["ACESTEP_LAZY_LOAD"] = "1"
        
        # Capture stderr to a temp file for crash diagnostics
        import tempfile
        stderr_file = tempfile.NamedTemporaryFile(mode='w', suffix='.log', prefix='acestep_', delete=False)
        
        process = start_background_process(
            cmd,
            cwd=str(ACESTEP_DIR),
            env=env,
            stderr=stderr_file,
        )
        
        # Wait for server to start (lazy-load mode: server accepts connections
        # quickly; models load on first request instead of blocking startup)
        import time
        for i in range(120):  # 120 second timeout (downloads may still occur)
            time.sleep(1)
            if is_acestep_running(port):
                print_info(f"ACE-Step server started successfully on port {port}")
                stderr_file.close()
                return process
            if process.poll() is not None:
                stderr_file.close()
                # Read stderr for crash details
                try:
                    with open(stderr_file.name, 'r') as f:
                        err_output = f.read().strip()
                    if err_output:
                        # Show last few lines of error
                        err_lines = err_output.strip().split('\n')[-5:]
                        print_warning("ACE-Step server process died. Last output:")
                        for line in err_lines:
                            print_warning(f"  {line}")
                    else:
                        print_warning("ACE-Step server process died (no error output)")
                except Exception:
                    print_warning("ACE-Step server process died")
                return None
            if i > 0 and i % 10 == 0:
                print_info(f"  Still waiting for ACE-Step... ({i}s) (models may be downloading)")
        
        stderr_file.close()
        print_warning("ACE-Step server failed to start within timeout")
        return None
        
    except Exception as e:
        print_warning(f"Failed to start ACE-Step server: {e}")
        return None


def stop_acestep_server(port: int = 8001):
    """Stop ACE-Step server."""
    if is_acestep_running(port):
        if kill_process_on_port(port):
            print_info(f"Stopped ACE-Step server on port {port}")


def stop_all_gpu_services(env_values: Optional[Dict[str, str]] = None):
    """Stop all host-side GPU services (ACE-Step, Qwen3-TTS, Z-Image, SeedVR2, Canary-STT, AudioSR, Media Toolkit, Real-ESRGAN CPU, Docling, LTX-2 Video).
    
    Called during application shutdown to cleanly free GPU resources.
    Ollama is not stopped as it's an externally managed service.
    """
    if env_values is None:
        env_values = load_current_env()
    
    stop_acestep_server(int(env_values.get('ACESTEP_API_PORT', '8001')))
    stop_qwen3_tts_server(int(env_values.get('QWEN3_TTS_API_PORT', '8002')))
    stop_zimage_server(int(env_values.get('ZIMAGE_API_PORT', '8003')))
    stop_seedvr2_server(int(env_values.get('SEEDVR2_API_PORT', '8004')))
    stop_canary_stt_server(int(env_values.get('CANARY_STT_API_PORT', '8005')))
    stop_audiosr_server(int(env_values.get('AUDIOSR_API_PORT', '8007')))
    stop_media_toolkit_server(int(env_values.get('MEDIA_TOOLKIT_API_PORT', '8008')))
    stop_realesrgan_cpu_server(int(env_values.get('REALESRGAN_CPU_API_PORT', '8009')))
    stop_docling_server(int(env_values.get('DOCLING_API_PORT', '8010')))
    stop_ltx_video_server(int(env_values.get('LTX_VIDEO_API_PORT', '8006')))


# =============================================================================
# Service Manager (host-side agent for backend-driven restarts)
# =============================================================================

_service_manager_proc: Optional[subprocess.Popen] = None


def _get_service_manager_port(env_values: Optional[Dict[str, str]] = None) -> int:
    """Return the configured service manager port from env_values, os.environ, or default."""
    if env_values:
        return int(env_values.get('SERVICE_MANAGER_PORT', '9100'))
    return int(os.environ.get('SERVICE_MANAGER_PORT', '9100'))


def start_service_manager(env_values: Optional[Dict[str, str]] = None) -> Optional[subprocess.Popen]:
    """Start the host-side service manager.
    
    The service manager allows the backend container to stop and restart
    GPU services during VRAM eviction.  It binds 0.0.0.0 so Docker
    containers can reach it via host.docker.internal on all platforms
    (Linux, macOS, Windows).  Access is protected by API key middleware.
    
    Port is read from SERVICE_MANAGER_PORT in env_values / os.environ (default 9100).
    """
    global _service_manager_proc

    port = _get_service_manager_port(env_values)

    manager_script = PROJECT_ROOT / "scripts" / "service_manager.py"
    if not manager_script.exists():
        print_warning("scripts/service_manager.py not found — service auto-restart disabled.")
        return None
    
    # Check if already running
    if is_port_in_use(port):
        print_info(f"Service manager already running on port {port}")
        return None
    
    print_info(f"Starting service manager on port {port}...")
    
    # The service manager imports fastapi/uvicorn which aren't in the
    # system Python.  Use the project .venv (created by start.py's own
    # setup) which has these dependencies.
    venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
    sm_python = str(venv_python) if venv_python.exists() else sys.executable
    
    try:
        proc = start_background_process(
            [sm_python, str(manager_script), "--port", str(port)],
            cwd=str(PROJECT_ROOT),
        )
        _service_manager_proc = proc
        
        # Wait for it to be ready
        import time
        for _attempt in range(10):
            time.sleep(1)
            # If the process already exited, it crashed (e.g. missing deps)
            if proc.poll() is not None:
                print_warning(
                    f"Service manager exited immediately (code {proc.returncode}). "
                    f"Check that fastapi/uvicorn are installed in {sm_python}."
                )
                _service_manager_proc = None
                return None
            if is_port_in_use(port):
                print_info("Service manager started ✓")
                configure_firewall_for_service(port, "Service Manager")
                return proc
        
        print_warning("Service manager may not have started correctly.")
        return proc
        
    except Exception as e:
        print_warning(f"Failed to start service manager: {e}")
        return None


def stop_service_manager(env_values: Optional[Dict[str, str]] = None):
    """Stop the host-side service manager."""
    global _service_manager_proc
    
    port = _get_service_manager_port(env_values)
    
    if _service_manager_proc and _service_manager_proc.poll() is None:
        print_info("Stopping service manager...")
        _service_manager_proc.terminate()
        try:
            _service_manager_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _service_manager_proc.kill()
        _service_manager_proc = None
    elif is_port_in_use(port):
        # Process started externally or from a previous run
        kill_process_on_port(port)
        print_info("Service manager stopped.")


def setup_acestep_if_enabled(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Install and start ACE-Step if enabled."""
    if env_values.get('USE_ACESTEP', 'false').lower() != 'true':
        return None
    
    if env_values.get('ACESTEP_AUTO_START', 'false').lower() != 'true':
        return None
    
    print_section("Setting up ACE-Step Music Generation")
    
    # Install if needed
    if not is_acestep_installed():
        print_info("ACE-Step not installed, installing now...")
        if not install_acestep():
            print_warning("ACE-Step installation failed. You can try manually later.")
            return None
    
    # Configure firewall to allow Docker containers to reach ACE-Step
    port = int(env_values.get('ACESTEP_API_PORT', '8001'))
    configure_firewall_for_service(port, "ACE-Step")
    
    # Start server
    return start_acestep_server(env_values)


# =============================================================================
# Qwen3-TTS Installation & Management (Host-side)
# =============================================================================

QWEN3_TTS_DIR = PROJECT_ROOT / "qwen3-tts"


def is_qwen3_tts_installed() -> bool:
    """Check if Qwen3-TTS venv is set up."""
    return (QWEN3_TTS_DIR / ".venv").exists() and (QWEN3_TTS_DIR / "app.py").exists()


def install_qwen3_tts() -> bool:
    """Set up Qwen3-TTS venv and install dependencies."""
    if is_qwen3_tts_installed():
        print_info("Qwen3-TTS is already installed")
        return True
    
    if not (QWEN3_TTS_DIR / "app.py").exists():
        print_warning(f"Qwen3-TTS app.py not found at {QWEN3_TTS_DIR}")
        return False
    
    print_info("Setting up Qwen3-TTS environment...")
    
    try:
        venv_dir = QWEN3_TTS_DIR / ".venv"
        
        # Create venv
        print_info("Creating Python venv for Qwen3-TTS...")
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60
        )
        if result.returncode != 0:
            print_warning(f"venv creation failed: {result.stderr}")
            return False
        
        # Install dependencies
        pip = get_venv_pip(venv_dir)
        requirements_file = QWEN3_TTS_DIR / "requirements.txt"
        
        print_info("Installing Qwen3-TTS dependencies (this may take several minutes)...")
        print_info("  Downloading: qwen-tts, torch, transformers, FastAPI...")
        result = subprocess.run(
            [str(pip), "install", "-r", str(requirements_file)],
            cwd=str(QWEN3_TTS_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800  # 30 min (torch is large)
        )
        if result.returncode != 0:
            print_warning(f"pip install failed: {result.stderr[:500]}")
            return False
        
        print_info("Qwen3-TTS installed successfully!")
        return True
        
    except subprocess.TimeoutExpired:
        print_warning("Qwen3-TTS installation timed out")
        return False
    except Exception as e:
        print_warning(f"Qwen3-TTS installation failed: {e}")
        return False


def is_qwen3_tts_running(port: int = 8002) -> bool:
    """Check if Qwen3-TTS server is running."""
    return is_port_in_use(port)


def start_qwen3_tts_server(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Start Qwen3-TTS server in background."""
    port = int(env_values.get('QWEN3_TTS_API_PORT', '8002'))
    tier = env_values.get('QWEN3_TTS_TIER', 'auto')
    
    if is_qwen3_tts_running(port):
        print_info(f"Qwen3-TTS server already running on port {port}")
        return None
    
    if not is_qwen3_tts_installed():
        print_warning("Qwen3-TTS not installed, cannot start server")
        return None
    
    print_info(f"Starting Qwen3-TTS server on port {port}...")
    
    venv_dir = QWEN3_TTS_DIR / ".venv"
    python = get_venv_python(venv_dir)
    
    # Build command using uvicorn via the venv Python
    cmd = [str(python), "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", str(port)]
    
    try:
        process = start_background_process(
            cmd,
            cwd=str(QWEN3_TTS_DIR)
        )
        
        # Wait for server to start
        import time
        for i in range(120):  # 2 minute timeout (model may download on first start)
            time.sleep(1)
            if is_qwen3_tts_running(port):
                print_info(f"Qwen3-TTS server started successfully on port {port}")
                return process
            if process.poll() is not None:
                print_warning("Qwen3-TTS server process died")
                return None
            if i > 0 and i % 15 == 0:
                print_info(f"  Still waiting for Qwen3-TTS... ({i}s) (models may be downloading)")
        
        print_warning("Qwen3-TTS server failed to start within timeout")
        return None
        
    except Exception as e:
        print_warning(f"Failed to start Qwen3-TTS server: {e}")
        return None


def stop_qwen3_tts_server(port: int = 8002):
    """Stop Qwen3-TTS server."""
    if is_qwen3_tts_running(port):
        if kill_process_on_port(port):
            print_info(f"Stopped Qwen3-TTS server on port {port}")


def setup_qwen3_tts_if_enabled(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Install and start Qwen3-TTS if enabled."""
    if env_values.get('USE_QWEN3_TTS', 'false').lower() != 'true':
        return None
    
    if env_values.get('QWEN3_TTS_AUTO_START', 'false').lower() != 'true':
        return None
    
    print_section("Setting up Qwen3-TTS Voice Generation")
    
    # Install if needed
    if not is_qwen3_tts_installed():
        print_info("Qwen3-TTS not installed, installing now...")
        if not install_qwen3_tts():
            print_warning("Qwen3-TTS installation failed. You can try manually later.")
            return None
    
    # Configure firewall to allow Docker containers to reach Qwen3-TTS
    port = int(env_values.get('QWEN3_TTS_API_PORT', '8002'))
    configure_firewall_for_service(port, "Qwen3-TTS")
    
    # Start server
    return start_qwen3_tts_server(env_values)


def collect_qwen3_tts_config(current_env: Dict[str, str], gpu_enabled: bool) -> Dict[str, str]:
    """Collect Qwen3-TTS local voice generation settings."""
    print_section("Local Voice Generation (Qwen3-TTS)")
    
    values = {}
    
    print(f"{Colors.BOLD}Qwen3-TTS - Free Local Voice Generation{Colors.RESET}")
    print(f"{Colors.DIM}Qwen3-TTS is a state-of-the-art open-source text-to-speech model by Alibaba.")
    print(f"It supports voice cloning, 9 built-in voices, and voice design from descriptions.")
    print(f"Multi-lingual: Chinese, English, Japanese, Korean.")
    print(f"Runs completely locally - FREE and UNLIMITED!")
    print(f"")
    print(f"{Colors.YELLOW}⚠️  IMPORTANT: Qwen3-TTS requires ~3-6GB disk space for models.{Colors.RESET}")
    print(f"{Colors.DIM}Models will be downloaded automatically on first use.{Colors.RESET}")
    print()
    
    # Detect GPU VRAM
    gpu_vram = 0
    if gpu_enabled:
        gpu_vram = detect_gpu_vram()
        if gpu_vram > 0:
            print(f"{Colors.GREEN}✓ GPU detected with {gpu_vram}MB VRAM{Colors.RESET}")
        else:
            print(f"{Colors.YELLOW}⚠ No GPU detected{Colors.RESET}")
    
    # Determine tier recommendation based on VRAM
    if gpu_vram >= 8000:
        recommended = True
        tier_recommendation = "full"
        tier_note = "Full model (1.7B) - All features: custom voice, voice clone, voice design"
    elif gpu_vram >= 4000:
        recommended = True
        tier_recommendation = "lite"
        tier_note = "Lite model (0.6B) - Voice cloning only"
    else:
        recommended = False
        tier_recommendation = "lite"
        tier_note = "Limited GPU - lite model recommended (voice cloning only)"
    
    if recommended:
        print(f"{Colors.GREEN}Recommended: {tier_recommendation} - {tier_note}{Colors.RESET}")
    else:
        print(f"{Colors.YELLOW}Limited GPU. Lite model (0.6B) can work with 4GB+ VRAM.{Colors.RESET}")
    
    print()
    
    # Ask if user wants Qwen3-TTS
    current_use = current_env.get("USE_QWEN3_TTS", "false").lower() == "true"
    use_qwen3_tts = prompt_yes_no(
        f"Enable Qwen3-TTS local voice generation?",
        default=current_use if current_use else recommended
    )
    values["USE_QWEN3_TTS"] = "true" if use_qwen3_tts else "false"
    
    if use_qwen3_tts:
        # Model tier selection
        print(f"\n{Colors.BOLD}Model Tier{Colors.RESET}")
        print(f"{Colors.DIM}This determines which model to load and available features:{Colors.RESET}")
        print(f"  • full - 1.7B model: voice clone + 9 built-in voices + voice design (~8GB VRAM)")
        print(f"  • lite - 0.6B model: voice clone only (~4GB VRAM)")
        print(f"  • auto - Auto-select based on your GPU VRAM")
        if gpu_vram > 0:
            print(f"{Colors.DIM}  Your GPU: {gpu_vram}MB → Recommended: {tier_recommendation}{Colors.RESET}")
        
        current_tier = current_env.get("QWEN3_TTS_TIER", "auto")
        tier = prompt(
            "Model tier",
            default=current_tier if current_tier != "auto" else tier_recommendation
        )
        values["QWEN3_TTS_TIER"] = tier if tier in ["full", "lite", "auto"] else "auto"
        
        # Idle timeout
        print(f"\n{Colors.BOLD}GPU Memory Management{Colors.RESET}")
        print(f"{Colors.DIM}Qwen3-TTS can unload the model from GPU after a period of inactivity")
        print(f"to free VRAM for other tasks (e.g., ACE-Step music generation).{Colors.RESET}")
        
        current_timeout = current_env.get("QWEN3_TTS_IDLE_TIMEOUT", "300")
        timeout_input = prompt(
            "Idle unload timeout in seconds (0 = never unload)",
            default=current_timeout
        )
        try:
            values["QWEN3_TTS_IDLE_TIMEOUT"] = str(int(timeout_input))
        except ValueError:
            values["QWEN3_TTS_IDLE_TIMEOUT"] = "300"
        
        # Auto-start
        current_auto = current_env.get("QWEN3_TTS_AUTO_START", "true").lower() == "true"
        auto_start = prompt_yes_no(
            "Auto-start Qwen3-TTS server with Money Agents?",
            default=current_auto
        )
        values["QWEN3_TTS_AUTO_START"] = "true" if auto_start else "false"
        
        # Keep other defaults
        values["QWEN3_TTS_API_URL"] = current_env.get("QWEN3_TTS_API_URL", "http://host.docker.internal:8002")
        values["QWEN3_TTS_API_PORT"] = current_env.get("QWEN3_TTS_API_PORT", "8002")
        
        print(f"\n{Colors.GREEN}✓ Qwen3-TTS will be set up on first startup")
        print(f"✓ Models download automatically (~3-6GB)")
        print(f"✓ Voice generation is FREE and unlimited!{Colors.RESET}")
    else:
        # Set defaults for disabled state
        values["QWEN3_TTS_TIER"] = "auto"
        values["QWEN3_TTS_AUTO_START"] = "false"
        values["QWEN3_TTS_IDLE_TIMEOUT"] = "300"
        values["QWEN3_TTS_API_URL"] = "http://host.docker.internal:8002"
        values["QWEN3_TTS_API_PORT"] = "8002"
    
    return values


# =============================================================================
# Z-Image Installation & Management (Host-side)
# =============================================================================

ZIMAGE_DIR = PROJECT_ROOT / "z-image"


def is_zimage_installed() -> bool:
    """Check if Z-Image venv is set up and repo is cloned."""
    return (
        (ZIMAGE_DIR / ".venv").exists()
        and (ZIMAGE_DIR / "app.py").exists()
        and (ZIMAGE_DIR / "Z-Image" / "src").exists()
    )


def install_zimage() -> bool:
    """Clone Z-Image repo, set up venv, and install dependencies."""
    if is_zimage_installed():
        print_info("Z-Image is already installed")
        return True
    
    if not (ZIMAGE_DIR / "app.py").exists():
        print_warning(f"Z-Image app.py not found at {ZIMAGE_DIR}")
        return False
    
    print_info("Setting up Z-Image environment...")
    
    try:
        # Clone Z-Image repository if needed
        zimage_repo = ZIMAGE_DIR / "Z-Image"
        if not zimage_repo.exists():
            print_info("Cloning Z-Image repository from GitHub...")
            result = subprocess.run(
                ["git", "clone", "https://github.com/Tongyi-MAI/Z-Image.git", str(zimage_repo)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300  # 5 min for clone
            )
            if result.returncode != 0:
                print_warning(f"git clone failed: {result.stderr}")
                return False
            print_info("Z-Image repository cloned successfully")
        
        venv_dir = ZIMAGE_DIR / ".venv"
        
        # Create venv
        if not venv_dir.exists():
            print_info("Creating Python venv for Z-Image...")
            result = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60
            )
            if result.returncode != 0:
                print_warning(f"venv creation failed: {result.stderr}")
                return False
        
        # Install dependencies
        pip = get_venv_pip(venv_dir)
        requirements_file = ZIMAGE_DIR / "requirements.txt"
        
        print_info("Installing Z-Image dependencies (this may take several minutes)...")
        print_info("  Downloading: torch, transformers, safetensors, FastAPI...")
        result = subprocess.run(
            [str(pip), "install", "-r", str(requirements_file)],
            cwd=str(ZIMAGE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800  # 30 min (torch is large)
        )
        if result.returncode != 0:
            print_warning(f"pip install failed: {result.stderr[:500]}")
            return False
        
        # Install Z-Image package in editable mode
        print_info("Installing Z-Image package...")
        result = subprocess.run(
            [str(pip), "install", "-e", str(zimage_repo)],
            cwd=str(ZIMAGE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600  # 10 min
        )
        if result.returncode != 0:
            print_warning(f"pip install Z-Image package failed: {result.stderr[:500]}")
            return False
        
        print_info("Z-Image installed successfully!")
        return True
        
    except subprocess.TimeoutExpired:
        print_warning("Z-Image installation timed out")
        return False
    except Exception as e:
        print_warning(f"Z-Image installation failed: {e}")
        return False


def is_zimage_running(port: int = 8003) -> bool:
    """Check if Z-Image server is running."""
    return is_port_in_use(port)


def start_zimage_server(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Start Z-Image server in background."""
    port = int(env_values.get('ZIMAGE_API_PORT', '8003'))
    
    if is_zimage_running(port):
        print_info(f"Z-Image server already running on port {port}")
        return None
    
    if not is_zimage_installed():
        print_warning("Z-Image not installed, cannot start server")
        return None
    
    print_info(f"Starting Z-Image server on port {port}...")
    
    venv_dir = ZIMAGE_DIR / ".venv"
    python = get_venv_python(venv_dir)
    
    # Build command using uvicorn via the venv Python
    cmd = [str(python), "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", str(port)]
    
    try:
        process = start_background_process(
            cmd,
            cwd=str(ZIMAGE_DIR)
        )
        
        # Wait for server to start
        import time
        for i in range(180):  # 3 minute timeout (model may download on first start ~12GB)
            time.sleep(1)
            if is_zimage_running(port):
                print_info(f"Z-Image server started successfully on port {port}")
                return process
            if process.poll() is not None:
                print_warning("Z-Image server process died")
                return None
            if i > 0 and i % 15 == 0:
                print_info(f"  Still waiting for Z-Image... ({i}s) (model may be downloading ~12GB)")
        
        print_warning("Z-Image server failed to start within timeout")
        return None
        
    except Exception as e:
        print_warning(f"Failed to start Z-Image server: {e}")
        return None


def stop_zimage_server(port: int = 8003):
    """Stop Z-Image server."""
    if is_zimage_running(port):
        if kill_process_on_port(port):
            print_info(f"Stopped Z-Image server on port {port}")


def setup_zimage_if_enabled(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Install and start Z-Image if enabled."""
    if env_values.get('USE_ZIMAGE', 'false').lower() != 'true':
        return None
    
    if env_values.get('ZIMAGE_AUTO_START', 'false').lower() != 'true':
        return None
    
    print_section("Setting up Z-Image Generation")
    
    # Install if needed
    if not is_zimage_installed():
        print_info("Z-Image not installed, installing now...")
        if not install_zimage():
            print_warning("Z-Image installation failed. You can try manually later.")
            return None
    
    # Configure firewall to allow Docker containers to reach Z-Image
    port = int(env_values.get('ZIMAGE_API_PORT', '8003'))
    configure_firewall_for_service(port, "Z-Image")
    
    # Start server
    return start_zimage_server(env_values)


def collect_zimage_config(current_env: Dict[str, str], gpu_enabled: bool) -> Dict[str, str]:
    """Collect Z-Image local image generation settings."""
    print_section("Local Image Generation (Z-Image)")
    
    values = {}
    
    print(f"{Colors.BOLD}Z-Image Turbo - Free Local Image Generation{Colors.RESET}")
    print(f"{Colors.DIM}Z-Image is a 6B parameter Diffusion Transformer (DiT) from Tongyi/Alibaba.")
    print(f"The Turbo variant generates high-quality 1024x1024 images in ~3-8 seconds.")
    print(f"8-step distilled inference, no classifier-free guidance needed.")
    print(f"Runs completely locally - FREE and UNLIMITED!")
    print(f"")
    print(f"{Colors.YELLOW}⚠️  IMPORTANT: Z-Image requires ~12GB disk space for model weights.{Colors.RESET}")
    print(f"{Colors.DIM}Models will be downloaded automatically from HuggingFace on first use.{Colors.RESET}")
    print()
    
    # Detect GPU VRAM
    gpu_vram = 0
    if gpu_enabled:
        gpu_vram = detect_gpu_vram()
        if gpu_vram > 0:
            print(f"{Colors.GREEN}✓ GPU detected with {gpu_vram}MB VRAM{Colors.RESET}")
        else:
            print(f"{Colors.YELLOW}⚠ No GPU detected{Colors.RESET}")
    
    # Check if VRAM is sufficient
    if gpu_vram >= 16000:
        recommended = True
        vram_note = "Sufficient VRAM for Z-Image Turbo"
    elif gpu_vram >= 12000:
        recommended = True
        vram_note = "Tight VRAM - may need to unload other models first"
    else:
        recommended = False
        vram_note = "Z-Image Turbo needs ~16-18GB VRAM"
    
    if recommended:
        print(f"{Colors.GREEN}Recommended: Yes - {vram_note}{Colors.RESET}")
    else:
        print(f"{Colors.YELLOW}⚠ {vram_note}{Colors.RESET}")
    
    print()
    
    # Ask if user wants Z-Image
    current_use = current_env.get("USE_ZIMAGE", "false").lower() == "true"
    use_zimage = prompt_yes_no(
        f"Enable Z-Image local image generation?",
        default=current_use if current_use else recommended
    )
    values["USE_ZIMAGE"] = "true" if use_zimage else "false"
    
    if use_zimage:
        # Idle timeout
        print(f"\n{Colors.BOLD}GPU Memory Management{Colors.RESET}")
        print(f"{Colors.DIM}Z-Image can unload the model from GPU after a period of inactivity")
        print(f"to free VRAM for other tasks (e.g., ACE-Step, Qwen3-TTS).{Colors.RESET}")
        
        current_timeout = current_env.get("ZIMAGE_IDLE_TIMEOUT", "300")
        timeout_input = prompt(
            "Idle unload timeout in seconds (0 = never unload)",
            default=current_timeout
        )
        try:
            values["ZIMAGE_IDLE_TIMEOUT"] = str(int(timeout_input))
        except ValueError:
            values["ZIMAGE_IDLE_TIMEOUT"] = "300"
        
        # Auto-start
        current_auto = current_env.get("ZIMAGE_AUTO_START", "true").lower() == "true"
        auto_start = prompt_yes_no(
            "Auto-start Z-Image server with Money Agents?",
            default=current_auto
        )
        values["ZIMAGE_AUTO_START"] = "true" if auto_start else "false"
        
        # Keep other defaults
        values["ZIMAGE_API_URL"] = current_env.get("ZIMAGE_API_URL", "http://host.docker.internal:8003")
        values["ZIMAGE_API_PORT"] = current_env.get("ZIMAGE_API_PORT", "8003")
        values["ZIMAGE_MODEL"] = current_env.get("ZIMAGE_MODEL", "turbo")
        
        print(f"\n{Colors.GREEN}✓ Z-Image will be set up on first startup")
        print(f"✓ Model downloads automatically from HuggingFace (~12GB)")
        print(f"✓ Image generation is FREE and unlimited!{Colors.RESET}")
    else:
        # Set defaults for disabled state
        values["ZIMAGE_AUTO_START"] = "false"
        values["ZIMAGE_IDLE_TIMEOUT"] = "300"
        values["ZIMAGE_API_URL"] = "http://host.docker.internal:8003"
        values["ZIMAGE_API_PORT"] = "8003"
        values["ZIMAGE_MODEL"] = "turbo"
    
    return values


# =============================================================================
# SeedVR2 Upscaler Installation & Management (Host-side)
# =============================================================================

SEEDVR2_DIR = PROJECT_ROOT / "seedvr2-upscaler"


def is_seedvr2_installed() -> bool:
    """Check if SeedVR2 venv is set up and repo is cloned."""
    return (
        (SEEDVR2_DIR / ".venv").exists()
        and (SEEDVR2_DIR / "app.py").exists()
        and (SEEDVR2_DIR / "seedvr2" / "src").exists()
    )


def install_seedvr2() -> bool:
    """Clone SeedVR2 repo, set up venv, and install dependencies."""
    if is_seedvr2_installed():
        print_info("SeedVR2 Upscaler is already installed")
        return True
    
    if not (SEEDVR2_DIR / "app.py").exists():
        print_warning(f"SeedVR2 app.py not found at {SEEDVR2_DIR}")
        return False
    
    print_info("Setting up SeedVR2 Upscaler environment...")
    
    try:
        # Clone SeedVR2 repository if needed
        seedvr2_repo = SEEDVR2_DIR / "seedvr2"
        if not seedvr2_repo.exists():
            print_info("Cloning SeedVR2 repository from GitHub...")
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
                print_warning(f"git clone failed: {result.stderr}")
                return False
            print_info("SeedVR2 repository cloned successfully")
        
        venv_dir = SEEDVR2_DIR / ".venv"
        
        # Create venv
        if not venv_dir.exists():
            print_info("Creating Python venv for SeedVR2...")
            result = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            if result.returncode != 0:
                print_warning(f"venv creation failed: {result.stderr}")
                return False
        
        # Install dependencies
        pip = get_venv_pip(venv_dir)
        
        # Install our server requirements
        requirements_file = SEEDVR2_DIR / "requirements.txt"
        print_info("Installing SeedVR2 server dependencies...")
        result = subprocess.run(
            [str(pip), "install", "-r", str(requirements_file)],
            cwd=str(SEEDVR2_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
        if result.returncode != 0:
            print_warning(f"pip install server requirements failed: {result.stderr[:500]}")
            return False
        
        # Install SeedVR2 upstream requirements (torch, etc.)
        seedvr2_reqs = seedvr2_repo / "requirements.txt"
        if seedvr2_reqs.exists():
            print_info("Installing SeedVR2 upstream dependencies (torch, diffusers, etc.)...")
            print_info("  This may take several minutes...")
            result = subprocess.run(
                [str(pip), "install", "-r", str(seedvr2_reqs)],
                cwd=str(seedvr2_repo),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=1800,
            )
            if result.returncode != 0:
                print_warning(f"pip install SeedVR2 requirements failed: {result.stderr[:500]}")
                return False
        
        print_info("SeedVR2 Upscaler installed successfully!")
        return True
        
    except subprocess.TimeoutExpired:
        print_warning("SeedVR2 installation timed out")
        return False
    except Exception as e:
        print_warning(f"SeedVR2 installation failed: {e}")
        return False


def is_seedvr2_running(port: int = 8004) -> bool:
    """Check if SeedVR2 server is running."""
    return is_port_in_use(port)


def start_seedvr2_server(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Start SeedVR2 Upscaler server in background."""
    port = int(env_values.get('SEEDVR2_API_PORT', '8004'))
    
    if is_seedvr2_running(port):
        print_info(f"SeedVR2 server already running on port {port}")
        return None
    
    if not is_seedvr2_installed():
        print_warning("SeedVR2 not installed, cannot start server")
        return None
    
    print_info(f"Starting SeedVR2 Upscaler server on port {port}...")
    
    venv_dir = SEEDVR2_DIR / ".venv"
    python = get_venv_python(venv_dir)
    
    cmd = [str(python), "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", str(port)]
    
    try:
        process = start_background_process(
            cmd,
            cwd=str(SEEDVR2_DIR),
        )
        
        import time
        for i in range(180):  # 3 min timeout (model download on first start ~4GB)
            time.sleep(1)
            if is_seedvr2_running(port):
                print_info(f"SeedVR2 server started successfully on port {port}")
                return process
            if process.poll() is not None:
                print_warning("SeedVR2 server process died")
                return None
            if i > 0 and i % 15 == 0:
                print_info(f"  Still waiting for SeedVR2... ({i}s) (model may be downloading ~4GB)")
        
        print_warning("SeedVR2 server failed to start within timeout")
        return None
        
    except Exception as e:
        print_warning(f"Failed to start SeedVR2 server: {e}")
        return None


def stop_seedvr2_server(port: int = 8004):
    """Stop SeedVR2 server."""
    if is_seedvr2_running(port):
        if kill_process_on_port(port):
            print_info(f"Stopped SeedVR2 server on port {port}")


def setup_seedvr2_if_enabled(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Install and start SeedVR2 if enabled."""
    if env_values.get('USE_SEEDVR2', 'false').lower() != 'true':
        return None
    
    if env_values.get('SEEDVR2_AUTO_START', 'false').lower() != 'true':
        return None
    
    print_section("Setting up SeedVR2 Upscaler")
    
    if not is_seedvr2_installed():
        print_info("SeedVR2 not installed, installing now...")
        if not install_seedvr2():
            print_warning("SeedVR2 installation failed. You can try manually later.")
            return None
    
    port = int(env_values.get('SEEDVR2_API_PORT', '8004'))
    configure_firewall_for_service(port, "SeedVR2 Upscaler")
    
    return start_seedvr2_server(env_values)


def collect_seedvr2_config(current_env: Dict[str, str], gpu_enabled: bool) -> Dict[str, str]:
    """Collect SeedVR2 Upscaler settings."""
    print_section("Local Image & Video Upscaler (SeedVR2)")
    
    values = {}
    
    print(f"{Colors.BOLD}SeedVR2 - Free Local Image & Video Upscaler{Colors.RESET}")
    print(f"{Colors.DIM}SeedVR2 is a one-step diffusion-based super-resolution model from ByteDance.")
    print(f"It upscales images and videos to higher resolutions while adding realistic detail.")
    print(f"Supports 3B and 7B parameter models with FP8/FP16 precision options.")
    print(f"Runs completely locally - FREE and UNLIMITED!")
    print(f"")
    print(f"{Colors.YELLOW}⚠️  IMPORTANT: SeedVR2 requires ~4GB disk space for model weights (3B FP8).{Colors.RESET}")
    print(f"{Colors.DIM}Models will be downloaded automatically from HuggingFace on first use.{Colors.RESET}")
    print()
    
    # Detect GPU VRAM
    gpu_vram = 0
    if gpu_enabled:
        gpu_vram = detect_gpu_vram()
        if gpu_vram > 0:
            print(f"{Colors.GREEN}✓ GPU detected with {gpu_vram}MB VRAM{Colors.RESET}")
        else:
            print(f"{Colors.YELLOW}⚠ No GPU detected{Colors.RESET}")
    
    # Check if VRAM is sufficient
    if gpu_vram >= 12000:
        recommended = True
        vram_note = "Sufficient VRAM for SeedVR2 (3B FP8 uses ~8-12GB)"
    elif gpu_vram >= 8000:
        recommended = True
        vram_note = "Tight VRAM - will use BlockSwap for memory optimization"
    else:
        recommended = False
        vram_note = "SeedVR2 3B FP8 needs ~8-12GB VRAM"
    
    if recommended:
        print(f"{Colors.GREEN}Recommended: Yes - {vram_note}{Colors.RESET}")
    else:
        print(f"{Colors.YELLOW}⚠ {vram_note}{Colors.RESET}")
    
    print()
    
    # Ask if user wants SeedVR2
    current_use = current_env.get("USE_SEEDVR2", "false").lower() == "true"
    use_seedvr2 = prompt_yes_no(
        f"Enable SeedVR2 image & video upscaler?",
        default=current_use if current_use else recommended,
    )
    values["USE_SEEDVR2"] = "true" if use_seedvr2 else "false"
    
    if use_seedvr2:
        # Model selection
        print(f"\n{Colors.BOLD}Model Selection{Colors.RESET}")
        print(f"{Colors.DIM}Choose a DiT model based on your VRAM and quality needs:{Colors.RESET}")
        print(f"  1. 3B FP8  (3.4GB, ~8-12GB VRAM)  - Recommended for most users")
        print(f"  2. 3B FP16 (6.8GB, ~12-16GB VRAM)  - Better quality, more VRAM")
        print(f"  3. 7B FP8  (8.5GB, ~14-18GB VRAM)  - Higher quality")
        print(f"  4. 7B FP16 (16.5GB, ~20-24GB VRAM) - Maximum quality")
        
        current_model = current_env.get("SEEDVR2_MODEL", "seedvr2_ema_3b_fp8_e4m3fn.safetensors")
        model_choice = prompt("Model choice (1-4)", default="1")
        
        model_map = {
            "1": "seedvr2_ema_3b_fp8_e4m3fn.safetensors",
            "2": "seedvr2_ema_3b_fp16.safetensors",
            "3": "seedvr2_ema_7b_fp8_e4m3fn.safetensors",
            "4": "seedvr2_ema_7b_fp16.safetensors",
        }
        values["SEEDVR2_MODEL"] = model_map.get(model_choice, current_model)
        
        # Idle timeout
        print(f"\n{Colors.BOLD}GPU Memory Management{Colors.RESET}")
        print(f"{Colors.DIM}SeedVR2 can unload the model from GPU after a period of inactivity")
        print(f"to free VRAM for other tasks (e.g., Z-Image, ACE-Step, Qwen3-TTS).{Colors.RESET}")
        
        current_timeout = current_env.get("SEEDVR2_IDLE_TIMEOUT", "300")
        timeout_input = prompt(
            "Idle unload timeout in seconds (0 = never unload)",
            default=current_timeout,
        )
        try:
            values["SEEDVR2_IDLE_TIMEOUT"] = str(int(timeout_input))
        except ValueError:
            values["SEEDVR2_IDLE_TIMEOUT"] = "300"
        
        # Auto-start
        current_auto = current_env.get("SEEDVR2_AUTO_START", "true").lower() == "true"
        auto_start = prompt_yes_no(
            "Auto-start SeedVR2 server with Money Agents?",
            default=current_auto,
        )
        values["SEEDVR2_AUTO_START"] = "true" if auto_start else "false"
        
        # Keep URL/port defaults
        values["SEEDVR2_API_URL"] = current_env.get("SEEDVR2_API_URL", "http://host.docker.internal:8004")
        values["SEEDVR2_API_PORT"] = current_env.get("SEEDVR2_API_PORT", "8004")
        
        print(f"\n{Colors.GREEN}✓ SeedVR2 Upscaler will be set up on first startup")
        print(f"✓ Model downloads automatically from HuggingFace (~4GB)")
        print(f"✓ Image & video upscaling is FREE and unlimited!{Colors.RESET}")
    else:
        values["SEEDVR2_AUTO_START"] = "false"
        values["SEEDVR2_IDLE_TIMEOUT"] = "300"
        values["SEEDVR2_API_URL"] = "http://host.docker.internal:8004"
        values["SEEDVR2_API_PORT"] = "8004"
        values["SEEDVR2_MODEL"] = "seedvr2_ema_3b_fp8_e4m3fn.safetensors"
    
    return values


# =============================================================================
# Canary-STT Speech-to-Text Installation & Management (Host-side)
# =============================================================================

CANARY_STT_DIR = PROJECT_ROOT / "canary-stt"


def is_canary_stt_installed() -> bool:
    """Check if Canary-STT venv is set up."""
    return (
        (CANARY_STT_DIR / ".venv").exists()
        and (CANARY_STT_DIR / "app.py").exists()
    )


def install_canary_stt() -> bool:
    """Set up venv and install dependencies for Canary-STT."""
    if is_canary_stt_installed():
        print_info("Canary-STT is already installed")
        return True

    if not (CANARY_STT_DIR / "app.py").exists():
        print_warning(f"Canary-STT app.py not found at {CANARY_STT_DIR}")
        return False

    print_info("Setting up Canary-STT environment...")

    try:
        venv_dir = CANARY_STT_DIR / ".venv"

        # Create venv
        if not venv_dir.exists():
            print_info("Creating Python venv for Canary-STT...")
            result = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            if result.returncode != 0:
                print_warning(f"venv creation failed: {result.stderr}")
                return False

        # Install dependencies
        pip = get_venv_pip(venv_dir)
        requirements_file = CANARY_STT_DIR / "requirements.txt"
        print_info("Installing Canary-STT dependencies (NeMo toolkit — this may take a while)...")
        result = subprocess.run(
            [str(pip), "install", "-r", str(requirements_file)],
            cwd=str(CANARY_STT_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600,  # NeMo install can be slow
        )
        if result.returncode != 0:
            print_warning(f"pip install failed: {result.stderr[:500]}")
            return False

        print_info("Canary-STT installed successfully!")
        return True

    except subprocess.TimeoutExpired:
        print_warning("Canary-STT installation timed out")
        return False
    except Exception as e:
        print_warning(f"Canary-STT installation failed: {e}")
        return False


def is_canary_stt_running(port: int = 8005) -> bool:
    """Check if Canary-STT server is running."""
    return is_port_in_use(port)


def start_canary_stt_server(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Start Canary-STT server in background."""
    port = int(env_values.get('CANARY_STT_API_PORT', '8005'))

    if is_canary_stt_running(port):
        print_info(f"Canary-STT server already running on port {port}")
        return None

    if not is_canary_stt_installed():
        print_warning("Canary-STT not installed, cannot start server")
        return None

    print_info(f"Starting Canary-STT server on port {port}...")

    venv_dir = CANARY_STT_DIR / ".venv"
    python = get_venv_python(venv_dir)

    cmd = [str(python), "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", str(port)]

    try:
        process = start_background_process(
            cmd,
            cwd=str(CANARY_STT_DIR),
        )

        import time
        for i in range(300):  # 5 min timeout (model download ~5GB on first start)
            time.sleep(1)
            if is_canary_stt_running(port):
                print_info(f"Canary-STT server started successfully on port {port}")
                return process
            if process.poll() is not None:
                print_warning("Canary-STT server process died")
                return None
            if i > 0 and i % 15 == 0:
                print_info(f"  Still waiting for Canary-STT... ({i}s) (model may be downloading ~5GB)")

        print_warning("Canary-STT server failed to start within timeout")
        return None

    except Exception as e:
        print_warning(f"Failed to start Canary-STT server: {e}")
        return None


def stop_canary_stt_server(port: int = 8005):
    """Stop Canary-STT server."""
    if is_canary_stt_running(port):
        if kill_process_on_port(port):
            print_info(f"Stopped Canary-STT server on port {port}")


def setup_canary_stt_if_enabled(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Install and start Canary-STT if enabled."""
    if env_values.get('USE_CANARY_STT', 'false').lower() != 'true':
        return None

    if env_values.get('CANARY_STT_AUTO_START', 'false').lower() != 'true':
        return None

    print_section("Setting up Canary-STT Speech-to-Text")

    if not is_canary_stt_installed():
        print_info("Canary-STT not installed, installing now...")
        if not install_canary_stt():
            print_warning("Canary-STT installation failed. You can try manually later.")
            return None

    port = int(env_values.get('CANARY_STT_API_PORT', '8005'))
    configure_firewall_for_service(port, "Canary-STT")

    return start_canary_stt_server(env_values)


def collect_canary_stt_config(current_env: Dict[str, str], gpu_enabled: bool) -> Dict[str, str]:
    """Collect Canary-STT speech-to-text settings."""
    print_section("Local Speech-to-Text (Canary-STT)")

    values = {}

    print(f"{Colors.BOLD}NVIDIA Canary-Qwen-2.5B - Free Local Speech-to-Text{Colors.RESET}")
    print(f"{Colors.DIM}Canary is a state-of-the-art speech recognition model from NVIDIA.")
    print(f"It uses a FastConformer encoder with a Qwen3-1.7B decoder for")
    print(f"highly accurate English transcription at 418x real-time speed.")
    print(f"Runs completely locally - FREE and UNLIMITED!")
    print(f"")
    print(f"{Colors.YELLOW}⚠️  IMPORTANT: Canary requires ~5GB disk space for model weights.{Colors.RESET}")
    print(f"{Colors.DIM}Model will be downloaded automatically from HuggingFace on first use.{Colors.RESET}")
    print()

    # Detect GPU VRAM
    gpu_vram = 0
    if gpu_enabled:
        gpu_vram = detect_gpu_vram()
        if gpu_vram > 0:
            print(f"{Colors.GREEN}✓ GPU detected with {gpu_vram}MB VRAM{Colors.RESET}")
        else:
            print(f"{Colors.YELLOW}⚠ No GPU detected{Colors.RESET}")

    # Check VRAM requirements (~6-8GB for Canary-Qwen-2.5B in bfloat16)
    if gpu_vram >= 10000:
        recommended = True
        vram_note = "Sufficient VRAM for Canary-STT (~6-8GB needed)"
    elif gpu_vram >= 6000:
        recommended = True
        vram_note = "Tight VRAM - should work for Canary-STT"
    else:
        recommended = False
        vram_note = "Canary-STT needs ~6-8GB VRAM"

    if recommended:
        print(f"{Colors.GREEN}Recommended: Yes - {vram_note}{Colors.RESET}")
    else:
        print(f"{Colors.YELLOW}⚠ {vram_note}{Colors.RESET}")

    print()

    # Ask if user wants Canary-STT
    current_use = current_env.get("USE_CANARY_STT", "false").lower() == "true"
    use_canary = prompt_yes_no(
        f"Enable Canary speech-to-text?",
        default=current_use if current_use else recommended,
    )
    values["USE_CANARY_STT"] = "true" if use_canary else "false"

    if use_canary:
        # Idle timeout
        print(f"\n{Colors.BOLD}GPU Memory Management{Colors.RESET}")
        print(f"{Colors.DIM}Canary-STT can unload the model from GPU after a period of inactivity")
        print(f"to free VRAM for other tasks (e.g., Z-Image, ACE-Step, Qwen3-TTS).{Colors.RESET}")

        current_timeout = current_env.get("CANARY_STT_IDLE_TIMEOUT", "300")
        timeout_input = prompt(
            "Idle unload timeout in seconds (0 = never unload)",
            default=current_timeout,
        )
        try:
            values["CANARY_STT_IDLE_TIMEOUT"] = str(int(timeout_input))
        except ValueError:
            values["CANARY_STT_IDLE_TIMEOUT"] = "300"

        # Auto-start
        current_auto = current_env.get("CANARY_STT_AUTO_START", "true").lower() == "true"
        auto_start = prompt_yes_no(
            "Auto-start Canary-STT server with Money Agents?",
            default=current_auto,
        )
        values["CANARY_STT_AUTO_START"] = "true" if auto_start else "false"

        # Keep URL/port defaults
        values["CANARY_STT_API_URL"] = current_env.get("CANARY_STT_API_URL", "http://host.docker.internal:8005")
        values["CANARY_STT_API_PORT"] = current_env.get("CANARY_STT_API_PORT", "8005")

        print(f"\n{Colors.GREEN}✓ Canary-STT will be set up on first startup")
        print(f"✓ Model downloads automatically from HuggingFace (~5GB)")
        print(f"✓ Speech-to-text is FREE and unlimited!{Colors.RESET}")
    else:
        values["CANARY_STT_AUTO_START"] = "false"
        values["CANARY_STT_IDLE_TIMEOUT"] = "300"
        values["CANARY_STT_API_URL"] = "http://host.docker.internal:8005"
        values["CANARY_STT_API_PORT"] = "8005"

    return values


# =============================================================================
# AudioSR Audio Super-Resolution Installation & Management (Host-side)
# =============================================================================

AUDIOSR_DIR = PROJECT_ROOT / "audiosr"


def is_audiosr_installed() -> bool:
    """Check if AudioSR venv is set up."""
    return (
        (AUDIOSR_DIR / ".venv").exists()
        and (AUDIOSR_DIR / "app.py").exists()
    )


def install_audiosr() -> bool:
    """Set up venv and install dependencies for AudioSR."""
    if is_audiosr_installed():
        print_info("AudioSR is already installed")
        return True

    if not (AUDIOSR_DIR / "app.py").exists():
        print_warning(f"AudioSR app.py not found at {AUDIOSR_DIR}")
        return False

    print_info("Setting up AudioSR environment...")

    try:
        venv_dir = AUDIOSR_DIR / ".venv"

        # Create venv
        if not venv_dir.exists():
            print_info("Creating Python venv for AudioSR...")
            result = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            if result.returncode != 0:
                print_warning(f"venv creation failed: {result.stderr}")
                return False

        # Install dependencies
        pip = get_venv_pip(venv_dir)
        requirements_file = AUDIOSR_DIR / "requirements.txt"

        # AudioSR==0.0.7 pins numpy<=1.23.5 which is incompatible with Python 3.12.
        # Install audiosr with --no-deps first, then install relaxed deps from requirements.txt.
        print_info("Installing AudioSR package (no-deps for Python 3.12 compat)...")
        result = subprocess.run(
            [str(pip), "install", "--no-deps", "audiosr==0.0.7"],
            cwd=str(AUDIOSR_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if result.returncode != 0:
            print_warning(f"audiosr package install failed: {result.stderr[:500]}")
            return False

        print_info("Installing AudioSR dependencies (PyTorch + diffusion models — this may take a while)...")
        result = subprocess.run(
            [str(pip), "install", "-r", str(requirements_file)],
            cwd=str(AUDIOSR_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3600,  # AudioSR install can be slow
        )
        if result.returncode != 0:
            print_warning(f"pip install failed: {result.stderr[:500]}")
            return False

        print_info("AudioSR installed successfully!")
        return True

    except subprocess.TimeoutExpired:
        print_warning("AudioSR installation timed out")
        return False
    except Exception as e:
        print_warning(f"AudioSR installation failed: {e}")
        return False


def is_audiosr_running(port: int = 8007) -> bool:
    """Check if AudioSR server is running."""
    return is_port_in_use(port)


def start_audiosr_server(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Start AudioSR server in background."""
    port = int(env_values.get('AUDIOSR_API_PORT', '8007'))

    if is_audiosr_running(port):
        print_info(f"AudioSR server already running on port {port}")
        return None

    if not is_audiosr_installed():
        print_warning("AudioSR not installed, cannot start server")
        return None

    print_info(f"Starting AudioSR server on port {port}...")

    venv_dir = AUDIOSR_DIR / ".venv"
    python = get_venv_python(venv_dir)

    cmd = [str(python), "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", str(port)]

    try:
        process = start_background_process(
            cmd,
            cwd=str(AUDIOSR_DIR),
        )

        import time
        for i in range(180):  # 3 min timeout
            time.sleep(1)
            if is_audiosr_running(port):
                print_info(f"AudioSR server started successfully on port {port}")
                return process
            if process.poll() is not None:
                print_warning("AudioSR server process died")
                return None
            if i > 0 and i % 15 == 0:
                print_info(f"  Still waiting for AudioSR... ({i}s) (model may be downloading)")

        print_warning("AudioSR server failed to start within timeout")
        return None

    except Exception as e:
        print_warning(f"Failed to start AudioSR server: {e}")
        return None


def stop_audiosr_server(port: int = 8007):
    """Stop AudioSR server."""
    if is_audiosr_running(port):
        if kill_process_on_port(port):
            print_info(f"Stopped AudioSR server on port {port}")


def setup_audiosr_if_enabled(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Install and start AudioSR if enabled."""
    if env_values.get('USE_AUDIOSR', 'false').lower() != 'true':
        return None

    if env_values.get('AUDIOSR_AUTO_START', 'false').lower() != 'true':
        return None

    print_section("Setting up AudioSR Audio Super-Resolution")

    if not is_audiosr_installed():
        print_info("AudioSR not installed, installing now...")
        if not install_audiosr():
            print_warning("AudioSR installation failed. You can try manually later.")
            return None

    port = int(env_values.get('AUDIOSR_API_PORT', '8007'))
    configure_firewall_for_service(port, "AudioSR")

    return start_audiosr_server(env_values)


def collect_audiosr_config(current_env: Dict[str, str], gpu_enabled: bool) -> Dict[str, str]:
    """Collect AudioSR audio super-resolution settings."""
    print_section("Local Audio Super-Resolution (AudioSR)")

    values = {}

    print(f"{Colors.BOLD}AudioSR - Free Local Audio Super-Resolution{Colors.RESET}")
    print(f"{Colors.DIM}AudioSR is a versatile audio super-resolution model using latent diffusion.")
    print(f"It upscales any audio (music, speech, environmental sounds) to 48kHz")
    print(f"high-fidelity output from any input sampling rate.")
    print(f"Runs completely locally - FREE and UNLIMITED!")
    print(f"")
    print(f"{Colors.YELLOW}⚠️  IMPORTANT: AudioSR downloads model weights on first use.{Colors.RESET}")
    print(f"{Colors.DIM}Models are cached locally after initial download.{Colors.RESET}")
    print()

    # Detect GPU VRAM
    gpu_vram = 0
    if gpu_enabled:
        gpu_vram = detect_gpu_vram()
        if gpu_vram > 0:
            print(f"{Colors.GREEN}✓ GPU detected with {gpu_vram}MB VRAM{Colors.RESET}")
        else:
            print(f"{Colors.YELLOW}⚠ No GPU detected{Colors.RESET}")

    # Check VRAM requirements (~4-8GB for AudioSR)
    if gpu_vram >= 10000:
        recommended = True
        vram_note = "Sufficient VRAM for AudioSR (~4-8GB needed)"
    elif gpu_vram >= 4000:
        recommended = True
        vram_note = "Should work for AudioSR"
    else:
        recommended = False
        vram_note = "AudioSR needs ~4-8GB VRAM"

    if recommended:
        print(f"{Colors.GREEN}Recommended: Yes - {vram_note}{Colors.RESET}")
    else:
        print(f"{Colors.YELLOW}⚠ {vram_note}{Colors.RESET}")

    print()

    # Ask if user wants AudioSR
    current_use = current_env.get("USE_AUDIOSR", "false").lower() == "true"
    use_audiosr = prompt_yes_no(
        f"Enable AudioSR audio super-resolution?",
        default=current_use if current_use else recommended,
    )
    values["USE_AUDIOSR"] = "true" if use_audiosr else "false"

    if use_audiosr:
        # Model variant
        print(f"\n{Colors.BOLD}Model Variant{Colors.RESET}")
        print(f"{Colors.DIM}basic = all audio types (music, speech, environmental)")
        print(f"speech = optimized specifically for speech{Colors.RESET}")
        current_model = current_env.get("AUDIOSR_MODEL", "basic")
        model_input = prompt(
            "Model variant (basic/speech)",
            default=current_model,
        )
        values["AUDIOSR_MODEL"] = model_input if model_input in ("basic", "speech") else "basic"

        # Idle timeout
        print(f"\n{Colors.BOLD}GPU Memory Management{Colors.RESET}")
        print(f"{Colors.DIM}AudioSR can unload the model from GPU after a period of inactivity")
        print(f"to free VRAM for other tasks (e.g., Z-Image, ACE-Step, Canary-STT).{Colors.RESET}")

        current_timeout = current_env.get("AUDIOSR_IDLE_TIMEOUT", "300")
        timeout_input = prompt(
            "Idle unload timeout in seconds (0 = never unload)",
            default=current_timeout,
        )
        try:
            values["AUDIOSR_IDLE_TIMEOUT"] = str(int(timeout_input))
        except ValueError:
            values["AUDIOSR_IDLE_TIMEOUT"] = "300"

        # Auto-start
        current_auto = current_env.get("AUDIOSR_AUTO_START", "true").lower() == "true"
        auto_start = prompt_yes_no(
            "Auto-start AudioSR server with Money Agents?",
            default=current_auto,
        )
        values["AUDIOSR_AUTO_START"] = "true" if auto_start else "false"

        # Keep URL/port defaults
        values["AUDIOSR_API_URL"] = current_env.get("AUDIOSR_API_URL", "http://host.docker.internal:8007")
        values["AUDIOSR_API_PORT"] = current_env.get("AUDIOSR_API_PORT", "8007")

        print(f"\n{Colors.GREEN}✓ AudioSR will be set up on first startup")
        print(f"✓ Model downloads automatically on first use")
        print(f"✓ Audio super-resolution is FREE and unlimited!{Colors.RESET}")
    else:
        values["AUDIOSR_AUTO_START"] = "false"
        values["AUDIOSR_IDLE_TIMEOUT"] = "300"
        values["AUDIOSR_MODEL"] = "basic"
        values["AUDIOSR_API_URL"] = "http://host.docker.internal:8007"
        values["AUDIOSR_API_PORT"] = "8007"

    return values


# =============================================================================
# Media Toolkit (FFmpeg) Installation & Management (Host-side)
# =============================================================================

MEDIA_TOOLKIT_DIR = PROJECT_ROOT / "media-toolkit"


def is_media_toolkit_installed() -> bool:
    """Check if Media Toolkit venv is set up."""
    return (
        (MEDIA_TOOLKIT_DIR / ".venv").exists()
        and (MEDIA_TOOLKIT_DIR / "app.py").exists()
    )


def install_media_toolkit() -> bool:
    """Set up venv and install dependencies for Media Toolkit."""
    if is_media_toolkit_installed():
        print_info("Media Toolkit is already installed")
        return True

    if not (MEDIA_TOOLKIT_DIR / "app.py").exists():
        print_warning(f"Media Toolkit app.py not found at {MEDIA_TOOLKIT_DIR}")
        return False

    print_info("Setting up Media Toolkit environment...")

    try:
        venv_dir = MEDIA_TOOLKIT_DIR / ".venv"

        # Create venv
        if not venv_dir.exists():
            print_info("Creating Python venv for Media Toolkit...")
            result = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            if result.returncode != 0:
                print_warning(f"venv creation failed: {result.stderr}")
                return False

        # Install dependencies
        pip = get_venv_pip(venv_dir)
        requirements_file = MEDIA_TOOLKIT_DIR / "requirements.txt"

        print_info("Installing Media Toolkit dependencies...")
        result = subprocess.run(
            [str(pip), "install", "-r", str(requirements_file)],
            cwd=str(MEDIA_TOOLKIT_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if result.returncode != 0:
            print_warning(f"pip install failed: {result.stderr[:500]}")
            return False

        print_info("Media Toolkit installed successfully!")
        return True

    except subprocess.TimeoutExpired:
        print_warning("Media Toolkit installation timed out")
        return False
    except Exception as e:
        print_warning(f"Media Toolkit installation failed: {e}")
        return False


def is_media_toolkit_running(port: int = 8008) -> bool:
    """Check if Media Toolkit server is running."""
    return is_port_in_use(port)


def start_media_toolkit_server(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Start Media Toolkit server in background."""
    port = int(env_values.get('MEDIA_TOOLKIT_API_PORT', '8008'))

    if is_media_toolkit_running(port):
        print_info(f"Media Toolkit server already running on port {port}")
        return None

    if not is_media_toolkit_installed():
        print_warning("Media Toolkit not installed, cannot start server")
        return None

    print_info(f"Starting Media Toolkit server on port {port}...")

    venv_dir = MEDIA_TOOLKIT_DIR / ".venv"
    python = get_venv_python(venv_dir)

    cmd = [str(python), "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", str(port)]

    try:
        process = start_background_process(
            cmd,
            cwd=str(MEDIA_TOOLKIT_DIR),
        )

        import time
        for i in range(30):  # 30s timeout — CPU-only, starts fast
            time.sleep(1)
            if is_media_toolkit_running(port):
                print_info(f"Media Toolkit server started successfully on port {port}")
                return process
            if process.poll() is not None:
                print_warning("Media Toolkit server process died")
                return None

        print_warning("Media Toolkit server failed to start within timeout")
        return None

    except Exception as e:
        print_warning(f"Failed to start Media Toolkit server: {e}")
        return None


def stop_media_toolkit_server(port: int = 8008):
    """Stop Media Toolkit server."""
    if is_media_toolkit_running(port):
        if kill_process_on_port(port):
            print_info(f"Stopped Media Toolkit server on port {port}")


def setup_media_toolkit_if_enabled(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Install and start Media Toolkit if enabled."""
    if env_values.get('USE_MEDIA_TOOLKIT', 'false').lower() != 'true':
        return None

    if env_values.get('MEDIA_TOOLKIT_AUTO_START', 'false').lower() != 'true':
        return None

    print_section("Setting up Media Toolkit (FFmpeg)")

    if not is_media_toolkit_installed():
        print_info("Media Toolkit not installed, installing now...")
        if not install_media_toolkit():
            print_warning("Media Toolkit installation failed. You can try manually later.")
            return None

    port = int(env_values.get('MEDIA_TOOLKIT_API_PORT', '8008'))
    configure_firewall_for_service(port, "Media Toolkit")

    return start_media_toolkit_server(env_values)


def collect_media_toolkit_config(current_env: Dict[str, str], gpu_enabled: bool) -> Dict[str, str]:
    """Collect Media Toolkit settings."""
    print_section("Media Toolkit (FFmpeg-based media composition)")

    values = {}

    print(f"{Colors.BOLD}Media Toolkit - FFmpeg Media Composition{Colors.RESET}")
    print(f"{Colors.DIM}The Media Toolkit enables agents to split, combine, and mix media files.")
    print(f"It provides operations like extracting audio, combining video + audio,")
    print(f"mixing multiple audio tracks, creating slideshows, trimming, and more.")
    print(f"CPU-only — no GPU required. Uses FFmpeg under the hood.")
    print(f"Runs completely locally - FREE and UNLIMITED!{Colors.RESET}")
    print()

    # Ask if user wants Media Toolkit
    current_use = current_env.get("USE_MEDIA_TOOLKIT", "false").lower() == "true"
    use_media_toolkit = prompt_yes_no(
        f"Enable Media Toolkit for media composition?",
        default=current_use if current_use else True,  # Recommend by default (CPU-only, lightweight)
    )
    values["USE_MEDIA_TOOLKIT"] = "true" if use_media_toolkit else "false"

    if use_media_toolkit:
        values["MEDIA_TOOLKIT_AUTO_START"] = "true"
        values["MEDIA_TOOLKIT_API_URL"] = current_env.get("MEDIA_TOOLKIT_API_URL", "http://host.docker.internal:8008")
        values["MEDIA_TOOLKIT_API_PORT"] = current_env.get("MEDIA_TOOLKIT_API_PORT", "8008")

        print(f"\n{Colors.GREEN}✓ Media Toolkit will be set up on first startup")
        print(f"✓ CPU-only — no GPU required, starts instantly")
        print(f"✓ Media composition is FREE and unlimited!{Colors.RESET}")
    else:
        values["MEDIA_TOOLKIT_AUTO_START"] = "false"
        values["MEDIA_TOOLKIT_API_URL"] = "http://host.docker.internal:8008"
        values["MEDIA_TOOLKIT_API_PORT"] = "8008"

    return values


# =============================================================================
# Real-ESRGAN CPU Upscaler Installation & Management (Host-side)
# =============================================================================

REALESRGAN_CPU_DIR = PROJECT_ROOT / "realesrgan-cpu"


def is_realesrgan_cpu_installed() -> bool:
    """Check if Real-ESRGAN CPU venv is set up."""
    return (
        (REALESRGAN_CPU_DIR / ".venv").exists()
        and (REALESRGAN_CPU_DIR / "app.py").exists()
    )


def install_realesrgan_cpu() -> bool:
    """Set up venv and install dependencies for Real-ESRGAN CPU."""
    if is_realesrgan_cpu_installed():
        print_info("Real-ESRGAN CPU is already installed")
        return True

    if not (REALESRGAN_CPU_DIR / "app.py").exists():
        print_warning(f"Real-ESRGAN CPU app.py not found at {REALESRGAN_CPU_DIR}")
        return False

    print_info("Setting up Real-ESRGAN CPU environment...")

    try:
        venv_dir = REALESRGAN_CPU_DIR / ".venv"

        # Create venv
        if not venv_dir.exists():
            print_info("Creating Python venv for Real-ESRGAN CPU...")
            result = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            if result.returncode != 0:
                print_warning(f"venv creation failed: {result.stderr}")
                return False

        pip = get_venv_pip(venv_dir)

        # Install CPU-only PyTorch first
        print_info("Installing CPU-only PyTorch (this may take a few minutes)...")
        result = subprocess.run(
            [str(pip), "install", "torch", "torchvision",
             "--index-url", "https://download.pytorch.org/whl/cpu"],
            cwd=str(REALESRGAN_CPU_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        if result.returncode != 0:
            print_warning(f"PyTorch CPU install failed: {result.stderr[:500]}")
            return False

        # Install remaining dependencies
        requirements_file = REALESRGAN_CPU_DIR / "requirements.txt"
        print_info("Installing Real-ESRGAN CPU dependencies...")
        result = subprocess.run(
            [str(pip), "install", "-r", str(requirements_file)],
            cwd=str(REALESRGAN_CPU_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        if result.returncode != 0:
            print_warning(f"pip install failed: {result.stderr[:500]}")
            return False

        print_info("Real-ESRGAN CPU installed successfully!")
        return True

    except subprocess.TimeoutExpired:
        print_warning("Real-ESRGAN CPU installation timed out")
        return False
    except Exception as e:
        print_warning(f"Real-ESRGAN CPU installation failed: {e}")
        return False


def is_realesrgan_cpu_running(port: int = 8009) -> bool:
    """Check if Real-ESRGAN CPU server is running."""
    return is_port_in_use(port)


def start_realesrgan_cpu_server(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Start Real-ESRGAN CPU server in background."""
    port = int(env_values.get('REALESRGAN_CPU_API_PORT', '8009'))

    if is_realesrgan_cpu_running(port):
        print_info(f"Real-ESRGAN CPU server already running on port {port}")
        return None

    if not is_realesrgan_cpu_installed():
        print_warning("Real-ESRGAN CPU not installed, cannot start server")
        return None

    print_info(f"Starting Real-ESRGAN CPU server on port {port}...")

    venv_dir = REALESRGAN_CPU_DIR / ".venv"
    python = get_venv_python(venv_dir)

    cmd = [str(python), "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", str(port)]

    try:
        process = start_background_process(
            cmd,
            cwd=str(REALESRGAN_CPU_DIR),
        )

        import time
        for i in range(90):  # 90s timeout — model loading on CPU is slow
            time.sleep(1)
            if is_realesrgan_cpu_running(port):
                print_info(f"Real-ESRGAN CPU server started successfully on port {port}")
                return process
            if process.poll() is not None:
                print_warning("Real-ESRGAN CPU server process died")
                return None

        print_warning("Real-ESRGAN CPU server failed to start within timeout")
        return None

    except Exception as e:
        print_warning(f"Failed to start Real-ESRGAN CPU server: {e}")
        return None


def stop_realesrgan_cpu_server(port: int = 8009):
    """Stop Real-ESRGAN CPU server."""
    if is_realesrgan_cpu_running(port):
        if kill_process_on_port(port):
            print_info(f"Stopped Real-ESRGAN CPU server on port {port}")


def setup_realesrgan_cpu_if_enabled(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Install and start Real-ESRGAN CPU if enabled."""
    if env_values.get('USE_REALESRGAN_CPU', 'false').lower() != 'true':
        return None

    if env_values.get('REALESRGAN_CPU_AUTO_START', 'false').lower() != 'true':
        return None

    print_section("Setting up Real-ESRGAN CPU Upscaler")

    if not is_realesrgan_cpu_installed():
        print_info("Real-ESRGAN CPU not installed, installing now...")
        if not install_realesrgan_cpu():
            print_warning("Real-ESRGAN CPU installation failed. You can try manually later.")
            return None

    port = int(env_values.get('REALESRGAN_CPU_API_PORT', '8009'))
    configure_firewall_for_service(port, "Real-ESRGAN CPU")

    return start_realesrgan_cpu_server(env_values)


def collect_realesrgan_cpu_config(current_env: Dict[str, str], gpu_enabled: bool) -> Dict[str, str]:
    """Collect Real-ESRGAN CPU upscaler settings."""
    print_section("Real-ESRGAN CPU Upscaler (CPU-only image & video upscaling)")

    values = {}

    print(f"{Colors.BOLD}Real-ESRGAN CPU Upscaler{Colors.RESET}")
    print(f"{Colors.DIM}CPU-only image & video upscaling using Real-ESRGAN neural networks.")
    print(f"No GPU required — works as a fallback when GPU is busy or unavailable.")
    print(f"Models auto-download on first use (~50-100MB each).")
    print(f"WARNING: CPU upscaling is much slower than GPU upscaling.")
    if gpu_enabled:
        print(f"For GPU-accelerated upscaling, use SeedVR2 Upscaler instead.")
    print(f"Runs completely locally - FREE and UNLIMITED!{Colors.RESET}")
    print()

    # Ask if user wants Real-ESRGAN CPU
    current_use = current_env.get("USE_REALESRGAN_CPU", "false").lower() == "true"
    # Recommend enabling if no GPU, or as always-available fallback
    default_enable = current_use if current_use else (not gpu_enabled)
    use_realesrgan = prompt_yes_no(
        f"Enable Real-ESRGAN CPU upscaler?",
        default=default_enable,
    )
    values["USE_REALESRGAN_CPU"] = "true" if use_realesrgan else "false"

    if use_realesrgan:
        # Model selection
        print(f"\n{Colors.BOLD}Select upscaling model:{Colors.RESET}")
        print(f"  1. realesr-animevideov3  — Anime/video optimized, fastest (recommended)")
        print(f"  2. realesrgan-x4plus     — Best quality for photographs")
        print(f"  3. realesrnet-x4plus     — Faster alternative for photographs")
        print(f"  4. realesrgan-x4plus-anime — Anime/illustration images")
        
        current_model = current_env.get("REALESRGAN_CPU_MODEL", "realesr-animevideov3")
        model_map = {
            "1": "realesr-animevideov3",
            "2": "realesrgan-x4plus",
            "3": "realesrnet-x4plus",
            "4": "realesrgan-x4plus-anime",
        }
        
        # Find current model number
        current_num = "1"
        for num, name in model_map.items():
            if name == current_model:
                current_num = num
                break
        
        model_choice = prompt(f"Model choice [1-4]", default=current_num)
        values["REALESRGAN_CPU_MODEL"] = model_map.get(model_choice, "realesr-animevideov3")

        values["REALESRGAN_CPU_AUTO_START"] = "true"
        values["REALESRGAN_CPU_API_URL"] = current_env.get("REALESRGAN_CPU_API_URL", "http://host.docker.internal:8009")
        values["REALESRGAN_CPU_API_PORT"] = current_env.get("REALESRGAN_CPU_API_PORT", "8009")

        print(f"\n{Colors.GREEN}✓ Real-ESRGAN CPU upscaler will be set up on first startup")
        print(f"✓ Model: {values['REALESRGAN_CPU_MODEL']}")
        print(f"✓ CPU-only — no GPU required")
        print(f"✓ Upscaling is FREE and unlimited!{Colors.RESET}")
    else:
        values["REALESRGAN_CPU_AUTO_START"] = "false"
        values["REALESRGAN_CPU_API_URL"] = "http://host.docker.internal:8009"
        values["REALESRGAN_CPU_API_PORT"] = "8009"
        values["REALESRGAN_CPU_MODEL"] = "realesr-animevideov3"

    return values


# =============================================================================
# Docling Document Parser Installation & Management (Host-side)
# =============================================================================

DOCLING_DIR = PROJECT_ROOT / "docling-parser"


def is_docling_installed() -> bool:
    """Check if Docling venv is set up."""
    return (
        (DOCLING_DIR / ".venv").exists()
        and (DOCLING_DIR / "app.py").exists()
    )


def install_docling() -> bool:
    """Set up venv and install dependencies for Docling."""
    if is_docling_installed():
        print_info("Docling is already installed")
        return True

    if not (DOCLING_DIR / "app.py").exists():
        print_warning(f"Docling app.py not found at {DOCLING_DIR}")
        return False

    print_info("Setting up Docling environment...")

    try:
        venv_dir = DOCLING_DIR / ".venv"

        # Create venv
        if not venv_dir.exists():
            print_info("Creating Python venv for Docling...")
            result = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            if result.returncode != 0:
                print_warning(f"venv creation failed: {result.stderr}")
                return False

        pip = get_venv_pip(venv_dir)

        # Install dependencies
        requirements_file = DOCLING_DIR / "requirements.txt"
        print_info("Installing Docling dependencies (this may take a few minutes)...")
        result = subprocess.run(
            [str(pip), "install", "-r", str(requirements_file)],
            cwd=str(DOCLING_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        if result.returncode != 0:
            print_warning(f"pip install failed: {result.stderr[:500]}")
            return False

        print_info("Docling installed successfully!")
        return True

    except subprocess.TimeoutExpired:
        print_warning("Docling installation timed out")
        return False
    except Exception as e:
        print_warning(f"Docling installation failed: {e}")
        return False


def is_docling_running(port: int = 8010) -> bool:
    """Check if Docling server is running."""
    return is_port_in_use(port)


def start_docling_server(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Start Docling server in background."""
    port = int(env_values.get('DOCLING_API_PORT', '8010'))

    if is_docling_running(port):
        print_info(f"Docling server already running on port {port}")
        return None

    if not is_docling_installed():
        print_warning("Docling not installed, cannot start server")
        return None

    print_info(f"Starting Docling server on port {port}...")

    venv_dir = DOCLING_DIR / ".venv"
    python = get_venv_python(venv_dir)

    cmd = [str(python), "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", str(port)]

    try:
        process = start_background_process(
            cmd,
            cwd=str(DOCLING_DIR),
        )

        import time
        for i in range(90):  # 90s timeout — model loading on first run
            time.sleep(1)
            if is_docling_running(port):
                print_info(f"Docling server started successfully on port {port}")
                return process
            if process.poll() is not None:
                print_warning("Docling server process died")
                return None

        print_warning("Docling server failed to start within timeout")
        return None

    except Exception as e:
        print_warning(f"Failed to start Docling server: {e}")
        return None


def stop_docling_server(port: int = 8010):
    """Stop Docling server."""
    if is_docling_running(port):
        if kill_process_on_port(port):
            print_info(f"Stopped Docling server on port {port}")


def setup_docling_if_enabled(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Install and start Docling if enabled."""
    if env_values.get('USE_DOCLING', 'false').lower() != 'true':
        return None

    if env_values.get('DOCLING_AUTO_START', 'false').lower() != 'true':
        return None

    print_section("Setting up Docling Document Parser")

    if not is_docling_installed():
        print_info("Docling not installed, installing now...")
        if not install_docling():
            print_warning("Docling installation failed. You can try manually later.")
            return None

    port = int(env_values.get('DOCLING_API_PORT', '8010'))
    configure_firewall_for_service(port, "Docling")

    return start_docling_server(env_values)


def collect_docling_config(current_env: Dict[str, str], gpu_enabled: bool) -> Dict[str, str]:
    """Collect Docling Document Parser settings."""
    print_section("Docling Document Parser (CPU-only document parsing & conversion)")

    values = {}

    print(f"{Colors.BOLD}Docling Document Parser{Colors.RESET}")
    print(f"{Colors.DIM}Parse and convert documents (PDF, DOCX, PPTX, HTML, images, etc.)")
    print(f"into structured Markdown or JSON using IBM Docling.")
    print(f"Features advanced table recognition and OCR for scanned documents.")
    print(f"CPU-only — no GPU required.")
    print(f"Runs completely locally - FREE and UNLIMITED!{Colors.RESET}")
    print()

    # Ask if user wants Docling
    current_use = current_env.get("USE_DOCLING", "false").lower() == "true"
    use_docling = prompt_yes_no(
        f"Enable Docling Document Parser?",
        default=current_use if current_use else True,
    )
    values["USE_DOCLING"] = "true" if use_docling else "false"

    if use_docling:
        values["DOCLING_AUTO_START"] = "true"
        values["DOCLING_API_URL"] = current_env.get("DOCLING_API_URL", "http://host.docker.internal:8010")
        values["DOCLING_API_PORT"] = current_env.get("DOCLING_API_PORT", "8010")

        print(f"\n{Colors.GREEN}✓ Docling will be set up on first startup")
        print(f"✓ CPU-only — no GPU required, works on any system")
        print(f"✓ Document parsing is FREE and unlimited!{Colors.RESET}")
    else:
        values["DOCLING_AUTO_START"] = "false"
        values["DOCLING_API_URL"] = "http://host.docker.internal:8010"
        values["DOCLING_API_PORT"] = "8010"

    return values


# =============================================================================
# LTX-2 Video Generation Installation & Management (Host-side)
# =============================================================================

LTX_VIDEO_DIR = PROJECT_ROOT / "ltx-video"


def is_ltx_video_installed() -> bool:
    """Check if LTX-2 Video venv is set up."""
    return (
        (LTX_VIDEO_DIR / ".venv").exists()
        and (LTX_VIDEO_DIR / "app.py").exists()
    )


def install_ltx_video() -> bool:
    """Set up venv and install dependencies for LTX-2 Video."""
    if is_ltx_video_installed():
        print_info("LTX-2 Video is already installed")
        return True

    if not (LTX_VIDEO_DIR / "app.py").exists():
        print_warning(f"LTX-2 Video app.py not found at {LTX_VIDEO_DIR}")
        return False

    print_info("Setting up LTX-2 Video environment...")

    try:
        venv_dir = LTX_VIDEO_DIR / ".venv"

        # Create venv
        if not venv_dir.exists():
            print_info("Creating Python venv for LTX-2 Video...")
            result = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            if result.returncode != 0:
                print_warning(f"venv creation failed: {result.stderr}")
                return False

        # Install base dependencies
        pip = get_venv_pip(venv_dir)
        requirements_file = LTX_VIDEO_DIR / "requirements.txt"
        print_info("Installing LTX-2 Video dependencies...")
        result = subprocess.run(
            [str(pip), "install", "-r", str(requirements_file)],
            cwd=str(LTX_VIDEO_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
        if result.returncode != 0:
            print_warning(f"pip install failed: {result.stderr[:500]}")
            return False

        # Install ltx-core from git
        print_info("Installing ltx-core from git...")
        result = subprocess.run(
            [str(pip), "install", "git+https://github.com/Lightricks/LTX-2.git#subdirectory=packages/ltx-core"],
            cwd=str(LTX_VIDEO_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        if result.returncode != 0:
            print_warning(f"ltx-core install failed: {result.stderr[:500]}")
            return False

        # Install ltx-pipelines from git
        print_info("Installing ltx-pipelines from git...")
        result = subprocess.run(
            [str(pip), "install", "git+https://github.com/Lightricks/LTX-2.git#subdirectory=packages/ltx-pipelines"],
            cwd=str(LTX_VIDEO_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        if result.returncode != 0:
            print_warning(f"ltx-pipelines install failed: {result.stderr[:500]}")
            return False

        # Download model files (~77 GB total from Lightricks/LTX-2 HuggingFace)
        if not download_ltx_video_models():
            print_warning("Model download failed or incomplete — you can retry later with:")
            print_warning(f"  {LTX_VIDEO_DIR / '.venv' / 'bin' / 'python'} {LTX_VIDEO_DIR / 'download_models.py'}")
            # Don't fail install — venv is set up, models can be downloaded later

        print_info("LTX-2 Video installed successfully!")
        return True

    except subprocess.TimeoutExpired:
        print_warning("LTX-2 Video installation timed out")
        return False
    except Exception as e:
        print_warning(f"LTX-2 Video installation failed: {e}")
        return False


def download_ltx_video_models() -> bool:
    """
    Download LTX-2 model files (~77 GB) from HuggingFace.

    Runs download_models.py inside the LTX-2 venv (which has huggingface_hub).
    Supports resume — interrupted downloads continue where they left off.
    """
    venv_dir = LTX_VIDEO_DIR / ".venv"
    if not venv_dir.exists():
        print_warning("LTX-2 venv not found, cannot download models")
        return False

    download_script = LTX_VIDEO_DIR / "download_models.py"
    if not download_script.exists():
        print_warning("download_models.py not found")
        return False

    python = get_venv_python(venv_dir)

    # First check what's needed
    result = subprocess.run(
        [str(python), str(download_script), "--check"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    if result.returncode == 0:
        print_info("All LTX-2 model files already present")
        return True

    # Models are missing — show what's needed and download
    print_info("Downloading LTX-2 model files from HuggingFace...")
    print(f"{Colors.BOLD}  This is a large download (~77 GB). It supports resume if interrupted.{Colors.RESET}")
    print(f"  Files go to: {PROJECT_ROOT / 'models' / 'ltx-2'}")

    try:
        result = subprocess.run(
            [str(python), str(download_script)],
            cwd=str(LTX_VIDEO_DIR),
            text=True,
            encoding="utf-8",
            errors="replace",
            # No timeout — download is ~77 GB and can take hours
        )
        return result.returncode == 0
    except KeyboardInterrupt:
        print_warning("Download interrupted — run again to resume")
        return False
    except Exception as e:
        print_warning(f"Model download error: {e}")
        return False


def is_ltx_video_running(port: int = 8006) -> bool:
    """Check if LTX-2 Video server is running."""
    return is_port_in_use(port)


def start_ltx_video_server(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Start LTX-2 Video server in background."""
    port = int(env_values.get('LTX_VIDEO_API_PORT', '8006'))

    if is_ltx_video_running(port):
        print_info(f"LTX-2 Video server already running on port {port}")
        return None

    if not is_ltx_video_installed():
        print_warning("LTX-2 Video not installed, cannot start server")
        return None

    print_info(f"Starting LTX-2 Video server on port {port}...")

    venv_dir = LTX_VIDEO_DIR / ".venv"
    python = get_venv_python(venv_dir)

    cmd = [str(python), "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", str(port)]

    # Build environment with CUDA config for sequential component loading
    import os as _os
    env = _os.environ.copy()
    env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

    # Set GPU affinity if configured
    gpu_indices = env_values.get('GPU_LTX_VIDEO', '')
    if gpu_indices:
        env["CUDA_VISIBLE_DEVICES"] = gpu_indices

    try:
        process = start_background_process(
            cmd,
            cwd=str(LTX_VIDEO_DIR),
            env=env,
        )

        import time
        for i in range(120):  # 2 min timeout (server starts fast, model loads lazily)
            time.sleep(1)
            if is_ltx_video_running(port):
                print_info(f"LTX-2 Video server started successfully on port {port}")
                return process
            if process.poll() is not None:
                print_warning("LTX-2 Video server process died")
                return None
            if i > 0 and i % 15 == 0:
                print_info(f"  Still waiting for LTX-2 Video... ({i}s)")

        print_warning("LTX-2 Video server failed to start within timeout")
        return None

    except Exception as e:
        print_warning(f"Failed to start LTX-2 Video server: {e}")
        return None


def stop_ltx_video_server(port: int = 8006):
    """Stop LTX-2 Video server."""
    if is_ltx_video_running(port):
        if kill_process_on_port(port):
            print_info(f"Stopped LTX-2 Video server on port {port}")


def setup_ltx_video_if_enabled(env_values: Dict[str, str]) -> Optional[subprocess.Popen]:
    """Install and start LTX-2 Video if enabled."""
    if env_values.get('USE_LTX_VIDEO', 'false').lower() != 'true':
        return None

    if env_values.get('LTX_VIDEO_AUTO_START', 'false').lower() != 'true':
        return None

    print_section("Setting up LTX-2 Video Generation")

    if not is_ltx_video_installed():
        print_info("LTX-2 Video not installed, installing now...")
        if not install_ltx_video():
            print_warning("LTX-2 Video installation failed. You can try manually later.")
            return None

    port = int(env_values.get('LTX_VIDEO_API_PORT', '8006'))
    configure_firewall_for_service(port, "LTX-2 Video")

    return start_ltx_video_server(env_values)


def collect_ltx_video_config(current_env: Dict[str, str], gpu_enabled: bool) -> Dict[str, str]:
    """Collect LTX-2 Video generation settings."""
    print_section("Local Video Generation (LTX-2)")

    values = {}

    print(f"{Colors.BOLD}LTX-2 19B - Free Local Video Generation{Colors.RESET}")
    print(f"{Colors.DIM}LTX-2 is a state-of-the-art video generation model from Lightricks.")
    print(f"It generates high-quality 768x512 video with synchronized audio,")
    print(f"up to 10 seconds at 24fps, and runs completely locally - FREE and UNLIMITED!")
    print(f"")
    print(f"{Colors.YELLOW}⚠️  IMPORTANT: LTX-2 requires ~24GB VRAM and ~30GB disk space for models.{Colors.RESET}")
    print(f"{Colors.DIM}Models must be downloaded manually from HuggingFace (see docs).{Colors.RESET}")
    print()

    # Detect GPU VRAM
    gpu_vram = 0
    if gpu_enabled:
        gpu_vram = detect_gpu_vram()
        if gpu_vram > 0:
            print(f"{Colors.GREEN}✓ GPU detected with {gpu_vram}MB VRAM{Colors.RESET}")
        else:
            print(f"{Colors.YELLOW}⚠ No GPU detected{Colors.RESET}")

    # Check VRAM requirements (~20-21GB peak for LTX-2 19B with sequential loading)
    if gpu_vram >= 24000:
        recommended = True
        vram_note = "Sufficient VRAM for LTX-2 (~20-21GB peak)"
    elif gpu_vram >= 20000:
        recommended = True
        vram_note = "Tight VRAM - should work for LTX-2 with sequential loading"
    else:
        recommended = False
        vram_note = "LTX-2 needs ~20-21GB VRAM (24GB GPU recommended)"

    if recommended:
        print(f"{Colors.GREEN}Recommended: Yes - {vram_note}{Colors.RESET}")
    else:
        print(f"{Colors.YELLOW}⚠ {vram_note}{Colors.RESET}")

    print()

    # Ask if user wants LTX-2
    current_use = current_env.get("USE_LTX_VIDEO", "false").lower() == "true"
    use_ltx = prompt_yes_no(
        f"Enable LTX-2 video generation?",
        default=current_use if current_use else recommended,
    )
    values["USE_LTX_VIDEO"] = "true" if use_ltx else "false"

    if use_ltx:
        # Model directory
        print(f"\n{Colors.BOLD}Model Directory{Colors.RESET}")
        print(f"{Colors.DIM}Directory where LTX-2 model files are stored.")
        print(f"Expected files: ltx-2-19B-distilled-fp8.safetensors,")
        print(f"gemma_3_12B_it_fp8_e4m3fn.safetensors, spatial_upscaler_2x.safetensors{Colors.RESET}")
        current_model_dir = current_env.get("LTX_VIDEO_MODEL_DIR", "./models/ltx-2")
        model_dir = prompt(
            "Model directory path",
            default=current_model_dir,
        )
        values["LTX_VIDEO_MODEL_DIR"] = model_dir

        # Idle timeout
        print(f"\n{Colors.BOLD}GPU Memory Management{Colors.RESET}")
        print(f"{Colors.DIM}LTX-2 can unload the model from GPU after a period of inactivity")
        print(f"to free VRAM for other tasks (e.g., Z-Image, ACE-Step, Qwen3-TTS).{Colors.RESET}")

        current_timeout = current_env.get("LTX_VIDEO_IDLE_TIMEOUT", "300")
        timeout_input = prompt(
            "Idle unload timeout in seconds (0 = never unload)",
            default=current_timeout,
        )
        try:
            values["LTX_VIDEO_IDLE_TIMEOUT"] = str(int(timeout_input))
        except ValueError:
            values["LTX_VIDEO_IDLE_TIMEOUT"] = "300"

        # Auto-start
        current_auto = current_env.get("LTX_VIDEO_AUTO_START", "true").lower() == "true"
        auto_start = prompt_yes_no(
            "Auto-start LTX-2 Video server with Money Agents?",
            default=current_auto,
        )
        values["LTX_VIDEO_AUTO_START"] = "true" if auto_start else "false"

        # Keep URL/port defaults
        values["LTX_VIDEO_API_URL"] = current_env.get("LTX_VIDEO_API_URL", "http://host.docker.internal:8006")
        values["LTX_VIDEO_API_PORT"] = current_env.get("LTX_VIDEO_API_PORT", "8006")

        print(f"\n{Colors.GREEN}✓ LTX-2 Video will be set up on first startup")
        print(f"✓ Generates 768x512 video with audio @ 24fps")
        print(f"✓ Video generation is FREE and unlimited!{Colors.RESET}")
    else:
        values["LTX_VIDEO_AUTO_START"] = "false"
        values["LTX_VIDEO_IDLE_TIMEOUT"] = "300"
        values["LTX_VIDEO_API_URL"] = "http://host.docker.internal:8006"
        values["LTX_VIDEO_API_PORT"] = "8006"
        values["LTX_VIDEO_MODEL_DIR"] = "./models/ltx-2"

    return values


def collect_acestep_config(current_env: Dict[str, str], gpu_enabled: bool) -> Dict[str, str]:
    """Collect ACE-Step local music generation settings."""
    print_section("Local Music Generation (ACE-Step)")
    
    values = {}
    
    print(f"{Colors.BOLD}ACE-Step 1.5 - Free Local Music Generation{Colors.RESET}")
    print(f"{Colors.DIM}ACE-Step is a state-of-the-art open-source music generation model.")
    print(f"It produces commercial-grade songs with lyrics, supports 50+ languages,")
    print(f"and runs completely locally - FREE and UNLIMITED!")
    print(f"")
    print(f"{Colors.YELLOW}⚠️  IMPORTANT: ACE-Step requires approximately 5GB disk space for models.{Colors.RESET}")
    print(f"{Colors.DIM}Models will be downloaded automatically on first use.{Colors.RESET}")
    print()
    
    # Detect GPU VRAM
    gpu_vram = 0
    if gpu_enabled:
        gpu_vram = detect_gpu_vram()
        if gpu_vram > 0:
            print(f"{Colors.GREEN}✓ GPU detected with {gpu_vram}MB VRAM{Colors.RESET}")
        else:
            print(f"{Colors.YELLOW}⚠ No GPU detected{Colors.RESET}")
    
    # Determine recommendation based on VRAM
    if gpu_vram >= 4000:
        recommended = True
    else:
        recommended = False
    
    if recommended:
        print(f"{Colors.GREEN}✓ Your GPU has enough VRAM for ACE-Step{Colors.RESET}")
    else:
        print(f"{Colors.YELLOW}⚠ No capable GPU detected. ACE-Step may be very slow on CPU.{Colors.RESET}")
    
    print()
    
    # Ask if user wants ACE-Step
    current_use = current_env.get("USE_ACESTEP", "false").lower() == "true"
    use_acestep = prompt_yes_no(
        f"Enable ACE-Step local music generation? (~5GB download)",
        default=current_use if current_use else recommended
    )
    values["USE_ACESTEP"] = "true" if use_acestep else "false"
    
    if use_acestep:
        # Model type (turbo vs base) - ASK THIS FIRST since it's the main choice
        print(f"\n{Colors.BOLD}Generation Model (DiT){Colors.RESET}")
        print(f"{Colors.DIM}This controls generation speed vs. quality:{Colors.RESET}")
        print(f"  • base  - Best quality (27-60 steps), on par with Suno (16GB+ VRAM)")
        print(f"  • turbo - Faster but lower quality (8 steps), for limited VRAM")
        
        # Default to base for high VRAM (16GB+), turbo for lower
        current_model = current_env.get("ACESTEP_MODEL", "")
        if current_model:
            model_default = current_model
        elif gpu_vram >= 16000:
            model_default = "base"
        else:
            model_default = "turbo"
        
        model = prompt(
            "Generation model",
            default=model_default
        )
        values["ACESTEP_MODEL"] = model if model in ["turbo", "base"] else "base"
        
        # Note: The ACE-Step server auto-selects the best LM model based on GPU VRAM.
        # No need to prompt for tier/LM size.
        
        # Auto-start
        current_auto = current_env.get("ACESTEP_AUTO_START", "true").lower() == "true"
        auto_start = prompt_yes_no(
            "Auto-start ACE-Step server with Money Agents?",
            default=current_auto
        )
        values["ACESTEP_AUTO_START"] = "true" if auto_start else "false"
        
        # Keep other defaults
        values["ACESTEP_API_URL"] = current_env.get("ACESTEP_API_URL", "http://host.docker.internal:8001")
        values["ACESTEP_API_PORT"] = current_env.get("ACESTEP_API_PORT", "8001")
        values["ACESTEP_API_KEY"] = current_env.get("ACESTEP_API_KEY", "")
        values["ACESTEP_DOWNLOAD_SOURCE"] = current_env.get("ACESTEP_DOWNLOAD_SOURCE", "auto")
        
        print(f"\n{Colors.GREEN}✓ ACE-Step will be cloned from GitHub on first startup")
        print(f"✓ Models download automatically (~5GB)")
        print(f"✓ Generation is FREE and unlimited!{Colors.RESET}")
    else:
        # Set defaults for disabled state
        values["ACESTEP_MODEL"] = "turbo"
        values["ACESTEP_AUTO_START"] = "false"
        values["ACESTEP_API_URL"] = "http://host.docker.internal:8001"
        values["ACESTEP_API_PORT"] = "8001"
        values["ACESTEP_API_KEY"] = ""
        values["ACESTEP_DOWNLOAD_SOURCE"] = "auto"
    
    return values


# =============================================================================
# ComfyUI API Management
# =============================================================================

COMFY_WORKFLOWS_DIR = PROJECT_ROOT / "comfy-workflows"


def build_comfyui_tools_env() -> str:
    """
    Build the COMFYUI_TOOLS env var from discovered ComfyUI workflow APIs.
    
    Format: name|display_name|port|comfyui_url|gpu_indices (semicolon-separated)
    
    Reads each workflow's config.yaml for the comfyui URL and gpu_indices.
    The gpu_indices field default to "0" but can be overridden in config.yaml
    as `gpu_indices: "0,1"`.
    
    Example output:
        ltx-2|LTX-2 Video|9902|http://host.docker.internal:8189|0
    """
    apis = discover_comfy_apis()
    enabled = [a for a in apis if a.get('enabled', True)]
    if not enabled:
        return ""
    
    entries = []
    for api in enabled:
        name = api['name']
        display_name = api.get('display_name', name)
        port = api['port']
        
        # Read the comfyui URL and GPU indices from config.yaml
        comfyui_url = "http://host.docker.internal:8189"  # default
        gpu_indices = "0"  # default
        
        config_file = api.get('path')
        if config_file:
            config_path = Path(config_file) / "config.yaml" if not str(config_file).endswith('.yaml') else Path(config_file)
            if config_path.exists():
                try:
                    import yaml
                    with open(config_path) as f:
                        config = yaml.safe_load(f)
                    raw_url = config.get('comfyui', {}).get('url', comfyui_url)
                    # Convert localhost to host.docker.internal for Docker access
                    comfyui_url = raw_url.replace('localhost', 'host.docker.internal').replace('127.0.0.1', 'host.docker.internal')
                    gpu_indices = str(config.get('gpu_indices', gpu_indices))
                except Exception:
                    pass
        
        entries.append(f"{name}|{display_name}|{port}|{comfyui_url}|{gpu_indices}")
    
    return ";".join(entries)


def update_env_comfyui_tools():
    """
    Update the COMFYUI_TOOLS env var in the .env file from discovered workflows.
    
    Called after starting ComfyUI APIs to ensure the backend knows about them.
    """
    comfyui_tools = build_comfyui_tools_env()
    current_env = load_current_env()
    if current_env.get('COMFYUI_TOOLS', '') != comfyui_tools:
        current_env['COMFYUI_TOOLS'] = comfyui_tools
        save_env_file(current_env)


def discover_comfy_apis() -> list:
    """
    Discover all ComfyUI APIs in comfy-workflows/.
    
    Returns list of dicts with:
        - name: API name
        - display_name: Human-readable name
        - port: Port number
        - path: Path to API directory
        - enabled: Whether the API is enabled
        - has_config: Whether it has a config.yaml (new-style) or just app.py (legacy)
    """
    apis = []
    
    if not COMFY_WORKFLOWS_DIR.exists():
        return apis
    
    try:
        import yaml
    except ImportError:
        return apis
    
    for api_dir in COMFY_WORKFLOWS_DIR.iterdir():
        if not api_dir.is_dir():
            continue
        
        app_file = api_dir / "app.py"
        if not app_file.exists():
            continue
        
        config_file = api_dir / "config.yaml"
        
        if config_file.exists():
            # New-style API with config.yaml
            try:
                with open(config_file) as f:
                    config = yaml.safe_load(f)
                
                apis.append({
                    'name': config.get('api', {}).get('name', api_dir.name),
                    'display_name': config.get('api', {}).get('display_name', api_dir.name),
                    'port': config.get('api', {}).get('port', 9901),
                    'path': api_dir,
                    'enabled': config.get('api', {}).get('enabled', True),
                    'has_config': True,
                })
            except Exception:
                pass
        else:
            # Legacy API without config.yaml - parse port from app.py
            try:
                content = app_file.read_text()
                port_match = re.search(r'port[=:]\s*(\d+)', content)
                port = int(port_match.group(1)) if port_match else 9901
                
                apis.append({
                    'name': api_dir.name,
                    'display_name': api_dir.name.replace('-', ' ').title(),
                    'port': port,
                    'path': api_dir,
                    'enabled': True,  # Legacy APIs are always enabled
                    'has_config': False,
                })
            except Exception:
                pass
    
    return sorted(apis, key=lambda x: x['port'])


def is_comfy_api_running(port: int) -> bool:
    """Check if a ComfyUI API is running on the given port."""
    return is_port_in_use(port)


def start_comfy_api(api: dict) -> Optional[subprocess.Popen]:
    """
    Start a ComfyUI API in the background.
    
    Returns the process handle if successful, None otherwise.
    """
    api_dir = api['path']
    port = api['port']
    name = api['name']
    
    # Check if already running
    if is_comfy_api_running(port):
        print_info(f"{name} already running on port {port}")
        return None
    
    # Ensure venv exists
    venv_dir = api_dir / ".venv"
    if not venv_dir.exists():
        print_info(f"Creating venv for {name}...")
        try:
            subprocess.run(
                [sys.executable, "-m", "venv", str(venv_dir)],
                check=True,
                capture_output=True
            )
            
            pip = get_venv_pip(venv_dir)
            print_info(f"Installing dependencies for {name}...")
            subprocess.run(
                [str(pip), "install", "-r", str(api_dir / "requirements.txt")],
                check=True,
                capture_output=True
            )
        except subprocess.CalledProcessError as e:
            print_error(f"Failed to set up venv for {name}: {e}")
            return None
    
    # Start the API
    python = get_venv_python(venv_dir)
    
    # Check if new-style (config.yaml) or legacy (direct python app.py)
    if api.get('has_config', False):
        # New-style: use uvicorn
        cmd = [str(python), "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)]
    else:
        # Legacy: run app.py directly
        cmd = [str(python), "app.py"]
    
    try:
        process = start_background_process(
            cmd,
            cwd=str(api_dir)
        )
        
        # Wait a moment and check if it's running
        import time
        time.sleep(2)
        
        if process.poll() is not None:
            print_error(f"Failed to start {name}")
            return None
        
        print_info(f"Started {name} on port {port}")
        return process
        
    except Exception as e:
        print_error(f"Failed to start {name}: {e}")
        return None


def stop_comfy_api(api: dict):
    """Stop a ComfyUI API by port (kills any process listening on that port)."""
    port = api['port']
    name = api['name']
    
    if not is_comfy_api_running(port):
        return
    
    if kill_process_on_port(port):
        print_info(f"Stopped {name}")


def start_all_comfy_apis():
    """Start all enabled ComfyUI APIs."""
    apis = discover_comfy_apis()
    
    if not apis:
        return
    
    enabled = [a for a in apis if a['enabled']]
    if not enabled:
        return
    
    print_info(f"Starting {len(enabled)} ComfyUI API(s)...")
    
    for api in enabled:
        start_comfy_api(api)


def stop_all_comfy_apis():
    """Stop all running ComfyUI APIs."""
    apis = discover_comfy_apis()
    
    for api in apis:
        stop_comfy_api(api)


def list_comfy_apis():
    """Print a list of all ComfyUI APIs and their status."""
    apis = discover_comfy_apis()
    
    if not apis:
        print(f"{Colors.DIM}No ComfyUI APIs found in comfy-workflows/{Colors.RESET}")
        return
    
    print(f"\n{Colors.BOLD}ComfyUI APIs:{Colors.RESET}")
    print()
    
    for api in apis:
        running = is_comfy_api_running(api['port'])
        enabled = api['enabled']
        
        if running:
            status = f"{Colors.GREEN}● running{Colors.RESET}"
        elif enabled:
            status = f"{Colors.YELLOW}○ stopped{Colors.RESET}"
        else:
            status = f"{Colors.DIM}○ disabled{Colors.RESET}"
        
        style = "new" if api['has_config'] else "legacy"
        print(f"  {api['name']:20} port:{api['port']}  {status}  [{style}]")
    
    print()


def run_full_setup():
    """Run the full setup wizard."""
    clear_screen()
    print_header()
    
    print(f"{Colors.BOLD}Welcome to the Money Agents Setup Wizard!{Colors.RESET}")
    print(f"\n{Colors.DIM}This wizard will help you configure the application.")
    print(f"You can run this script again anytime to update your configuration")
    print(f"or reset your admin password.{Colors.RESET}\n")
    
    input(f"Press Enter to continue...")
    
    # Prerequisites were already checked in main(), but verify Docker is still running
    # (user may have stopped it between menu and selecting full setup)
    if not check_docker_running():
        print_error("Docker is no longer running. Please start Docker and try again.")
        sys.exit(1)
    
    # Load current config if exists
    current_env = load_current_env()
    
    # Collect all settings
    admin_creds = collect_admin_credentials(current_env)
    llm_keys = collect_llm_api_keys(current_env)
    
    # Check if any cloud LLM is configured
    has_cloud_llm = any([
        llm_keys.get("OPENAI_API_KEY", "").replace("your_openai_api_key_here", ""),
        llm_keys.get("ANTHROPIC_API_KEY", "").replace("your_anthropic_api_key_here", ""),
        llm_keys.get("Z_AI_API_KEY", "").replace("your_zai_api_key_here", ""),
    ])
    
    ollama_config = collect_ollama_config(current_env, has_cloud_llm)
    
    # Tool configuration: offer "enable all" preset or custom
    if ENABLE_ALL_TOOLS:
        # --all flag: skip prompts, auto-select everything
        tool_choice = 1
    else:
        print_section("Tool Configuration")
        print(f"  {Colors.BOLD}How would you like to configure tools?{Colors.RESET}\n")
        print(f"  Option 1 auto-detects your GPU VRAM and enables all compatible tools")
        print(f"  with sensible defaults. You can fine-tune later via 'Update configuration'.\n")
        tool_choice = prompt_choice("Select tool configuration mode:", [
            "Enable all compatible tools (recommended)",
            "Custom — choose each tool individually",
        ])
    
    if tool_choice == 1:
        # Enable all tools with auto-detected defaults
        # Merge Ollama config from generate_all_tools_defaults with user's
        # Ollama answers (they may have customized models/URL)
        all_tool_values = generate_all_tools_defaults(current_env)
        # User already answered Ollama questions — keep those answers
        all_tool_values.update(ollama_config)
        # Still ask about LND and Serper since they need user credentials
        lnd_config = collect_lnd_config(current_env)
        # Ask about Serper/search (needs API key or clone URL)
        serper_values = collect_serper_config(current_env)
        other_keys = {**all_tool_values, **lnd_config, **serper_values}
        features = {}
    else:
        # Custom flow: ask GPU toggle FIRST, then tools individually
        features = collect_feature_settings(current_env)
        # Pass GPU decision into current_env so tool collectors see it
        custom_env = {**current_env, **features}
        other_keys = collect_other_api_keys(custom_env)
        lnd_config = collect_lnd_config(current_env)
        other_keys.update(lnd_config)
    
    # Merge all values
    all_values = {**current_env, **llm_keys, **ollama_config, **other_keys, **features}
    
    # Summary
    print_section("Configuration Summary")
    
    print(f"  Admin Email:     {admin_creds['email']}")
    print(f"  Admin Username:  {admin_creds['username']}")
    print()
    print(f"  OpenAI:          {'✓ Configured' if all_values.get('OPENAI_API_KEY', '').replace('your_openai_api_key_here', '') else '✗ Not set'}")
    print(f"  Anthropic:       {'✓ Configured' if all_values.get('ANTHROPIC_API_KEY', '').replace('your_anthropic_api_key_here', '') else '✗ Not set'}")
    print(f"  Z.ai:            {'✓ Configured' if all_values.get('Z_AI_API_KEY', '').replace('your_zai_api_key_here', '') else '✗ Not set'}")
    print(f"  Ollama:          {'✓ Enabled' if all_values.get('USE_OLLAMA') == 'true' else '✗ Disabled'}")
    print(f"  ElevenLabs:      {'✓ Configured' if all_values.get('ELEVENLABS_API_KEY', '').replace('your_elevenlabs_api_key_here', '') else '✗ Not set'}")
    use_clone = all_values.get('USE_SERPER_CLONE') == 'true'
    serper_configured = all_values.get('SERPER_API_KEY', '').replace('your_serper_api_key_here', '')
    if use_clone and serper_configured:
        clone_url = all_values.get('SERPER_CLONE_URL', '')
        print(f"  Web Search:      ✓ Serper Clone ({clone_url})")
    elif serper_configured:
        print(f"  Web Search:      ✓ Serper (serper.dev)")
    else:
        print(f"  Web Search:      ⚠ Not set (limited functionality)")
    print(f"  Suno:            {'✓ Enabled' if all_values.get('USE_SUNO') == 'true' else '✗ Disabled'}")
    print(f"  GPU:             {'✓ Enabled' if all_values.get('USE_GPU') == 'true' else '✗ Disabled'}")
    if all_values.get('USE_ACESTEP') == 'true':
        model = all_values.get('ACESTEP_MODEL', 'turbo')
        print(f"  ACE-Step:        ✓ Enabled ({model} model)")
    else:
        print(f"  ACE-Step:        ✗ Disabled")
    if all_values.get('USE_QWEN3_TTS') == 'true':
        tts_tier = all_values.get('QWEN3_TTS_TIER', 'auto')
        print(f"  Qwen3-TTS:       ✓ Enabled ({tts_tier} tier)")
    else:
        print(f"  Qwen3-TTS:       ✗ Disabled")
    if all_values.get('USE_LND') == 'true':
        lnd_url = all_values.get('LND_REST_URL', '')
        via_tor = ' via Tor' if '.onion' in lnd_url else ''
        print(f"  Bitcoin (LND):   ✓ Enabled ({lnd_url}{via_tor})")
    else:
        print(f"  Bitcoin (LND):   ✗ Disabled")
    
    # Additional tools summary (only show if any are enabled)
    extra_tools = []
    for tool_key, label in [
        ("USE_ZIMAGE", "Z-Image"), ("USE_SEEDVR2", "SeedVR2"),
        ("USE_CANARY_STT", "Canary-STT"), ("USE_AUDIOSR", "AudioSR"),
        ("USE_LTX_VIDEO", "LTX-2 Video"), ("USE_MEDIA_TOOLKIT", "Media Toolkit"),
        ("USE_REALESRGAN_CPU", "Real-ESRGAN CPU"), ("USE_DOCLING", "Docling"),
        ("USE_NOSTR", "Nostr"),
    ]:
        if all_values.get(tool_key) == 'true':
            extra_tools.append(label)
    if extra_tools:
        print(f"  Other Tools:     ✓ {', '.join(extra_tools)}")
    
    # Download size estimate
    download_sizes = estimate_download_sizes(all_values)
    if download_sizes["total_gb"] > 0:
        print()
        print(f"  {Colors.YELLOW}Estimated model downloads: ~{download_sizes['total_gb']}GB{Colors.RESET}")
        print(f"  {Colors.DIM}(Models download on first use of each tool){Colors.RESET}")
    
    print()
    if not prompt_yes_no("Save this configuration and create admin user?", default=True):
        print_info("Setup cancelled.")
        return
    
    # Save .env file
    print_section("Applying Configuration")
    
    # Populate COMFYUI_TOOLS from discovered workflow APIs
    all_values['COMFYUI_TOOLS'] = build_comfyui_tools_env()
    
    save_env_file(all_values)
    
    # Auto-generate a secure SECRET_KEY if the default placeholder is still set
    all_values = ensure_secure_secret_key(all_values)

    # Auto-generate secure REDIS_PASSWORD and FLOWER_PASSWORD if defaults are still set
    all_values = ensure_secure_passwords(all_values)

    # Auto-generate GPU_SERVICE_API_KEY and SERVICE_MANAGER_API_KEY if not set
    all_values = ensure_service_api_keys(all_values)

    # Derive SERVICE_MANAGER_URL from SERVICE_MANAGER_PORT (keeps docker-compose in sync)
    all_values = ensure_service_manager_url(all_values)

    # Record the host operating system so the UI can show the correct start command
    import platform as _platform
    host_system = _platform.system().lower()
    if host_system == "darwin":
        all_values['HOST_OS'] = 'macos'
    elif host_system == "windows":
        all_values['HOST_OS'] = 'windows'
    else:
        all_values['HOST_OS'] = 'linux'
    
    # Generate docker-compose.override.yml with GPU config + DNS overrides
    use_gpu = all_values.get('USE_GPU', 'false').lower() == 'true'
    extra_hosts = build_extra_hosts_from_env(all_values)
    generate_docker_compose_override(PROJECT_ROOT, use_gpu=use_gpu, extra_hosts=extra_hosts)
    
    # Start services
    print_info("Starting Docker services...")
    run_docker_compose_command(["up", "-d", "--build"])
    
    print_info("Waiting for services to be ready...")
    import time
    time.sleep(15)  # Wait for services to fully start
    
    # Create admin user
    print_info("Creating admin user...")
    if create_admin_user(admin_creds['email'], admin_creds['username'], admin_creds['password']):
        print_info(f"Admin user '{admin_creds['username']}' created successfully!")
    
    # Run database migrations
    print_info("Running database migrations...")
    subprocess.run(
        ["docker", "compose", "exec", "-T", "backend", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        capture_output=True
    )
    
    # Initialize resources and tools
    run_resource_detection()
    run_init_tools_catalog()
    
    # Setup ACE-Step if enabled (install and start on host)
    acestep_process = setup_acestep_if_enabled(all_values)
    
    # Setup Qwen3-TTS if enabled (install and start on host)
    qwen3_tts_process = setup_qwen3_tts_if_enabled(all_values)
    
    # Setup Z-Image if enabled (install and start on host)
    zimage_process = setup_zimage_if_enabled(all_values)
    
    # Setup SeedVR2 Upscaler if enabled (install and start on host)
    seedvr2_process = setup_seedvr2_if_enabled(all_values)
    
    # Setup Canary-STT if enabled (install and start on host)
    canary_stt_process = setup_canary_stt_if_enabled(all_values)
    
    # Setup AudioSR if enabled (install and start on host)
    audiosr_process = setup_audiosr_if_enabled(all_values)
    
    # Setup Media Toolkit if enabled (install and start on host)
    media_toolkit_process = setup_media_toolkit_if_enabled(all_values)
    
    # Setup LTX-2 Video if enabled (install and start on host)
    ltx_video_process = setup_ltx_video_if_enabled(all_values)
    
    # Done!
    print_section("Setup Complete! 🎉")
    print(f"""
  {Colors.GREEN}Your Money Agents instance is ready!{Colors.RESET}

  {Colors.BOLD}Access the application:{Colors.RESET}
  - Frontend:  {Colors.CYAN}http://localhost:5173{Colors.RESET}
  - API Docs:  {Colors.CYAN}http://localhost:8000/docs{Colors.RESET}

  {Colors.BOLD}Login with:{Colors.RESET}
  - Email/Username: {admin_creds['email']} or {admin_creds['username']}
  - Password: (the password you just set)

  {Colors.BOLD}Useful commands:{Colors.RESET}
  - View logs:      {Colors.DIM}bash dev.sh logs backend{Colors.RESET}
  - Stop services:  {Colors.DIM}bash dev.sh stop{Colors.RESET}
  - Reset password: {Colors.DIM}python start.py{Colors.RESET}

  {Colors.BOLD}Next steps:{Colors.RESET}
  1. Log in to the application
  2. Review detected resources in Resource Management
  3. Check the Tools Catalog for available integrations
  4. Create your first campaign proposal!
""")
    
    # Wait for Ctrl+C to stop
    wait_for_ctrl_c()
    
    print()
    print_section("Stopping Application")
    print_info("Shutdown requested via Ctrl+C...")
    stop_all_comfy_apis()
    stop_all_gpu_services()
    run_docker_compose_command(["stop"])
    
    print_section("Application Stopped! ✓")
    print(f"""
  {Colors.GREEN}All services have been stopped.{Colors.RESET}

  {Colors.BOLD}To start again:{Colors.RESET}
  - Run: {Colors.CYAN}python start.py{Colors.RESET}
""")

def run_password_reset():
    """Run password reset flow.
    
    Starts core Docker services temporarily if they aren't already running
    (the backend must be up to execute the password reset script), then
    restores the previous container state afterward.
    """
    clear_screen()
    print_header()
    
    print_section("Password Reset")
    
    print(f"{Colors.DIM}Enter the email or username of the account to reset.{Colors.RESET}\n")
    
    identifier = prompt("Email or username", required=True)
    
    while True:
        password = prompt("New password (min 8 characters)", password=True, required=True)
        valid, msg = validate_password(password)
        if not valid:
            print_error(msg)
            continue
        
        confirm = prompt("Confirm password", password=True, required=True)
        if password != confirm:
            print_error("Passwords do not match")
            continue
        break
    
    services_were_started = start_services_if_needed()
    
    if reset_admin_password(identifier, password):
        print_info(f"Password reset successfully for '{identifier}'!")
    else:
        print_error("Password reset failed.")
    
    # Restore containers to their previous state
    if services_were_started:
        print_info("Stopping services that were started for the password reset...")
        run_docker_compose_command(["stop"])
    
    print()
    input(f"{Colors.DIM}Press Enter to return to the main menu...{Colors.RESET}")

def run_config_update():
    """Run configuration update flow."""
    clear_screen()
    print_header()
    
    print(f"{Colors.BOLD}Update Configuration{Colors.RESET}")
    print(f"\n{Colors.DIM}Current values will be shown as defaults.")
    print(f"Press Enter to keep the current value.{Colors.RESET}\n")
    
    # Load current config
    current_env = load_current_env()
    
    # Collect all settings (admin credentials not needed for update)
    llm_keys = collect_llm_api_keys(current_env)
    
    has_cloud_llm = any([
        llm_keys.get("OPENAI_API_KEY", "").replace("your_openai_api_key_here", ""),
        llm_keys.get("ANTHROPIC_API_KEY", "").replace("your_anthropic_api_key_here", ""),
        llm_keys.get("Z_AI_API_KEY", "").replace("your_zai_api_key_here", ""),
    ])
    
    ollama_config = collect_ollama_config(current_env, has_cloud_llm)
    
    # Tool configuration: offer "enable all" preset or custom
    print_section("Tool Configuration")
    print(f"  {Colors.BOLD}How would you like to configure tools?{Colors.RESET}\n")
    tool_choice = prompt_choice("Select tool configuration mode:", [
        "Enable all compatible tools (auto-detect GPU)",
        "Custom — choose each tool individually",
    ])
    
    if tool_choice == 1:
        all_tool_values = generate_all_tools_defaults(current_env)
        all_tool_values.update(ollama_config)
        lnd_config = collect_lnd_config(current_env)
        serper_values = collect_serper_config(current_env)
        other_keys = {**all_tool_values, **lnd_config, **serper_values}
        features = {}
    else:
        features = collect_feature_settings(current_env)
        custom_env = {**current_env, **features}
        other_keys = collect_other_api_keys(custom_env)
        lnd_config = collect_lnd_config(current_env)
        other_keys.update(lnd_config)
    
    # Merge values (keeping existing values not modified)
    all_values = {**current_env, **llm_keys, **ollama_config, **other_keys, **features}
    
    print_section("Configuration Summary")
    print(f"  OpenAI:          {'✓ Configured' if all_values.get('OPENAI_API_KEY', '').replace('your_openai_api_key_here', '') else '✗ Not set'}")
    print(f"  Anthropic:       {'✓ Configured' if all_values.get('ANTHROPIC_API_KEY', '').replace('your_anthropic_api_key_here', '') else '✗ Not set'}")
    print(f"  Z.ai:            {'✓ Configured' if all_values.get('Z_AI_API_KEY', '').replace('your_zai_api_key_here', '') else '✗ Not set'}")
    print(f"  Ollama:          {'✓ Enabled' if all_values.get('USE_OLLAMA') == 'true' else '✗ Disabled'}")
    print(f"  ElevenLabs:      {'✓ Configured' if all_values.get('ELEVENLABS_API_KEY', '').replace('your_elevenlabs_api_key_here', '') else '✗ Not set'}")
    use_clone = all_values.get('USE_SERPER_CLONE') == 'true'
    serper_configured = all_values.get('SERPER_API_KEY', '').replace('your_serper_api_key_here', '')
    if use_clone and serper_configured:
        clone_url = all_values.get('SERPER_CLONE_URL', '')
        print(f"  Web Search:      ✓ Serper Clone ({clone_url})")
    elif serper_configured:
        print(f"  Web Search:      ✓ Serper (serper.dev)")
    else:
        print(f"  Web Search:      ⚠ Not set")
    print(f"  Suno:            {'✓ Enabled' if all_values.get('USE_SUNO') == 'true' else '✗ Disabled'}")
    print(f"  GPU:             {'✓ Enabled' if all_values.get('USE_GPU') == 'true' else '✗ Disabled'}")
    if all_values.get('USE_ACESTEP') == 'true':
        model = all_values.get('ACESTEP_MODEL', 'turbo')
        print(f"  ACE-Step:        ✓ Enabled ({model} model)")
    else:
        print(f"  ACE-Step:        ✗ Disabled")
    if all_values.get('USE_QWEN3_TTS') == 'true':
        tts_tier = all_values.get('QWEN3_TTS_TIER', 'auto')
        print(f"  Qwen3-TTS:       ✓ Enabled ({tts_tier} tier)")
    else:
        print(f"  Qwen3-TTS:       ✗ Disabled")
    if all_values.get('USE_LND') == 'true':
        lnd_url = all_values.get('LND_REST_URL', '')
        via_tor = ' via Tor' if '.onion' in lnd_url else ''
        print(f"  Bitcoin (LND):   ✓ Enabled ({lnd_url}{via_tor})")
    else:
        print(f"  Bitcoin (LND):   ✗ Disabled")
    
    # Show other tools status
    other_tools = []
    tool_keys = {
        'USE_ZIMAGE': 'Z-Image',
        'USE_SEEDVR2_UPSCALER': 'SeedVR2',
        'USE_CANARY_STT': 'Canary-STT',
        'USE_AUDIOSR': 'AudioSR',
        'USE_LTX_VIDEO': 'LTX-2 Video',
        'USE_REALESRGAN_CPU': 'Real-ESRGAN',
        'USE_DOCLING': 'Docling',
        'USE_MEDIA_TOOLKIT': 'Media Toolkit',
    }
    for key, name in tool_keys.items():
        if all_values.get(key) == 'true':
            other_tools.append(name)
    if other_tools:
        print(f"  Other Tools:     ✓ {', '.join(other_tools)}")
    else:
        print(f"  Other Tools:     ✗ None enabled")
    
    # Show download size estimate
    dl_info = estimate_download_sizes(all_values)
    if dl_info['items']:
        print(f"\n  {Colors.YELLOW}Estimated downloads: ~{dl_info['total_gb']:.1f} GB{Colors.RESET}")
        for name, size_gb in dl_info['items']:
            print(f"    {name}: ~{size_gb:.1f} GB")
    
    print()
    if not prompt_yes_no("Save this configuration?", default=True):
        print_info("Update cancelled.")
        return
    
    # Save .env file
    save_env_file(all_values)
    
    # Verify Docker is still running before restarting services
    if not check_docker_running():
        print_error("Docker is no longer running. Configuration saved, but services cannot be restarted.")
        print_info("Start Docker and run this script again to apply changes.")
        return
    
    # Generate docker-compose.override.yml with GPU config + DNS overrides
    use_gpu = all_values.get('USE_GPU', 'false').lower() == 'true'
    extra_hosts = build_extra_hosts_from_env(all_values)
    generate_docker_compose_override(PROJECT_ROOT, use_gpu=use_gpu, extra_hosts=extra_hosts)
    
    # Check if services were running before
    services_were_running = is_services_running()
    
    # Start or restart services to pick up new config
    if services_were_running:
        print_info("Restarting services to apply new configuration...")
        # Must use 'up -d' (not 'restart') to pick up new environment variables
        run_docker_compose_command(["up", "-d"])
    else:
        print_info("Starting services with new configuration...")
        run_docker_compose_command(["up", "-d"])
    
    import time
    time.sleep(10)
    
    # Re-run tools initialization with new config
    run_init_tools_catalog()
    
    # Setup ACE-Step if enabled (install and start on host)
    acestep_process = setup_acestep_if_enabled(all_values)
    
    # Setup Qwen3-TTS if enabled (install and start on host)
    qwen3_tts_process = setup_qwen3_tts_if_enabled(all_values)
    
    # Setup Z-Image if enabled (install and start on host)
    zimage_process = setup_zimage_if_enabled(all_values)
    
    # Setup SeedVR2 Upscaler if enabled (install and start on host)
    seedvr2_process = setup_seedvr2_if_enabled(all_values)
    
    # Setup Canary-STT if enabled (install and start on host)
    canary_stt_process = setup_canary_stt_if_enabled(all_values)
    
    # Setup AudioSR if enabled (install and start on host)
    audiosr_process = setup_audiosr_if_enabled(all_values)
    
    # Setup Media Toolkit if enabled (install and start on host)
    media_toolkit_process = setup_media_toolkit_if_enabled(all_values)
    
    # Setup Real-ESRGAN CPU if enabled (install and start on host)
    realesrgan_cpu_process = setup_realesrgan_cpu_if_enabled(all_values)
    
    # Setup Docling if enabled (install and start on host)
    docling_process = setup_docling_if_enabled(all_values)
    
    # Setup LTX-2 Video if enabled (install and start on host)
    ltx_video_process = setup_ltx_video_if_enabled(all_values)
    
    print_section("Configuration Updated! ✓")
    print(f"""
  {Colors.GREEN}Services are now running with the new configuration.{Colors.RESET}

  {Colors.BOLD}Access the application:{Colors.RESET}
  - Frontend:  {Colors.CYAN}http://localhost:5173{Colors.RESET}
  - API Docs:  {Colors.CYAN}http://localhost:8000/docs{Colors.RESET}
""")
    
    # Wait for Ctrl+C to stop (services are now running either way)
    wait_for_ctrl_c()
    
    print()
    print_section("Stopping Application")
    print_info("Shutdown requested via Ctrl+C...")
    run_docker_compose_command(["stop"])
    
    print_section("Application Stopped! ✓")
    print(f"""
  {Colors.GREEN}All services have been stopped.{Colors.RESET}

  {Colors.BOLD}To start again:{Colors.RESET}
  - Run: {Colors.CYAN}python start.py{Colors.RESET}
""")


def run_factory_reset():
    """Run factory reset - completely wipe database and configuration."""
    clear_screen()
    print_header()
    
    print_section("⚠️  FACTORY RESET  ⚠️")
    
    print(f"""{Colors.RED}{Colors.BOLD}
  WARNING: This action CANNOT be undone!
  
  Factory reset will:
  • DELETE your .env configuration file
  • WIPE the entire database (all campaigns, proposals, tools, users)
  • REMOVE all Docker volumes
  • Reset the system to a fresh state as if just cloned from GitHub
  
  You will lose ALL data including:
  • All user accounts
  • All campaigns and proposals
  • All tool configurations
  • All conversation history
  • All analytics and learning data
{Colors.RESET}""")
    
    print()
    if not prompt_yes_no(f"{Colors.RED}Are you absolutely sure you want to continue?{Colors.RESET}", default=False):
        print_info("Factory reset cancelled.")
        return
    
    print()
    print(f"{Colors.YELLOW}To confirm factory reset, type 'money-agents' below:{Colors.RESET}")
    confirmation = prompt("Confirmation", required=True)
    
    if confirmation != "money-agents":
        print_error("Confirmation text did not match. Factory reset cancelled.")
        return
    
    print()
    print_section("Performing Factory Reset...")
    
    # Step 1: Stop all services
    print_info("Stopping all services...")
    run_docker_compose_command(["down"])
    
    # Step 2: Remove Docker volumes (this wipes the database)
    print_info("Removing Docker volumes (wiping database)...")
    result = subprocess.run(
        ["docker", "compose", "down", "-v", "--remove-orphans"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    if result.returncode != 0:
        print_warning(f"Volume removal may have had issues: {result.stderr[:200] if result.stderr else 'unknown'}")
    
    # Step 3: Delete .env file
    print_info("Removing .env configuration file...")
    try:
        if ENV_FILE.exists():
            ENV_FILE.unlink()
            print_info(".env file deleted.")
    except Exception as e:
        print_error(f"Failed to delete .env: {e}")
    
    # Step 4: Clean up any other generated files
    print_info("Cleaning up generated files...")
    
    # Remove celerybeat-schedule if it exists
    celerybeat_file = PROJECT_ROOT / "backend" / "celerybeat-schedule"
    if celerybeat_file.exists():
        try:
            celerybeat_file.unlink()
        except Exception:
            pass
    
    # Remove __pycache__ directories
    for pycache in PROJECT_ROOT.rglob("__pycache__"):
        try:
            shutil.rmtree(pycache)
        except Exception:
            pass
    
    print_section("Factory Reset Complete! 🔄")
    print(f"""
  {Colors.GREEN}Money Agents has been reset to factory state.{Colors.RESET}

  {Colors.BOLD}To set up again:{Colors.RESET}
  
    {Colors.CYAN}python start.py{Colors.RESET}

  This will walk you through the initial configuration again.
""")


def wait_for_backend_health(timeout: int = 60) -> bool:
    """
    Wait for the backend to be healthy by polling the health endpoint.
    
    Args:
        timeout: Maximum seconds to wait
        
    Returns:
        True if backend is healthy, False if timeout reached
    """
    import time
    import urllib.request
    import urllib.error
    
    start_time = time.time()
    spinner = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
    spinner_idx = 0
    
    while time.time() - start_time < timeout:
        try:
            req = urllib.request.Request("http://localhost:8000/health", method='GET')
            with urllib.request.urlopen(req, timeout=2) as response:
                if response.status == 200:
                    print(f"\r{Colors.GREEN}✓{Colors.RESET} Backend is healthy!                    ")
                    return True
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ConnectionRefusedError):
            pass
        
        # Show spinner
        elapsed = int(time.time() - start_time)
        print(f"\r  {spinner[spinner_idx]} Waiting for backend... ({elapsed}s)", end='', flush=True)
        spinner_idx = (spinner_idx + 1) % len(spinner)
        time.sleep(0.5)
    
    print(f"\r{Colors.YELLOW}⚠{Colors.RESET} Backend health check timed out          ")
    return False


def wait_for_ctrl_c():
    """Block until the user presses Ctrl+C.
    
    Returns:
        "ctrl_c" when interrupted.
    """
    import time
    
    spinner = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
    spinner_idx = 0
    
    print(f"\n  {Colors.DIM}Press Ctrl+C to stop all services{Colors.RESET}")
    
    try:
        while True:
            print(f"\r  {spinner[spinner_idx]} Running... (Ctrl+C to stop)", end='', flush=True)
            spinner_idx = (spinner_idx + 1) % len(spinner)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\r{Colors.YELLOW}⚡{Colors.RESET} Ctrl+C received - shutting down...          ")
        return "ctrl_c"


def run_add_comfy_api():
    """Guide the user to add a ComfyUI API from a workflow JSON file."""
    clear_screen()
    print_header()

    print_section("Add ComfyUI API")

    print(f"  {Colors.DIM}This will launch the ComfyUI API Generator Wizard, which creates")
    print(f"  a REST API from a ComfyUI workflow JSON export.{Colors.RESET}\n")

    add_comfy_script = PROJECT_ROOT / "add-comfy-api.py"
    if not add_comfy_script.exists():
        print_error("add-comfy-api.py not found in the project root.")
        return

    # Prompt for workflow JSON path with validation loop
    while True:
        try:
            raw = input(f"  {Colors.BOLD}Path to workflow JSON file{Colors.RESET} (or 'cancel'): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            print_info("Cancelled.")
            return

        if raw.lower() in ("cancel", "q", "quit", "exit", ""):
            print_info("Cancelled.")
            return

        workflow_path = Path(raw).expanduser().resolve()

        if not workflow_path.exists():
            print_error(f"File not found: {workflow_path}")
            print(f"  {Colors.DIM}Please check the path and try again, or type 'cancel' to go back.{Colors.RESET}\n")
            continue

        if not workflow_path.suffix.lower() == ".json":
            print_warning(f"File does not have a .json extension: {workflow_path.name}")
            if not prompt_yes_no("Continue anyway?", default=False):
                continue

        # File exists — hand off to add-comfy-api.py
        print()
        print_info(f"Workflow file: {workflow_path}")
        print_info("Launching ComfyUI API Generator Wizard...\n")

        result = subprocess.run(
            [sys.executable, str(add_comfy_script), str(workflow_path)],
            cwd=str(PROJECT_ROOT),
        )

        if result.returncode == 0:
            print()
            print_info("ComfyUI API generation complete.")
        else:
            print()
            print_warning(f"ComfyUI API wizard exited with code {result.returncode}.")
        return


def run_start_application():
    """Start the application services."""
    clear_screen()
    print_header()
    
    print_section("Starting Application")
    
    # Prerequisites were checked in main(), but verify Docker is still running
    if not check_docker_running():
        print_error("Docker is no longer running. Please start Docker first.")
        return
    
    # Auto-generate secure keys/passwords if still using known defaults
    current_env = load_current_env()
    ensure_secure_secret_key(current_env)
    ensure_secure_passwords(current_env)
    ensure_service_api_keys(current_env)
    
    print_info("Starting Money Agents services...")
    run_docker_compose_command(["up", "-d"])
    
    print()
    # Wait for backend to be healthy
    backend_healthy = wait_for_backend_health(timeout=60)
    
    # Also give frontend a moment to start
    if backend_healthy:
        import time
        print(f"  {Colors.DIM}Waiting for frontend to be ready...{Colors.RESET}")
        time.sleep(3)
    
    # Check if services started successfully
    if is_services_running():
        # Start ComfyUI APIs
        start_all_comfy_apis()
        
        # Update COMFYUI_TOOLS env var from discovered workflows
        update_env_comfyui_tools()
        
        # Start host-side services if enabled
        current_env = load_current_env()
        setup_acestep_if_enabled(current_env)
        setup_qwen3_tts_if_enabled(current_env)
        setup_zimage_if_enabled(current_env)
        setup_seedvr2_if_enabled(current_env)
        setup_canary_stt_if_enabled(current_env)
        setup_audiosr_if_enabled(current_env)
        setup_media_toolkit_if_enabled(current_env)
        setup_realesrgan_cpu_if_enabled(current_env)
        setup_docling_if_enabled(current_env)
        setup_ltx_video_if_enabled(current_env)
        
        # Start the host-side service manager (allows backend to
        # restart GPU services that were /shutdown for VRAM eviction)
        print_section("Starting GPU Service Manager")
        start_service_manager(current_env)
        
        # Show consolidated firewall note if any services need rules
        print_deferred_firewall_note()
        
        print_section("Application Started! ✓")
        print(f"""
  {Colors.GREEN}Money Agents is now running!{Colors.RESET}

  {Colors.BOLD}Open in your browser:{Colors.RESET}
  ┌────────────────────────────────────────────┐
  │  {Colors.CYAN}{Colors.BOLD}http://localhost:5173{Colors.RESET}                     │
  └────────────────────────────────────────────┘

  {Colors.BOLD}Other URLs:{Colors.RESET}
  - API Docs:  {Colors.CYAN}http://localhost:8000/docs{Colors.RESET}
""")
        
        # List ComfyUI APIs if any are running
        comfy_apis = discover_comfy_apis()
        running_apis = [a for a in comfy_apis if is_comfy_api_running(a['port'])]
        if running_apis:
            print(f"  {Colors.BOLD}ComfyUI APIs:{Colors.RESET}")
            for api in running_apis:
                print(f"  - {api['display_name']}:  {Colors.CYAN}http://127.0.0.1:{api['port']}/docs{Colors.RESET}")
            print()
        
        # Wait for Ctrl+C to stop
        wait_for_ctrl_c()
        
        print()
        print_section("Stopping Application")
        print_info("Shutdown requested via Ctrl+C...")
        
        # Stop ComfyUI APIs
        stop_all_comfy_apis()
        
        # Stop host-side GPU services (ACE-Step, Qwen3-TTS, Z-Image)
        stop_all_gpu_services(current_env)
        
        # Stop the service manager
        stop_service_manager()
        
        run_docker_compose_command(["stop"])
        
        print_section("Application Stopped! ✓")
        print(f"""
  {Colors.GREEN}All services have been stopped.{Colors.RESET}

  {Colors.BOLD}To start again:{Colors.RESET}
  - Run: {Colors.CYAN}python start.py{Colors.RESET}
""")
    else:
        print_error("Services may not have started correctly. Check logs with: bash dev.sh logs")


def run_stop_application():
    """Stop the application services."""
    clear_screen()
    print_header()
    
    print_section("Stopping Application")
    
    # Stop ComfyUI APIs first
    stop_all_comfy_apis()
    
    # Stop host-side GPU services (ACE-Step, Qwen3-TTS, Z-Image)
    current_env = load_current_env()
    stop_all_gpu_services(current_env)
    
    # Stop the service manager
    stop_service_manager()
    
    print_info("Stopping Money Agents services...")
    run_docker_compose_command(["stop"])
    
    print_section("Application Stopped! ✓")
    print(f"""
  {Colors.GREEN}Money Agents services have been stopped.{Colors.RESET}

  {Colors.BOLD}To start again:{Colors.RESET}
  - Run: {Colors.CYAN}python start.py{Colors.RESET} (option 1)
  - Or:  {Colors.DIM}bash dev.sh start{Colors.RESET}
""")


def main():
    """Main entry point."""
    global ENABLE_ALL_TOOLS
    
    # Parse --all flag: enable all compatible tools automatically
    if '--all' in sys.argv:
        ENABLE_ALL_TOOLS = True
        sys.argv.remove('--all')
    
    clear_screen()
    print_header()

    # Run prerequisite checks before doing anything
    # On first run, check ports too since we're about to start services
    first_run = not ENV_FILE.exists()
    if not check_prerequisites(
        require_docker_running=True,
        check_ports=first_run,
    ):
        sys.exit(1)

    # Check if .env exists (indicates previous setup)
    if ENV_FILE.exists():
        # If --all flag, skip the menu and run full setup directly
        if ENABLE_ALL_TOOLS:
            print_info("--all flag detected. Running full setup with all compatible tools enabled...")
            run_full_setup()
            return
        
        # Check if services are running
        services_running = is_services_running()
        
        print(f"{Colors.BOLD}Money Agents is already configured.{Colors.RESET}")
        if services_running:
            print(f"{Colors.GREEN}Status: Running ✓{Colors.RESET}\n")
        else:
            print(f"{Colors.YELLOW}Status: Stopped{Colors.RESET}\n")
        
        print("What would you like to do?\n")
        
        # Dynamic first option based on running state
        start_stop_option = "Stop application" if services_running else "Start application"
        
        choice = prompt_choice("Select an option:", [
            start_stop_option,
            "Reset admin password",
            "Update configuration",
            "Add ComfyUI API",
            "Run full setup again (overwrites existing config)",
            "Factory reset (wipe everything)",
            "Exit"
        ])
        
        if choice == 1:
            if services_running:
                run_stop_application()
            else:
                run_start_application()
        elif choice == 2:
            run_password_reset()
            # Return to main menu
            main()
            return
        elif choice == 3:
            run_config_update()
        elif choice == 4:
            run_add_comfy_api()
        elif choice == 5:
            if prompt_yes_no("This will overwrite your existing configuration. Continue?", default=False):
                run_full_setup()
            else:
                print_info("Cancelled.")
        elif choice == 6:
            run_factory_reset()
        else:
            print_info("Goodbye!")
    else:
        # First time setup
        run_full_setup()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{Colors.YELLOW}Setup cancelled by user.{Colors.RESET}\n")
        sys.exit(1)
