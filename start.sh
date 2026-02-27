#!/usr/bin/env bash
# =============================================================================
# Money Agents — Start Script (Linux / macOS)
# =============================================================================
#
# This is the main entry point for Money Agents on Linux and macOS.
# Run it every time — prerequisite checks take ~1 second when everything
# is already installed.  On a fresh system it will:
#
#   0. Install Homebrew  (macOS only, if missing)
#   1. Install Python 3.10+  (apt / brew)
#   2. Install Docker Engine or Docker Desktop  (apt / brew)
#   3. Install Docker Compose V2 plugin  (if needed)
#   4. Install Git  (apt / brew)
#   5. Add your user to the docker group  (Linux)
#   6. Start the Docker daemon  (if not running)
#   7. Install NVIDIA Container Toolkit  (Linux, optional)
#   8. Configure UFW firewall rules for host-side services
#   9. Install Ollama  (local LLM runtime)
#  10. Check disk space
#  11. Launch  python start.py
#
# Usage:
#   bash start.sh           # normal
#   bash start.sh --yes     # skip confirmations (auto-accept all installs)
#   bash start.sh --all     # enable all compatible tools automatically
#   bash start.sh --yes --all  # fully unattended setup
#
# =============================================================================
set -euo pipefail

# ─── Globals ────────────────────────────────────────────────────────────────
AUTO_YES=false
ENABLE_ALL=false
NEED_REBOOT=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ANSI colours
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

# ─── Helpers ────────────────────────────────────────────────────────────────
info()    { echo -e "${GREEN}✓${RESET} $*"; }
warn()    { echo -e "${YELLOW}⚠${RESET} $*"; }
err()     { echo -e "${RED}✗${RESET} $*"; }
header()  { echo -e "\n${CYAN}${BOLD}─── $* ───${RESET}\n"; }

confirm() {
    # Usage: confirm "Install Docker?" && do_install
    if $AUTO_YES; then return 0; fi
    local prompt="$1"
    while true; do
        read -rp "$(echo -e "${YELLOW}?${RESET} ${prompt} [Y/n] ")" yn
        case "${yn,,}" in
            ""|y|yes) return 0 ;;
            n|no)     return 1 ;;
            *)        echo "  Please answer y or n." ;;
        esac
    done
}

confirm_default_no() {
    # Like confirm() but defaults to No when the user presses Enter.
    if $AUTO_YES; then return 0; fi
    local prompt="$1"
    while true; do
        read -rp "$(echo -e "${YELLOW}?${RESET} ${prompt} [y/N] ")" yn
        case "${yn,,}" in
            y|yes)    return 0 ;;
            ""|n|no)  return 1 ;;
            *)        echo "  Please answer y or n." ;;
        esac
    done
}

command_exists() { command -v "$1" &>/dev/null; }

# Detect OS
detect_os() {
    case "$(uname -s)" in
        Linux*)  OS="linux" ;;
        Darwin*) OS="macos" ;;
        *)       OS="unknown" ;;
    esac

    ARCH="$(uname -m)"

    # Linux distro family
    DISTRO=""
    PKG_MGR=""
    if [[ "$OS" == "linux" ]]; then
        if [[ -f /etc/os-release ]]; then
            # shellcheck disable=SC1091
            . /etc/os-release
            DISTRO="${ID:-unknown}"
        fi
        if command_exists apt-get; then
            PKG_MGR="apt"
        elif command_exists dnf; then
            PKG_MGR="dnf"
        elif command_exists yum; then
            PKG_MGR="yum"
        elif command_exists pacman; then
            PKG_MGR="pacman"
        fi
    fi
}

# ─── Parse args ─────────────────────────────────────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --yes|-y) AUTO_YES=true ;;
        --all)   ENABLE_ALL=true ;;
        --help|-h)
            echo "Usage: bash start.sh [--yes] [--all]"
            echo "  --yes, -y   Skip confirmation prompts (auto-accept all installs)"
            echo "  --all       Enable all compatible tools automatically"
            exit 0
            ;;
    esac
done

# ─── Banner ─────────────────────────────────────────────────────────────────
echo -e "
${CYAN}${BOLD}╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║   ⚡  M O N E Y   A G E N T S  —  Setup                          ║
║                                                                  ║
║   Checking and installing prerequisites...                       ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝${RESET}
"

detect_os

if [[ "$OS" == "unknown" ]]; then
    err "Unsupported operating system: $(uname -s)"
    echo "  This script supports Linux and macOS."
    echo "  For Windows, run:  .\\start.ps1"
    exit 1
fi

info "Detected: ${OS} (${ARCH})"
[[ -n "$DISTRO" ]] && info "Distro:   ${DISTRO} (package manager: ${PKG_MGR:-none})"

# =============================================================================
# 0. Homebrew (macOS only — required for all package installs)
# =============================================================================
if [[ "$OS" == "macos" ]] && ! command_exists brew; then
    header "Homebrew (macOS Package Manager)"
    echo "  Homebrew is required to install dependencies on macOS."
    echo "  https://brew.sh"
    echo ""
    if confirm "Install Homebrew now?"; then
        info "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # Homebrew on Apple Silicon installs to /opt/homebrew; add to PATH for this session
        if [[ -f /opt/homebrew/bin/brew ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [[ -f /usr/local/bin/brew ]]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi
        if command_exists brew; then
            info "Homebrew installed successfully."
        else
            err "Homebrew installation failed. Please install manually:"
            echo "    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
            exit 1
        fi
    else
        err "Homebrew is required on macOS. Please install it first:"
        echo "    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        exit 1
    fi
fi

# =============================================================================
# 1. Python 3.10+
# =============================================================================
header "Python 3.10+"

install_python() {
    if [[ "$OS" == "macos" ]]; then
        brew install python@3.12
    elif [[ "$PKG_MGR" == "apt" ]]; then
        sudo apt-get update -qq
        sudo apt-get install -y python3 python3-venv python3-pip
    elif [[ "$PKG_MGR" == "dnf" ]]; then
        sudo dnf install -y python3 python3-pip
    elif [[ "$PKG_MGR" == "yum" ]]; then
        sudo yum install -y python3 python3-pip
    elif [[ "$PKG_MGR" == "pacman" ]]; then
        sudo pacman -Sy --noconfirm python python-pip
    else
        err "Could not determine how to install Python on this system."
        echo "  Please install Python 3.10+ manually: https://www.python.org/downloads/"
        exit 1
    fi
}

# Find a Python 3.10+ interpreter
find_python() {
    for cmd in python3 python; do
        if command_exists "$cmd"; then
            local ver
            ver="$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)" || continue
            local major minor
            major="${ver%%.*}"
            minor="${ver##*.}"
            if (( major == 3 && minor >= 10 )); then
                PYTHON_CMD="$cmd"
                PYTHON_VER="$ver"
                return 0
            fi
        fi
    done
    return 1
}

if find_python; then
    info "Python ${PYTHON_VER} found ($(command -v "$PYTHON_CMD"))"
else
    warn "Python 3.10+ not found."
    if confirm "Install Python?"; then
        install_python
        # Re-check
        if find_python; then
            info "Python ${PYTHON_VER} installed successfully."
        else
            err "Python installation did not provide Python 3.10+."
            echo "  Please install manually: https://www.python.org/downloads/"
            exit 1
        fi
    else
        err "Python 3.10+ is required.  Please install it and re-run this script."
        echo "  Download: https://www.python.org/downloads/"
        exit 1
    fi
fi

# =============================================================================
# 2. Git
# =============================================================================
header "Git"

install_git() {
    if [[ "$OS" == "macos" ]]; then
        if command_exists brew; then
            brew install git
        else
            # xcode-select installs git on macOS
            xcode-select --install 2>/dev/null || true
            echo "  If the Xcode tools dialog appeared, complete the installation and re-run this script."
        fi
    elif [[ "$PKG_MGR" == "apt" ]]; then
        sudo apt-get update -qq
        sudo apt-get install -y git
    elif [[ "$PKG_MGR" == "dnf" ]]; then
        sudo dnf install -y git
    elif [[ "$PKG_MGR" == "yum" ]]; then
        sudo yum install -y git
    elif [[ "$PKG_MGR" == "pacman" ]]; then
        sudo pacman -Sy --noconfirm git
    else
        err "Could not determine how to install Git on this system."
        echo "  Please install Git manually: https://git-scm.com/downloads"
        exit 1
    fi
}

if command_exists git; then
    info "Git found ($(git --version))"
else
    warn "Git not found."
    if confirm "Install Git? (needed for ACE-Step music generation)"; then
        install_git
        if command_exists git; then
            info "Git installed ($(git --version))"
        else
            warn "Git installation may not have completed — continuing without it."
            echo "  ACE-Step (local music generation) will not be available until Git is installed."
        fi
    else
        warn "Skipping Git — ACE-Step (local music generation) will not be available."
    fi
fi

# =============================================================================
# 3. Docker
# =============================================================================
header "Docker"

install_docker_linux() {
    info "Installing Docker via get.docker.com convenience script..."
    curl -fsSL https://get.docker.com | sudo sh
}

install_docker_macos() {
    if command_exists brew; then
        info "Installing Docker Desktop via Homebrew..."
        brew install --cask docker
        echo ""
        warn "Docker Desktop has been installed."
        echo "  You may need to open Docker Desktop from Applications to complete setup."
        echo "  Opening Docker Desktop now..."
        open -a Docker 2>/dev/null || true
        # Wait for Docker daemon to come up
        echo -e "  ${DIM}Waiting for Docker to start (up to 60 seconds)...${RESET}"
        for i in $(seq 1 30); do
            if docker info &>/dev/null; then
                break
            fi
            sleep 2
        done
    else
        err "Homebrew is not installed.  Install Homebrew first, or install Docker Desktop manually:"
        echo "    Homebrew:       /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        echo "    Docker Desktop: https://docs.docker.com/desktop/setup/install/mac-install/"
        exit 1
    fi
}

if command_exists docker; then
    info "Docker found ($(docker --version 2>/dev/null || echo 'version unknown'))"
else
    warn "Docker not found."
    if [[ "$OS" == "macos" ]]; then
        echo "  Docker Desktop is required on macOS."
        echo "  Download: https://docs.docker.com/desktop/setup/install/mac-install/"
    else
        echo "  Docker Engine is required on Linux."
        echo "  Docs: https://docs.docker.com/engine/install/"
    fi
    if confirm "Install Docker?"; then
        if [[ "$OS" == "macos" ]]; then
            install_docker_macos
        else
            install_docker_linux
        fi
        if command_exists docker; then
            info "Docker installed ($(docker --version 2>/dev/null))"
        else
            err "Docker installation failed.  Please install manually and re-run."
            [[ "$OS" == "macos" ]] && echo "  https://docs.docker.com/desktop/setup/install/mac-install/"
            [[ "$OS" == "linux" ]] && echo "  https://docs.docker.com/engine/install/"
            exit 1
        fi
    else
        err "Docker is required.  Please install it and re-run this script."
        exit 1
    fi
fi

# =============================================================================
# 4. Docker Compose V2
# =============================================================================
header "Docker Compose"

if docker compose version &>/dev/null; then
    info "Docker Compose found ($(docker compose version 2>/dev/null))"
else
    warn "Docker Compose V2 plugin not found."
    if [[ "$OS" == "linux" && "$PKG_MGR" == "apt" ]]; then
        if confirm "Install docker-compose-plugin?"; then
            sudo apt-get update -qq
            sudo apt-get install -y docker-compose-plugin
            if docker compose version &>/dev/null; then
                info "Docker Compose installed ($(docker compose version 2>/dev/null))"
            else
                err "Docker Compose installation failed."
                echo "  See: https://docs.docker.com/compose/install/linux/"
                exit 1
            fi
        else
            err "Docker Compose V2 is required.  Please install it and re-run."
            echo "  See: https://docs.docker.com/compose/install/"
            exit 1
        fi
    elif [[ "$OS" == "macos" ]]; then
        err "Docker Compose should be included with Docker Desktop."
        echo "  Please update Docker Desktop to the latest version."
        echo "  Download: https://docs.docker.com/desktop/setup/install/mac-install/"
        exit 1
    else
        err "Docker Compose V2 ('docker compose') is required."
        echo "  See: https://docs.docker.com/compose/install/"
        exit 1
    fi
fi

# =============================================================================
# 5. Docker Group (Linux only)
# =============================================================================
if [[ "$OS" == "linux" ]]; then
    header "Docker Permissions (Linux)"

    if docker info &>/dev/null; then
        info "Docker is accessible by current user."
    else
        # Check if it's a permissions issue
        if docker info 2>&1 | grep -qi "permission denied"; then
            warn "Your user is not in the 'docker' group."
            echo "  You need to be a member of the 'docker' group to run Docker without sudo."
            if confirm "Add user '${USER}' to the docker group?"; then
                sudo usermod -aG docker "$USER"
                info "User '${USER}' added to the docker group."

                # Activate the new group in this session without requiring logout.
                # 'sg docker' runs a command with the docker group active.
                # We re-exec this script so all subsequent steps see the new group.
                info "Activating docker group (no logout needed)..."
                # Pass through any flags (e.g. --yes)
                sg docker -c "bash \"${BASH_SOURCE[0]}\" $*"
                exit $?
            else
                warn "Skipping — Docker commands may require 'sudo'."
                echo "  To fix later:  sudo usermod -aG docker \$USER  (then log out/in)"
            fi
        else
            warn "Docker daemon may not be running (checked in next step)."
        fi
    fi
fi

# =============================================================================
# 6. Start Docker daemon
# =============================================================================
header "Docker Daemon"

if docker info &>/dev/null; then
    info "Docker daemon is running."
else
    warn "Docker daemon is not running."
    if [[ "$OS" == "macos" ]]; then
        if confirm "Open Docker Desktop?"; then
            open -a Docker 2>/dev/null || true
            echo -e "  ${DIM}Waiting for Docker to start (up to 60 seconds)...${RESET}"
            started=false
            for i in $(seq 1 30); do
                if docker info &>/dev/null; then
                    started=true
                    break
                fi
                sleep 2
            done
            if $started; then
                info "Docker daemon is now running."
            else
                warn "Docker Desktop may still be starting up."
                echo "  Wait for the Docker icon to appear in the menu bar, then re-run this script."
                exit 1
            fi
        else
            err "Docker must be running.  Open Docker Desktop and re-run this script."
            exit 1
        fi
    else
        # Linux: try systemctl
        if confirm "Start the Docker daemon (sudo systemctl start docker)?"; then
            sudo systemctl start docker
            sleep 2
            if docker info &>/dev/null; then
                info "Docker daemon started."
                # Also enable on boot
                if confirm "Enable Docker to start on boot?"; then
                    sudo systemctl enable docker
                    info "Docker enabled on boot."
                fi
            else
                err "Could not start Docker daemon."
                echo "  Check logs:  sudo journalctl -u docker --no-pager -n 20"
                exit 1
            fi
        else
            err "Docker daemon must be running.  Start it and re-run this script."
            echo "  Run:  sudo systemctl start docker"
            exit 1
        fi
    fi
fi

# =============================================================================
# 7. NVIDIA Container Toolkit (Linux, optional)
# =============================================================================
if [[ "$OS" == "linux" ]]; then
    header "NVIDIA GPU Support (Optional)"

    if command_exists nvidia-smi; then
        gpu_name=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
        info "NVIDIA GPU detected: ${gpu_name}"

        # Check if nvidia-container-toolkit is installed
        if dpkg -l nvidia-container-toolkit &>/dev/null 2>&1 || rpm -q nvidia-container-toolkit &>/dev/null 2>&1; then
            info "NVIDIA Container Toolkit is installed."
        else
            warn "NVIDIA Container Toolkit is NOT installed."
            echo "  This is needed for Docker containers to access your GPU."
            if confirm "Install NVIDIA Container Toolkit?"; then
                # Add NVIDIA repo
                curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg 2>/dev/null
                curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
                    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
                    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list > /dev/null
                sudo apt-get update -qq
                sudo apt-get install -y nvidia-container-toolkit
                sudo nvidia-ctk runtime configure --runtime=docker
                sudo systemctl restart docker
                info "NVIDIA Container Toolkit installed and Docker configured."
            else
                warn "Skipping — GPU acceleration in Docker will not be available."
            fi
        fi
    else
        echo -e "  ${DIM}No NVIDIA GPU detected — skipping (GPU is optional).${RESET}"
    fi
fi

# =============================================================================
# 8. Firewall rules for host-side services (Linux, UFW)
# =============================================================================
if [[ "$OS" == "linux" ]]; then
    header "Firewall Rules (Docker ↔ Host Services)"

    # Docker containers need to reach host-side services (GPU tools, Ollama, etc.)
    # via host.docker.internal.  If UFW is active, it blocks this by default.
    # We add rules for ALL tool ports upfront so they're ready regardless of
    # which tools the user enables later.

    if command_exists ufw; then
        # Check if UFW is active (needs sudo)
        ufw_active=false
        ufw_status=$(sudo -n ufw status 2>/dev/null) || ufw_status=""
        if echo "$ufw_status" | grep -qi "active"; then
            ufw_active=true
        elif sudo -n true 2>/dev/null; then
            # sudo works passwordless but ufw not active
            ufw_active=false
        else
            # Can't check without password — assume active if ufw is installed
            # (Ubuntu/Mint enable it by default)
            ufw_active=true
        fi

        if $ufw_active; then
            # Load port configuration from .env (fall back to defaults from .env.example)
            if [[ -f "${SCRIPT_DIR}/.env" ]]; then
                # Source only the *_PORT and OLLAMA_BASE_URL variables
                while IFS='=' read -r key value; do
                    case "$key" in
                        *_PORT|*_PORT\ *) export "${key%%[[:space:]]*}=${value%%[[:space:]#]*}" ;;
                        OLLAMA_BASE_URL)  export "OLLAMA_BASE_URL=${value%%[[:space:]#]*}" ;;
                    esac
                done < <(grep -E '^([A-Z_]+_PORT|OLLAMA_BASE_URL)=' "${SCRIPT_DIR}/.env" 2>/dev/null)
            fi

            # Extract Ollama port from URL (e.g. http://host.docker.internal:11434 -> 11434)
            ollama_port=11434
            if [[ -n "${OLLAMA_BASE_URL:-}" ]]; then
                # Strip scheme and path, grab port after last colon
                _ollama_hostport="${OLLAMA_BASE_URL#*://}"
                _ollama_hostport="${_ollama_hostport%%/*}"
                if [[ "$_ollama_hostport" == *:* ]]; then
                    ollama_port="${_ollama_hostport##*:}"
                fi
            fi

            # All host-side service ports that Docker containers may need to reach
            declare -A SERVICE_PORTS=(
                [${ACESTEP_API_PORT:-8001}]="ACE-Step"
                [${QWEN3_TTS_API_PORT:-8002}]="Qwen3-TTS"
                [${ZIMAGE_API_PORT:-8003}]="Z-Image"
                [${SEEDVR2_API_PORT:-8004}]="SeedVR2"
                [${CANARY_STT_API_PORT:-8005}]="Canary-STT"
                [${LTX_VIDEO_API_PORT:-8006}]="LTX-2 Video"
                [${AUDIOSR_API_PORT:-8007}]="AudioSR"
                [${MEDIA_TOOLKIT_API_PORT:-8008}]="Media Toolkit"
                [${REALESRGAN_CPU_API_PORT:-8009}]="Real-ESRGAN CPU"
                [${DOCLING_API_PORT:-8010}]="Docling"
                [${SERVICE_MANAGER_PORT:-9100}]="Service Manager"
                [${ollama_port}]="Ollama"
            )

            # Check which rules are missing
            missing_ports=()
            existing_rules=$(sudo -n ufw status 2>/dev/null || echo "")

            if [[ -n "$existing_rules" ]]; then
                for port in "${!SERVICE_PORTS[@]}"; do
                    # Check if a rule for this port from Docker networks already exists
                    if ! echo "$existing_rules" | grep -q "$port.*172.16.0.0/12"; then
                        missing_ports+=("$port")
                    fi
                done
            else
                # Couldn't read rules without password — assume all missing
                for port in "${!SERVICE_PORTS[@]}"; do
                    missing_ports+=("$port")
                done
            fi

            if (( ${#missing_ports[@]} == 0 )); then
                info "All firewall rules for Docker ↔ host services are in place."
            else
                # Sort ports for display
                IFS=$'\n' sorted_ports=($(sort -n <<<"${missing_ports[*]}")); unset IFS

                echo "  Docker containers need access to host services on these ports:"
                for port in "${sorted_ports[@]}"; do
                    echo -e "    ${DIM}${port}  ${SERVICE_PORTS[$port]}${RESET}"
                done
                echo ""

                # If rules were added in a previous run, default to N (skip)
                ufw_marker="${SCRIPT_DIR}/.ufw_rules_added"
                ufw_prompt_verb="Add"
                ufw_confirm=confirm
                if [[ -f "$ufw_marker" ]]; then
                    ufw_prompt_verb="Re-create"
                    ufw_confirm=confirm_default_no
                fi

                if $ufw_confirm "${ufw_prompt_verb} UFW firewall rules for Docker container access? (requires sudo)"; then
                    # Single sudo prompt, then add all rules
                    sudo_ok=false
                    if sudo true; then
                        sudo_ok=true
                    fi

                    if $sudo_ok; then
                        failed=0
                        for port in "${sorted_ports[@]}"; do
                            name="${SERVICE_PORTS[$port]}"
                            if sudo ufw allow from 172.16.0.0/12 to any port "$port" \
                                comment "Docker networks - ${name}" &>/dev/null; then
                                info "Port ${port} (${name}) ✓"
                            else
                                warn "Port ${port} (${name}) — failed to add rule"
                                (( failed++ )) || true
                            fi
                        done

                        if (( failed == 0 )); then
                            info "All firewall rules added successfully."
                            # Write marker so subsequent runs default to skip
                            date -Iseconds > "$ufw_marker" 2>/dev/null || true
                        else
                            warn "${failed} rule(s) failed. You may need to add them manually."
                        fi
                    else
                        warn "Could not get sudo access. Add rules manually:"
                        for port in "${sorted_ports[@]}"; do
                            name="${SERVICE_PORTS[$port]}"
                            echo -e "  ${DIM}sudo ufw allow from 172.16.0.0/12 to any port ${port} comment 'Docker networks - ${name}'${RESET}"
                        done
                    fi
                else
                    warn "Skipping firewall rules."
                    echo "  Docker containers may not be able to reach host services (GPU tools, Ollama)."
                    echo "  You can add rules later by re-running this script."
                fi
            fi
        else
            info "UFW is not active — no firewall rules needed."
        fi
    else
        echo -e "  ${DIM}UFW not installed — no firewall configuration needed.${RESET}"
    fi
fi

# =============================================================================
# 9. Install Ollama (if not present)
# =============================================================================
header "Ollama (local LLM runtime)"

if command_exists ollama; then
    info "Ollama is already installed: $(ollama --version 2>/dev/null || echo 'unknown version')"
else
    echo "  Ollama lets you run LLM models locally (recommended)."
    if [[ "$OS" == "macos" ]]; then
        echo "  Install via Homebrew: brew install ollama"
    else
        echo "  Install script: https://ollama.com/install.sh"
    fi
    echo ""
    if confirm "Install Ollama now?"; then
        info "Downloading and installing Ollama..."
        if [[ "$OS" == "macos" ]] && command_exists brew; then
            brew install ollama
        else
            curl -fsSL https://ollama.com/install.sh | sh
        fi
        if command_exists ollama; then
            info "Ollama installed successfully: $(ollama --version 2>/dev/null || echo 'unknown version')"
        else
            warn "Ollama install finished but 'ollama' command not found."
            echo "  You can install it manually later."
        fi
    else
        warn "Skipping Ollama — you can install it later if needed."
    fi
fi

# =============================================================================
# 10. Disk space check
# =============================================================================
header "Disk Space"

free_kb=$(df -Pk "$SCRIPT_DIR" 2>/dev/null | awk 'NR==2 {print $4}')
if [[ -n "$free_kb" ]]; then
    free_gb=$(( free_kb / 1048576 ))
    if (( free_gb >= 10 )); then
        info "${free_gb} GB free — plenty of space."
    elif (( free_gb >= 5 )); then
        warn "${free_gb} GB free — this may be tight. Docker images + data need ~5 GB minimum."
    else
        warn "Only ${free_gb} GB free — you may run out of space."
        echo "  Docker images require ~3 GB, plus data storage."
        echo "  Consider freeing up disk space before continuing."
    fi
else
    echo -e "  ${DIM}Could not determine free disk space.${RESET}"
fi

# =============================================================================
# 11. Launch start.py
# =============================================================================
header "Launching Money Agents Setup Wizard"

info "All prerequisites are installed!"
echo ""

# Build start.py arguments
START_PY_ARGS=()
if $ENABLE_ALL; then
    START_PY_ARGS+=("--all")
fi

echo -e "  ${DIM}Starting:  ${PYTHON_CMD} start.py ${START_PY_ARGS[*]:-}${RESET}"
echo ""

cd "$SCRIPT_DIR"
exec "$PYTHON_CMD" start.py "${START_PY_ARGS[@]:-}"
