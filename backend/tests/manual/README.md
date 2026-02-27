# Manual Testing: Mock Tools for Agent Integration

This directory contains mock tools that simulate real-world external services.
These run on the **HOST machine** (not in Docker) to test container→host communication.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  HOST MACHINE (your workstation)                             │
│                                                              │
│  Terminal 1:                   Terminal 2:                   │
│  ┌──────────────────┐         ┌──────────────────────┐      │
│  │ mock_gpu_api.py  │         │ mock_cli_api.py      │      │
│  │ Port: 9999       │         │ Port: 9998           │      │
│  │ (GPU Service)    │         │ (wraps CLI tool)     │      │
│  └────────▲─────────┘         └──────────▲───────────┘      │
│           │                              │                   │
│           └──── host.docker.internal ────┘                   │
│                         │                                    │
│  ┌──────────────────────┴─────────────────────────┐         │
│  │            DOCKER CONTAINERS                    │         │
│  │                                                 │         │
│  │   Backend & Celery use ToolExecutor to call:   │         │
│  │     http://host.docker.internal:9999  (GPU)    │         │
│  │     http://host.docker.internal:9998  (CLI)    │         │
│  └─────────────────────────────────────────────────┘         │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Start Docker Services
```bash
cd /path/to/money-agents
docker compose up -d
```

### 2. Start Mock Services on Host

**Terminal 1 - GPU API:**
```bash
cd /path/to/money-agents
python backend/tests/manual/mock_gpu_api.py
```
Runs on http://localhost:9999

**Terminal 2 - CLI API:**
```bash
cd /path/to/money-agents
python backend/tests/manual/mock_cli_api.py
```
Runs on http://localhost:9998

### 3. Verify from Container
```bash
# Exec into backend container and test connectivity
docker exec -it money-agents-backend curl http://host.docker.internal:9999/health
docker exec -it money-agents-backend curl http://host.docker.internal:9998/health
```

### 4. Run Test Script
```bash
./backend/tests/manual/test_tool_implementation.sh
```

## Mock Services

### mock_gpu_api.py - Simulates GPU Service

Mimics a GPU-intensive API like Ollama, Stable Diffusion, etc.

**Endpoints:**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/gpu/status` | GET | GPU memory, temp, utilization |
| `/gpu/models` | GET | List available models |
| `/gpu/process` | POST | Submit GPU job (simulates 2-8 sec processing) |
| `/gpu/jobs/{id}` | GET | Check job status |

**Example:**
```bash
# Health check
curl http://localhost:9999/health

# Submit job
curl -X POST http://localhost:9999/gpu/process \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Generate an image of a sunset", "model": "stable-diffusion"}'
```

### mock_cli_api.py - HTTP Wrapper for CLI Tool

Wraps `mock_cli_tool.sh` as HTTP API. Real-world use: wrapping ffmpeg, imagemagick, yt-dlp.

**Endpoints:**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/info` | GET | System/tool info |
| `/process` | POST | Process text input |
| `/analyze` | POST | Analyze text |
| `/convert` | POST | Convert data format |

**Example:**
```bash
# Health check
curl http://localhost:9998/health

# Analyze text
curl -X POST http://localhost:9998/analyze \
  -H "Content-Type: application/json" \
  -d '{"input": "Hello world, this is a test!"}'
```

### mock_cli_tool.sh - Underlying CLI Tool

Can be used directly via command line (not HTTP):

```bash
./mock_cli_tool.sh --operation info
./mock_cli_tool.sh --operation analyze --input "Sample text"
./mock_cli_tool.sh --operation process --input "text to process" --format json
```

## Testing Tool Implementation Workflow

The `test_tool_implementation.sh` script tests the full cycle:

1. **Create Tools** - Creates "mock-gpu-imgen" and "mock-cli-analyzer" in `implementing` status
2. **Create Resources** - Creates a GPU resource with max_concurrent=1
3. **Link Resources** - Links GPU resource to the GPU tool
4. **Move to Implemented** - Updates status to `implemented`
5. **Test Execution** - Attempts to execute tools via the API

## Real-World Analogies

| Mock Service | Real-World Equivalent |
|--------------|----------------------|
| mock_gpu_api.py | Ollama, ComfyUI, SD-WebUI |
| mock_cli_api.py | ffmpeg wrapper, yt-dlp API |
| GPU resource queue | Prevents multiple GPU jobs competing for VRAM |

## Troubleshooting

### Container can't reach host
```bash
# Check extra_hosts is configured
docker exec money-agents-backend cat /etc/hosts | grep host.docker.internal

# Should show something like:
# 172.17.0.1    host.docker.internal
```

### Mock service not responding
```bash
# Check it's running on host
curl http://localhost:9999/health

# Check port isn't blocked
netstat -tlnp | grep 9999
```

### Tool execution fails
Check the ToolExecution records:
```sql
SELECT * FROM tool_executions ORDER BY created_at DESC LIMIT 5;
```
