# Resource Agent

A lightweight, cross-platform agent for distributing workloads across multiple machines.

## Overview

The Resource Agent runs on worker machines (Linux or Windows) and:
- Reports system capabilities (GPU, CPU, RAM, storage)
- Receives jobs from the central broker
- Executes tools and returns results
- Sends heartbeats to maintain connection status
- **Optionally**: Manages campaigns (LLM reasoning and tool orchestration)

## Supported Platforms

| OS | GPU Support | Tested On |
|----|-------------|-----------|
| Linux | NVIDIA (nvidia-smi) | Mint 22.2, Ubuntu |
| Windows | NVIDIA (nvidia-smi) | Windows 11 |

## Installation

### Prerequisites

- Python 3.10+ 
- pip

### Quick Setup

```bash
# Clone or copy resource-agent folder to the target machine
cd resource-agent

# Create virtual environment
python -m venv venv

# Activate (Linux)
source venv/bin/activate

# Activate (Windows PowerShell)
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Copy and edit config
cp config.example.yaml config.yaml
# Edit config.yaml with broker URL and API key
```

### Configuration

Edit `config.yaml`:

```yaml
broker:
  url: "ws://<YOUR_SERVER_IP>:8000/api/v1/broker/agent"  # Main backend
  api_key: "your-agent-api-key"  # Get from admin panel

agent:
  name: "win11-4090"  # Unique name for this machine
  tags: ["gpu", "windows", "4090"]  # Searchable tags
  
capabilities:
  # Auto-detected, but can override
  gpu_enabled: true
  max_concurrent_jobs: 1  # GPU jobs typically run one at a time
```

## Campaign Worker Mode

In addition to executing tools, the agent can also **manage campaigns** - handling LLM reasoning and tool orchestration locally. This is useful for:

- **Horizontal Scaling**: Distribute campaign processing across multiple machines
- **Edge Computing**: Run LLM processing closer to data/tools
- **Redundancy**: Multiple workers can pick up campaigns if one fails

### Features

- **Multi-Provider Support**: Supports GLM (Zhipu), Claude (Anthropic), and OpenAI
- **Automatic Failover**: Tries providers in priority order, skips unavailable ones
- **Model Tier Support**: Fast, reasoning, and quality tiers (matches main app)
- **Token Tracking**: Reports token usage back to main app for monitoring
- **Max Tokens Standard**: Uses 6000 max tokens (main app standard)

### Enabling Campaign Worker

Add to `config.yaml`:

```yaml
campaign_worker:
  enabled: true
  max_campaigns: 3  # Max concurrent campaigns to manage
  
  # Provider priority (tries in order, skips those without keys)
  llm_provider_priority: "glm,claude,openai"
  
  # Set API keys for providers you have (use env vars for security!)
  # anthropic_api_key: "sk-ant-..."  # Or set ANTHROPIC_API_KEY env var
  # openai_api_key: "sk-..."         # Or set OPENAI_API_KEY env var  
  # zhipu_api_key: "..."             # Or set Z_AI_API_KEY env var
  
  # Model settings (usually inherited from campaign)
  llm_default_model_tier: "reasoning"  # fast, reasoning, quality
  llm_max_tokens: 6000  # Standard max tokens
```

Or use environment variables:

```bash
export CAMPAIGN_WORKER_ENABLED=true
export LLM_PROVIDER_PRIORITY="glm,claude,openai"
export ANTHROPIC_API_KEY="sk-ant-..."  # For Claude
export OPENAI_API_KEY="sk-..."         # For OpenAI
export Z_AI_API_KEY="..."              # For GLM/Zhipu
```

### Model Tiers

The system uses model tiers to select appropriate models:

| Tier | GLM | Claude | OpenAI |
|------|-----|--------|--------|
| fast | glm-4-flash | claude-3-haiku | gpt-4o-mini |
| reasoning | glm-4-flash | claude-sonnet-4 | o1-mini |
| quality | glm-4-plus | claude-opus-4 | gpt-4o |

### How It Works

1. Agent registers as a campaign worker on connection
2. Backend assigns campaigns with model_tier setting
3. Agent selects provider based on priority and availability
4. Agent executes LLM reasoning using the appropriate model tier
5. Tool calls are dispatched back to appropriate resource hosts
6. Results with token counts are synced to backend database
7. User input is routed from frontend → backend → worker

### Running

```bash
# Run in foreground (for testing)
python agent.py

# Or use the service installer (see below)
```

## Service Installation

### Linux (systemd)

```bash
sudo ./install/install_linux.sh
# Service: sudo systemctl start resource-agent
# Logs: journalctl -u resource-agent -f
```

### Windows (NSSM)

```powershell
# Run as Administrator
.\install\install_windows.ps1
# Service: Start-Service ResourceAgent
# Logs: Check %USERPROFILE%\resource-agent\logs
```

## Capabilities Reported

The agent automatically detects and reports:

- **CPU**: Cores, model name, architecture
- **Memory**: Total RAM, available RAM
- **GPU** (if NVIDIA): Model, VRAM, driver version, CUDA version
- **Storage**: Mounted volumes with free space
- **Network**: Hostname, IP address
- **Platform**: OS type, version

## Job Execution

When the broker assigns a job:

1. Agent validates it can run the job (has required capabilities)
2. Downloads any required inputs
3. Executes the tool (subprocess or HTTP call)
4. Uploads results back to broker
5. Reports completion status

## Environment Variables

Can also configure via environment:

| Variable | Description | Default |
|----------|-------------|---------|
| `BROKER_URL` | WebSocket URL of broker | From config.yaml |
| `BROKER_API_KEY` | Authentication key | From config.yaml |
| `AGENT_NAME` | Unique agent identifier | Hostname |
| `LOG_LEVEL` | Logging verbosity | INFO |
| `CAMPAIGN_WORKER_ENABLED` | Enable campaign worker mode | false |
| `CAMPAIGN_MAX_CAMPAIGNS` | Max concurrent campaigns | 3 |
| `LLM_PROVIDER_PRIORITY` | Provider order (comma-separated) | glm,claude,openai |
| `ANTHROPIC_API_KEY` | Claude (Anthropic) API key | - |
| `OPENAI_API_KEY` | OpenAI API key | - |
| `Z_AI_API_KEY` | GLM (Zhipu) API key | - |
| `LLM_MODEL_TIER` | Default model tier | reasoning |

## Troubleshooting

### Agent won't connect

1. Check broker URL is reachable: `curl http://BROKER_IP:8000/api/v1/health`
2. Verify API key is correct
3. Check firewall allows outbound WebSocket (port 8000)

### GPU not detected

1. Verify nvidia-smi works: `nvidia-smi`
2. Check NVIDIA drivers are installed
3. On Windows, ensure nvidia-smi is in PATH

### Jobs failing

1. Check agent logs for errors
2. Verify tool dependencies are installed
3. Check disk space for temp files
