# =============================================================================
# Money Agents -- Start Script (Windows PowerShell)
# =============================================================================
#
# This is the main entry point for Money Agents on Windows.
# Run it every time -- prerequisite checks take ~1 second when everything
# is already installed.  On a fresh system it will:
#
#   0. Check winget availability
#   1. Install Python 3.10+       (winget)
#   2. Install Git                 (winget)
#   3. Install Docker Desktop      (winget)
#   4. Ensure Docker Compose V2 is present
#   5. Ensure Docker daemon is running
#   6. NVIDIA GPU (informational)
#   7. Firewall rules for host-side services
#   8. Install Ollama              (winget)
#   9. Check disk space
#  10. Launch  python start.py
#
# Usage (in PowerShell):
#   .\start.ps1              # normal
#   .\start.ps1 -Yes         # skip confirmation prompts
#   .\start.ps1 -All         # enable all compatible tools automatically
#   .\start.ps1 -Yes -All    # fully unattended setup
#
# If PowerShell blocks the script, run:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#
# =============================================================================

param(
    [switch]$Yes,
    [switch]$All,
    [switch]$Help
)

# --- Strict mode ------------------------------------------------------------
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# --- Globals ----------------------------------------------------------------
$NeedReboot = $false
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path

# --- Helpers ----------------------------------------------------------------
function Write-Info    { param([string]$Msg) Write-Host "  [OK] $Msg" -ForegroundColor Green }
function Write-Warn    { param([string]$Msg) Write-Host "  [!!] $Msg" -ForegroundColor Yellow }
function Write-Err     { param([string]$Msg) Write-Host "  [X] $Msg" -ForegroundColor Red }
function Write-Header  { param([string]$Msg) Write-Host "`n  --- $Msg ---`n" -ForegroundColor Cyan }

function Confirm-Action {
    param([string]$Prompt)
    if ($Yes) { return $true }
    $answer = Read-Host "  ? $Prompt [Y/n]"
    return ($answer -eq "" -or $answer -match "^[Yy]")
}

function Confirm-DefaultNo {
    # Like Confirm-Action but defaults to No when the user presses Enter.
    param([string]$Prompt)
    if ($Yes) { return $true }
    $answer = Read-Host "  ? $Prompt [y/N]"
    return ($answer -match "^[Yy]")
}

function Test-Command {
    param([string]$Name)
    $null = Get-Command $Name -ErrorAction SilentlyContinue
    return $?
}

function Test-Admin {
    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Refresh-Path {
    # Reload PATH from Machine + User registry so newly installed tools are visible
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath    = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path    = "$machinePath;$userPath"
}

# --- Help -------------------------------------------------------------------
if ($Help) {
    Write-Host "Usage: .\start.ps1 [-Yes] [-All] [-Help]"
    Write-Host "  -Yes    Skip confirmation prompts (auto-accept all installs)"
    Write-Host "  -All    Enable all compatible tools automatically"
    Write-Host "  -Help   Show this help message"
    exit 0
}

# --- Banner -----------------------------------------------------------------
Write-Host ""
Write-Host "  +==================================================================+" -ForegroundColor Cyan
Write-Host "  |                                                                  |" -ForegroundColor Cyan
    Write-Host "  |   **  M O N E Y   A G E N T S  --  Start (Windows)              |" -ForegroundColor Cyan
Write-Host "  |                                                                  |" -ForegroundColor Cyan
Write-Host "  |   Checking and installing prerequisites...                       |" -ForegroundColor Cyan
Write-Host "  |                                                                  |" -ForegroundColor Cyan
Write-Host "  +==================================================================+" -ForegroundColor Cyan
Write-Host ""

$osVer  = [System.Environment]::OSVersion.Version
$arch   = if ([Environment]::Is64BitOperatingSystem) { "x64" } else { "x86" }
Write-Info "Detected: Windows $($osVer.Major).$($osVer.Minor) ($arch)"

if (-not (Test-Admin)) {
    Write-Host ""
    Write-Warn "Running without Administrator privileges."
    Write-Host "    Some installations (Docker, Git) may require elevation."
    Write-Host "    If an install fails, re-run this script as Administrator."
    Write-Host ""
}

# Check for winget
$HasWinget = Test-Command "winget"
if (-not $HasWinget) {
    Write-Warn "winget (Windows Package Manager) not found."
    Write-Host "    winget comes pre-installed on Windows 10 1709+ and Windows 11."
    Write-Host "    If missing, install 'App Installer' from the Microsoft Store:"
    Write-Host "    https://aka.ms/getwinget"
    Write-Host ""
    Write-Host "    Without winget, you will need to install prerequisites manually."
    Write-Host ""
}

# =============================================================================
# 1. Python 3.10+
# =============================================================================
Write-Header "Python 3.10+"

$PythonCmd = $null
$PythonVer = $null

function Find-Python {
    foreach ($cmd in @("python", "python3", "py")) {
        if (Test-Command $cmd) {
            try {
                $ver = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
                if ($ver -match "^(\d+)\.(\d+)$") {
                    $major = [int]$Matches[1]
                    $minor = [int]$Matches[2]
                    if ($major -eq 3 -and $minor -ge 10) {
                        $script:PythonCmd = $cmd
                        $script:PythonVer = $ver
                        return $true
                    }
                }
            } catch { }
        }
    }
    return $false
}

if (Find-Python) {
    Write-Info "Python $PythonVer found ($(Get-Command $PythonCmd | Select-Object -ExpandProperty Source))"
} else {
    Write-Warn "Python 3.10+ not found."
    if ($HasWinget -and (Confirm-Action "Install Python 3.12 via winget?")) {
        Write-Host "    Installing Python 3.12..." -ForegroundColor DarkGray
        winget install --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements --silent
        Refresh-Path
        if (Find-Python) {
            Write-Info "Python $PythonVer installed successfully."
        } else {
            Write-Err "Python installation completed but Python 3.10+ not found in PATH."
            Write-Host "    You may need to close and reopen this terminal, then re-run setup.ps1"
            Write-Host "    Or download from: https://www.python.org/downloads/"
            $NeedReboot = $true
        }
    } else {
        Write-Err "Python 3.10+ is required."
        Write-Host "    Download from: https://www.python.org/downloads/"
        Write-Host "    During installation, check 'Add Python to PATH'."
        exit 1
    }
}

# =============================================================================
# 2. Git
# =============================================================================
Write-Header "Git"

if (Test-Command "git") {
    $gitVer = (git --version 2>$null) -join ""
    Write-Info "Git found ($gitVer)"
} else {
    Write-Warn "Git not found."
    if ($HasWinget -and (Confirm-Action "Install Git via winget? (needed for ACE-Step music generation)")) {
        Write-Host "    Installing Git..." -ForegroundColor DarkGray
        winget install --id Git.Git --accept-source-agreements --accept-package-agreements --silent
        Refresh-Path
        if (Test-Command "git") {
            Write-Info "Git installed ($(git --version 2>$null))"
        } else {
            Write-Warn "Git installation completed but 'git' not found in PATH."
            Write-Host "    You may need to close and reopen this terminal."
            Write-Host "    ACE-Step (local music generation) will not be available until Git is in PATH."
        }
    } else {
        Write-Warn "Skipping Git -- ACE-Step (local music generation) will not be available."
        Write-Host "    To install later: winget install Git.Git"
        Write-Host "    Or download from: https://git-scm.com/download/win"
    }
}

# =============================================================================
# 3. Docker Desktop
# =============================================================================
Write-Header "Docker Desktop"

if (Test-Command "docker") {
    $dockerVer = (docker --version 2>$null) -join ""
    Write-Info "Docker found ($dockerVer)"
} else {
    Write-Warn "Docker not found."
    Write-Host "    Docker Desktop (with WSL 2 backend) is required on Windows."
    if ($HasWinget -and (Confirm-Action "Install Docker Desktop via winget?")) {
        Write-Host "    Installing Docker Desktop..." -ForegroundColor DarkGray
        Write-Host "    (This may take several minutes)" -ForegroundColor DarkGray
        winget install --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements --silent
        Refresh-Path
        if (Test-Command "docker") {
            Write-Info "Docker Desktop installed."
            Write-Host ""
            Write-Warn "A reboot may be required to complete Docker Desktop setup."
            Write-Host "    Docker Desktop requires WSL 2 -- if not already enabled,"
            Write-Host "    Windows may prompt you to reboot."
            $NeedReboot = $true
        } else {
            Write-Warn "Docker Desktop installed but 'docker' not yet in PATH."
            Write-Host "    A reboot is likely needed to complete setup."
            Write-Host "    After rebooting, open Docker Desktop and re-run this script."
            $NeedReboot = $true
        }
    } else {
        Write-Err "Docker is required."
        Write-Host "    Download Docker Desktop: https://docs.docker.com/desktop/setup/install/windows-install/"
        Write-Host "    Or: winget install Docker.DockerDesktop"
        exit 1
    }
}

# =============================================================================
# 4. Docker Compose V2
# =============================================================================
Write-Header "Docker Compose"

if (Test-Command "docker") {
    try {
        $composeVer = docker compose version 2>$null
        if ($LASTEXITCODE -eq 0 -and $composeVer) {
            Write-Info "Docker Compose found ($composeVer)"
        } else {
            throw "not found"
        }
    } catch {
        Write-Warn "Docker Compose V2 not found."
        Write-Host "    Docker Compose should be included with Docker Desktop."
        Write-Host "    Please update Docker Desktop to the latest version."
        Write-Host "    https://docs.docker.com/desktop/setup/install/windows-install/"
    }
} else {
    Write-Host "    Skipped -- Docker not installed." -ForegroundColor DarkGray
}

# =============================================================================
# 5. Docker Daemon Running
# =============================================================================
Write-Header "Docker Daemon"

if ($NeedReboot) {
    Write-Warn "Skipping daemon check -- a reboot may be needed first."
} elseif (Test-Command "docker") {
    try {
        $null = docker info 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Info "Docker daemon is running."
        } else {
            throw "not running"
        }
    } catch {
        Write-Warn "Docker daemon is not running."
        if (Confirm-Action "Start Docker Desktop?") {
            # Try to find and launch Docker Desktop
            $dockerDesktopPath = $null
            $candidates = @(
                "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
                "${env:ProgramFiles(x86)}\Docker\Docker\Docker Desktop.exe"
            )
            foreach ($path in $candidates) {
                if (Test-Path $path) {
                    $dockerDesktopPath = $path
                    break
                }
            }

            if ($dockerDesktopPath) {
                Start-Process $dockerDesktopPath
                Write-Host "    Waiting for Docker to start (up to 60 seconds)..." -ForegroundColor DarkGray
                $started = $false
                for ($i = 0; $i -lt 30; $i++) {
                    Start-Sleep -Seconds 2
                    try {
                        $null = docker info 2>$null
                        if ($LASTEXITCODE -eq 0) {
                            $started = $true
                            break
                        }
                    } catch { }
                }
                if ($started) {
                    Write-Info "Docker daemon is now running."
                } else {
                    Write-Warn "Docker Desktop may still be starting."
                    Write-Host "    Wait for the Docker icon in the system tray, then re-run this script."
                    exit 1
                }
            } else {
                Write-Warn "Could not find Docker Desktop executable."
                Write-Host "    Open Docker Desktop manually and re-run this script."
                exit 1
            }
        } else {
            Write-Err "Docker must be running.  Open Docker Desktop and re-run this script."
            exit 1
        }
    }
} else {
    Write-Host "    Skipped -- Docker not installed." -ForegroundColor DarkGray
}

# =============================================================================
# 6. NVIDIA GPU (informational)
# =============================================================================
Write-Header "NVIDIA GPU Support (Optional)"

if (Test-Command "nvidia-smi") {
    try {
        $gpuName = (nvidia-smi --query-gpu=name --format=csv,noheader 2>$null) | Select-Object -First 1
        Write-Info "NVIDIA GPU detected: $gpuName"
        Write-Host "    Docker Desktop with WSL 2 handles GPU passthrough automatically." -ForegroundColor DarkGray
        Write-Host "    Make sure you have the latest NVIDIA GPU drivers installed." -ForegroundColor DarkGray
        Write-Host "    https://www.nvidia.com/Download/index.aspx" -ForegroundColor DarkGray
    } catch {
        Write-Host "    nvidia-smi found but could not query GPU." -ForegroundColor DarkGray
    }
} else {
    Write-Host "    No NVIDIA GPU detected -- GPU features will be disabled (optional)." -ForegroundColor DarkGray
}

# =============================================================================
# 7. Firewall rules for host-side services
# =============================================================================
Write-Header "Windows Firewall (Docker <-> Host Services)"

# Load port configuration from .env (fall back to defaults from .env.example)
$envFile = Join-Path $PSScriptRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^([A-Z_]+_PORT)=([0-9]+)') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
        }
        elseif ($_ -match '^OLLAMA_BASE_URL=(.+)') {
            [Environment]::SetEnvironmentVariable("OLLAMA_BASE_URL", $Matches[1].Trim(), "Process")
        }
    }
}

# Extract Ollama port from OLLAMA_BASE_URL (e.g. http://host.docker.internal:11434 -> 11434)
$ollamaPort = 11434
if ($env:OLLAMA_BASE_URL) {
    try {
        $ollamaPort = [int]([uri]$env:OLLAMA_BASE_URL).Port
        if ($ollamaPort -le 0) { $ollamaPort = 11434 }
    } catch { $ollamaPort = 11434 }
}

# All ports that host-side tools listen on (read from env, with defaults)
$ServicePorts = [ordered]@{
    ([int]$(if ($env:ACESTEP_API_PORT)        { $env:ACESTEP_API_PORT }        else { 8001 })) = "ACE-Step"
    ([int]$(if ($env:QWEN3_TTS_API_PORT)      { $env:QWEN3_TTS_API_PORT }      else { 8002 })) = "Qwen3-TTS"
    ([int]$(if ($env:ZIMAGE_API_PORT)          { $env:ZIMAGE_API_PORT }          else { 8003 })) = "Z-Image"
    ([int]$(if ($env:SEEDVR2_API_PORT)         { $env:SEEDVR2_API_PORT }         else { 8004 })) = "SeedVR2"
    ([int]$(if ($env:CANARY_STT_API_PORT)      { $env:CANARY_STT_API_PORT }      else { 8005 })) = "Canary-STT"
    ([int]$(if ($env:LTX_VIDEO_API_PORT)       { $env:LTX_VIDEO_API_PORT }       else { 8006 })) = "LTX-2 Video"
    ([int]$(if ($env:AUDIOSR_API_PORT)          { $env:AUDIOSR_API_PORT }          else { 8007 })) = "AudioSR"
    ([int]$(if ($env:MEDIA_TOOLKIT_API_PORT)    { $env:MEDIA_TOOLKIT_API_PORT }    else { 8008 })) = "Media Toolkit"
    ([int]$(if ($env:REALESRGAN_CPU_API_PORT)   { $env:REALESRGAN_CPU_API_PORT }   else { 8009 })) = "Real-ESRGAN CPU"
    ([int]$(if ($env:DOCLING_API_PORT)          { $env:DOCLING_API_PORT }          else { 8010 })) = "Docling"
    ([int]$(if ($env:SERVICE_MANAGER_PORT)      { $env:SERVICE_MANAGER_PORT }      else { 9100 })) = "Service Manager"
    $ollamaPort = "Ollama"
}

# Check which rules are missing
$MissingRules = @()
foreach ($port in $ServicePorts.Keys) {
    $name = $ServicePorts[$port]
    $ruleName = "Docker $name (port $port)"
    try {
        $existing = netsh advfirewall firewall show rule name="$ruleName" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not ($existing -match $ruleName)) {
            $MissingRules += @{ Port = $port; Name = $name; RuleName = $ruleName }
        }
    } catch {
        $MissingRules += @{ Port = $port; Name = $name; RuleName = $ruleName }
    }
}

if ($MissingRules.Count -eq 0) {
    Write-Info "All $($ServicePorts.Count) firewall rules are already in place."
} else {
    Write-Host "    $($MissingRules.Count) firewall rule(s) needed for Docker to reach host services:" -ForegroundColor DarkGray
    foreach ($rule in $MissingRules) {
        Write-Host "      - Port $($rule.Port): $($rule.Name)" -ForegroundColor DarkGray
    }
    Write-Host ""

    $isAdmin = Test-Admin
    if (-not $isAdmin) {
        Write-Warn "Administrator privileges required to add firewall rules."
        Write-Host "    Re-run as Administrator, or add these rules manually:" -ForegroundColor DarkGray
        foreach ($rule in $MissingRules) {
            Write-Host "    netsh advfirewall firewall add rule name=`"$($rule.RuleName)`" dir=in action=allow protocol=tcp localport=$($rule.Port) remoteip=172.16.0.0/12" -ForegroundColor DarkGray
        }
    } else {
        # Use marker file to decide prompt wording / default
        $fwMarker = Join-Path $PSScriptRoot ".fw_rules_added"
        if (Test-Path $fwMarker) {
            $promptVerb = "Re-create"
            $confirmFn  = { param($p) Confirm-DefaultNo $p }
        } else {
            $promptVerb = "Add"
            $confirmFn  = { param($p) Confirm-Action $p }
        }

        if (& $confirmFn "$promptVerb $($MissingRules.Count) firewall rules for Docker?") {
            $added   = 0
            $failed  = 0
            foreach ($rule in $MissingRules) {
                try {
                    $null = netsh advfirewall firewall add rule `
                        name="$($rule.RuleName)" `
                        dir=in action=allow protocol=tcp `
                        localport=$($rule.Port) `
                        remoteip=172.16.0.0/12 2>$null
                    if ($LASTEXITCODE -eq 0) {
                        $added++
                    } else {
                        $failed++
                    }
                } catch {
                    $failed++
                }
            }
            if ($added -gt 0) { Write-Info "$added firewall rule(s) added successfully." }
            if ($failed -gt 0) { Write-Warn "$failed rule(s) failed -- check manually." }
            # Write marker so subsequent runs default to skip
            if ($failed -eq 0) {
                Get-Date -Format o | Out-File -FilePath $fwMarker -Encoding utf8 -ErrorAction SilentlyContinue
            }
        } else {
            Write-Warn "Skipping firewall rules -- Docker may not reach host-side tools."
        }
    }
}

# =============================================================================
# 8. Install Ollama (local LLM runtime)
# =============================================================================
Write-Header "Ollama (Local LLM Runtime)"

if (Test-Command "ollama") {
    try {
        $ollamaVer = (ollama --version 2>$null) -join ""
        Write-Info "Ollama is already installed ($ollamaVer)"
    } catch {
        Write-Info "Ollama is already installed."
    }
} else {
    Write-Host "    Ollama lets you run LLM models locally (recommended)." -ForegroundColor DarkGray
    Write-Host "    https://ollama.com" -ForegroundColor DarkGray
    Write-Host ""
    if ($HasWinget -and (Confirm-Action "Install Ollama via winget?")) {
        Write-Host "    Installing Ollama..." -ForegroundColor DarkGray
        winget install --id Ollama.Ollama --accept-source-agreements --accept-package-agreements --silent
        Refresh-Path
        if (Test-Command "ollama") {
            Write-Info "Ollama installed successfully."
        } else {
            Write-Warn "Ollama installed but 'ollama' not found in PATH yet."
            Write-Host "    You may need to close and reopen this terminal."
        }
    } else {
        Write-Warn "Skipping Ollama -- you can install it later from https://ollama.com"
    }
}

# =============================================================================
# 9. Disk Space
# =============================================================================
Write-Header "Disk Space"

try {
    $drive     = (Resolve-Path $ScriptDir).Drive.Name + ":"
    $diskInfo  = Get-PSDrive $drive.TrimEnd(":") -ErrorAction SilentlyContinue
    if ($diskInfo) {
        $freeGB = [math]::Round($diskInfo.Free / 1GB, 1)
        if ($freeGB -ge 10) {
            Write-Info "$freeGB GB free on $drive -- plenty of space."
        } elseif ($freeGB -ge 5) {
            Write-Warn "$freeGB GB free on $drive -- this may be tight."
        } else {
            Write-Warn "Only $freeGB GB free on $drive -- you may run out of space."
            Write-Host "    Docker images require ~3 GB, plus data storage."
        }
    }
} catch {
    Write-Host "    Could not determine free disk space." -ForegroundColor DarkGray
}

# =============================================================================
# 10. Early exit if reboot needed
# =============================================================================
if ($NeedReboot) {
    Write-Host ""
    Write-Host "  +==================================================================+" -ForegroundColor Yellow
    Write-Host "  |                                                                  |" -ForegroundColor Yellow
    Write-Host "  |   ACTION REQUIRED: Close this terminal (and possibly reboot)     |" -ForegroundColor Yellow
    Write-Host "  |                                                                  |" -ForegroundColor Yellow
    Write-Host "  |   Some installations require a fresh terminal or reboot.         |" -ForegroundColor Yellow
    Write-Host "  |                                                                  |" -ForegroundColor Yellow
    Write-Host "  |   After restarting:                                              |" -ForegroundColor Yellow
    Write-Host "  |     1. Open Docker Desktop (if it doesn't auto-start)            |" -ForegroundColor Yellow
    Write-Host "  |     2. Open PowerShell in this folder                            |" -ForegroundColor Yellow
    Write-Host "  |     3. Run:  .\start.ps1                                        |" -ForegroundColor Yellow
    Write-Host "  |                                                                  |" -ForegroundColor Yellow
    Write-Host "  +==================================================================+" -ForegroundColor Yellow
    Write-Host ""
    exit 0
}

# =============================================================================
# 11. Final check -- make sure we have Python
# =============================================================================
if (-not $PythonCmd) {
    if (-not (Find-Python)) {
        Write-Err "Python 3.10+ is still not available.  Please install it and re-run."
        exit 1
    }
}

# =============================================================================
# 12. Launch start.py
# =============================================================================
Write-Header "Launching Money Agents Setup Wizard"

Write-Info "All prerequisites are installed!"
Write-Host ""

# Build start.py arguments
$StartPyArgs = @("start.py")
if ($All) {
    $StartPyArgs += "--all"
}

$argsDisplay = $StartPyArgs -join " "
Write-Host "    Starting:  $PythonCmd $argsDisplay" -ForegroundColor DarkGray
Write-Host ""

Set-Location $ScriptDir
& $PythonCmd @StartPyArgs
