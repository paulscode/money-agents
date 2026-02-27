# Install Resource Agent as a Windows Service
# Run this script as Administrator in PowerShell

param(
    [switch]$Uninstall,
    [string]$ServiceName = "ResourceAgent"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AgentDir = Split-Path -Parent $ScriptDir

Write-Host "=== Resource Agent Windows Installer ===" -ForegroundColor Cyan
Write-Host "Agent directory: $AgentDir"
Write-Host ""

# Check for Administrator privileges
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: This script must be run as Administrator!" -ForegroundColor Red
    Write-Host "Right-click PowerShell and select 'Run as Administrator'"
    exit 1
}

if ($Uninstall) {
    Write-Host "Uninstalling service..." -ForegroundColor Yellow
    
    # Stop service if running
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($service) {
        if ($service.Status -eq 'Running') {
            Stop-Service -Name $ServiceName
            Write-Host "Service stopped."
        }
        
        # Remove using sc.exe
        sc.exe delete $ServiceName
        Write-Host "Service removed."
    }
    else {
        Write-Host "Service not found."
    }
    
    exit 0
}

# Check for Python
$pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Path
if (-not $pythonPath) {
    Write-Host "ERROR: Python not found in PATH!" -ForegroundColor Red
    Write-Host "Please install Python 3.10+ and add it to PATH"
    exit 1
}
Write-Host "Python found: $pythonPath"

# Create virtual environment
$venvPath = Join-Path $AgentDir "venv"
if (-not (Test-Path $venvPath)) {
    Write-Host "Creating virtual environment..."
    python -m venv $venvPath
}

# Activate and install dependencies
Write-Host "Installing dependencies..."
$pipPath = Join-Path $venvPath "Scripts\pip.exe"
& $pipPath install --upgrade pip
& $pipPath install -r (Join-Path $AgentDir "requirements.txt")

# Create work directory
$workDir = Join-Path $AgentDir "work"
if (-not (Test-Path $workDir)) {
    New-Item -ItemType Directory -Path $workDir | Out-Null
}

# Create config from example if not exists
$configPath = Join-Path $AgentDir "config.yaml"
if (-not (Test-Path $configPath)) {
    Write-Host "Creating config.yaml from example..."
    Copy-Item (Join-Path $AgentDir "config.example.yaml") $configPath
    Write-Host ""
    Write-Host "WARNING: Edit config.yaml with your broker URL and API key!" -ForegroundColor Yellow
    Write-Host "  notepad $configPath"
}

# Check for NSSM (Non-Sucking Service Manager)
$nssmPath = (Get-Command nssm -ErrorAction SilentlyContinue).Path
if (-not $nssmPath) {
    Write-Host ""
    Write-Host "NSSM not found. Installing via winget..." -ForegroundColor Yellow
    
    try {
        winget install nssm --accept-source-agreements --accept-package-agreements
        # Refresh PATH
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        $nssmPath = (Get-Command nssm -ErrorAction SilentlyContinue).Path
    }
    catch {
        Write-Host "Could not install NSSM automatically." -ForegroundColor Red
        Write-Host "Please install NSSM manually from: https://nssm.cc/download"
        Write-Host "Or via: choco install nssm"
        Write-Host ""
        Write-Host "After installing NSSM, run this script again."
        exit 1
    }
}

if (-not $nssmPath) {
    Write-Host "NSSM still not found in PATH. Please ensure it's installed and in PATH." -ForegroundColor Red
    exit 1
}

Write-Host "NSSM found: $nssmPath"

# Remove existing service if present
$existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existingService) {
    Write-Host "Removing existing service..."
    if ($existingService.Status -eq 'Running') {
        Stop-Service -Name $ServiceName
    }
    nssm remove $ServiceName confirm
}

# Install service
Write-Host "Installing Windows service..."
$pythonExe = Join-Path $venvPath "Scripts\python.exe"
$agentScript = Join-Path $AgentDir "agent.py"

nssm install $ServiceName $pythonExe $agentScript
nssm set $ServiceName AppDirectory $AgentDir
nssm set $ServiceName DisplayName "Money Agents Resource Agent"
nssm set $ServiceName Description "Executes jobs for Money Agents system"
nssm set $ServiceName Start SERVICE_AUTO_START
nssm set $ServiceName AppStdout (Join-Path $AgentDir "logs\stdout.log")
nssm set $ServiceName AppStderr (Join-Path $AgentDir "logs\stderr.log")
nssm set $ServiceName AppRotateFiles 1
nssm set $ServiceName AppRotateBytes 10485760

# Create logs directory
$logsDir = Join-Path $AgentDir "logs"
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir | Out-Null
}

Write-Host ""
Write-Host "=== Installation Complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Service commands (PowerShell as Administrator):"
Write-Host "  Start:   Start-Service $ServiceName"
Write-Host "  Stop:    Stop-Service $ServiceName"
Write-Host "  Status:  Get-Service $ServiceName"
Write-Host "  Logs:    Get-Content $logsDir\stdout.log -Tail 50"
Write-Host ""
Write-Host "Or use Services GUI: services.msc"
Write-Host ""
Write-Host "IMPORTANT: Before starting, edit config.yaml with your broker URL and API key!" -ForegroundColor Yellow
Write-Host "  notepad $configPath"
