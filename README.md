# Money Agents

An AI-powered platform for autonomous money-making opportunity discovery and campaign execution. Multiple specialized AI agents collaborate to find opportunities, write proposals, execute campaigns, and manage tools — with minimal human intervention.

## Quick Start

**Linux / macOS:**
```bash
git clone https://github.com/paulscode/money-agents.git
cd money-agents
bash start.sh
```

**Windows (PowerShell as Administrator):**
```powershell
git clone https://github.com/paulscode/money-agents.git
cd money-agents
.\start.ps1
```

The start script will:
- Check for and install prerequisites (Python 3.10+, Docker, Git)
- Start the Docker daemon if needed
- Launch the interactive setup wizard
- Walk you through configuring API keys
- Create your admin account and start all services
- Auto-detect system resources (CPU, RAM, GPU)

Once setup completes, open **http://localhost:5173** in your browser.

> Run `python start.py` anytime to change configuration, reset your password, or manage services.
>
> Use `bash start.sh --yes` or `.\start.ps1 -Yes` to skip confirmation prompts on subsequent runs.

---

## What It Does

Money Agents uses a team of autonomous AI agents that work together:

| Agent | Role |
|-------|------|
| **Opportunity Scout** | Searches the web for money-making opportunities, evaluates them, and ranks results by potential (requires a [Serper](https://serper.dev) API key, or a self-hosted [Serper Clone](https://github.com/paulscode/searxng-serper-bridge)) |
| **Proposal Writer** | Takes approved opportunities and writes detailed campaign proposals with budgets and timelines |
| **Campaign Manager** | Executes approved proposals as multi-stream campaigns with parallel task execution |
| **Tool Scout** | Identifies and registers new tools that could help the agents accomplish their goals |
| **Spend Advisor** | Reviews Bitcoin spending requests with a skeptical eye — approves, rejects, or questions the agent's logic |

### How the agents interact

```
Opportunity Scout  ──discovers──►  Opportunities
                                       │
                               (user approves)
                                       │
                                       ▼
Proposal Writer  ────refines────►  Proposals
                                       │
                               (user approves)
                                       │
                                       ▼
Campaign Manager  ───executes───►  Campaigns
        │                              │
        ├── uses tools ◄───────────────┘
        ├── requests budget (if Bitcoin enabled)
        │         │
        │    Spend Advisor reviews
        │         │
        └── runs in Dev Sandbox (isolated containers)
```

You control the pipeline: review opportunities, approve proposals, monitor campaigns. The agents handle the research, writing, and execution.

---

## Platform Support

| Platform | Status | GPU Support |
|----------|--------|-------------|
| Linux (x86_64) | Full support | NVIDIA GPUs via Container Toolkit |
| Windows 10/11 | Full support | NVIDIA GPUs via Docker Desktop (WSL 2) |
| macOS (Intel) | Full support | No GPU passthrough — host-side tools |
| macOS (Apple Silicon) | Full support | GPU via host-side tools (Ollama, etc.) |

### Prerequisites

- **Python 3.10+** — for running the setup wizard
- **Docker & Docker Compose V2** — Docker Desktop on Windows/macOS, or Docker Engine on Linux
- **8GB+ RAM** recommended

The start script (`start.sh` / `start.ps1`) checks and installs prerequisites automatically.

### GPU Support (Optional)

If you have an NVIDIA GPU and want to run local AI models:

**Linux** — Install the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html):
```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

**Windows** — Install the latest [NVIDIA GPU drivers](https://www.nvidia.com/Download/index.aspx). Docker Desktop with WSL 2 handles GPU passthrough automatically.

**macOS** — Docker Desktop does not support GPU passthrough. GPU-accelerated tools (Ollama, etc.) run natively on the host and are accessed by containers via `host.docker.internal`.

The setup wizard auto-detects your GPU and configures everything accordingly.

---

## LLM Providers

Money Agents needs at least one LLM provider to function. The system tries providers in priority order, falling back to the next if one is unavailable:

| Priority | Provider | Notes |
|----------|----------|-------|
| 1 | **Z.ai** (GLM-4.7) | Cheapest — has free flash models |
| 2 | **Anthropic** (Claude) | High quality fallback |
| 3 | **OpenAI** (GPT) | Enterprise-grade fallback |
| 4 | **Ollama** (local) | Free, runs on your hardware — tried last due to concurrency limits |

Configure API keys during setup, or edit `.env` and restart. You can use any combination — even Ollama alone for a fully free, offline setup.

---

## Local GPU Tools

If you have an NVIDIA GPU, the setup wizard offers optional free, locally-running AI tools:

| Tool | Port | VRAM | Description |
|------|------|------|-------------|
| **Ollama** | 11434 | Varies | Local LLM inference (Mistral, GLM, etc.) |
| **ACE-Step** | 8001 | ~4-5GB | AI music generation with lyrics (50+ languages) |
| **Qwen3-TTS** | 8002 | ~4-8GB | Text-to-speech with voice cloning |
| **Z-Image** | 8003 | ~16-18GB | Text-to-image generation (6B DiT) |
| **SeedVR2** | 8004 | ~8-12GB | Image & video upscaling (up to 4x) |
| **Canary-STT** | 8005 | ~4-6GB | Speech-to-text transcription |
| **LTX-2** | 8006 | ~20-24GB | Text-to-video generation with audio |
| **AudioSR** | 8007 | ~4-8GB | Audio super-resolution (upscale to 48kHz) |
| **Media Toolkit** | 8008 | None (CPU) | FFmpeg-based media composition (split, combine, mix) |

GPU tools share VRAM cooperatively — only one model is loaded at a time, with automatic eviction and reloading. The system handles this transparently.

You can also connect **ComfyUI workflows** as additional tools via the setup wizard.

---

## Bitcoin / Lightning Integration (Optional)

Connect an LND Lightning node to enable budget-controlled Bitcoin payments:

- **Read-only monitoring:** Balance, channels, transactions, payments, invoices
- **Budget-controlled spending:** Per-campaign Bitcoin budgets with approval workflows
- **Spend Advisor agent:** AI reviews spending requests before approval
- **Tor support:** Connect to .onion LND nodes (Start9, Umbrel, etc.)
- **Mempool Explorer:** Clickable transaction links, fee estimation

Configure during setup by pasting an `lndconnect://` URI or entering connection details manually.

---

## Services & Ports

| Service | Port | Description |
|---------|------|-------------|
| Frontend (React) | 5173 | Web UI |
| Backend (FastAPI) | 8000 | REST API + WebSocket |
| API Docs | 8000/docs | Interactive API documentation |
| PostgreSQL | 5433 | Database |
| Redis | 6379 | Cache & message queue |
| Flower | 5555 | Celery task monitoring (debug) |

---

## Managing Services

```bash
# Check service status
bash dev.sh status

# View logs (streams output; Ctrl-C to stop)
bash dev.sh logs backend
bash dev.sh logs frontend
bash dev.sh logs all

# Restart all services
bash dev.sh restart

# Stop all services
bash dev.sh stop

# Run commands inside containers
bash dev.sh exec backend bash
```

Or use the setup wizard: `python start.py`

---

## Manual Setup (Advanced)

If you prefer to configure things manually instead of using the setup wizard:

1. **Copy the environment template:**
   ```bash
   cp .env.example .env
   ```

2. **Edit `.env`** with your API keys and preferences. At minimum, configure one LLM provider.

3. **Start services:**
   ```bash
   docker compose up -d
   ```

4. **Create your admin account** using the setup wizard — it detects the existing `.env` and jumps straight to account creation:
   ```bash
   python start.py
   ```
   You can also re-run `python start.py` at any time to reset an admin password.

5. **Access the app** at http://localhost:5173

### Adding External Storage (Optional)

To mount additional drives for Storage Resources:

1. Copy the override template:
   ```bash
   cp docker-compose.override.yml.example docker-compose.override.yml
   ```

2. Add your mount paths:
   ```yaml
   services:
     backend:
       volumes:
         - /mnt/data:/mnt/data:rw
   ```

3. Restart: `docker compose up -d backend`

---

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on submitting pull requests, reporting bugs, and suggesting features.

This project follows the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). For security vulnerabilities, see [SECURITY.md](SECURITY.md).

---

## Security Notes

- API keys are stored in `.env` (gitignored, never committed)
- JWT-based authentication with bcrypt password hashing
- Input validation and sanitization on all endpoints
- Dev Sandbox runs agent code in isolated Docker containers with resource limits
- Bitcoin spending requires explicit approval workflows with configurable thresholds

---

## License

[MIT](LICENSE)
