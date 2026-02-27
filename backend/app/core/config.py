from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional
from pathlib import Path


# Get the path to the .env file (in project root)
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"  # Ignore extra fields in .env file
    )
    
    # Application
    app_name: str = "Money Agents"
    app_version: str = "1.0.0"
    debug: bool = False
    
    # API
    api_v1_prefix: str = "/api/v1"
    
    # Security
    secret_key: SecretStr  # SGA3-L2: SecretStr prevents accidental exposure via repr/model_dump
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 15  # SGA3-L3: Match .env.example default
    
    # Environment ("production" or "development")
    environment: str = "production"
    
    # API documentation visibility (disable in production)
    enable_docs: bool = False
    
    # Database
    database_url: str
    database_echo: bool = False
    # GAP-21: PostgreSQL SSL mode (disable, allow, prefer, require, verify-ca, verify-full)
    # Set to "require" or stricter for production deployments
    db_ssl_mode: Optional[str] = None
    
    # Redis
    redis_url: str = "redis://localhost:6379/0"
    
    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    
    # AI Services
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    z_ai_api_key: Optional[str] = None
    z_ai_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    elevenlabs_api_key: Optional[str] = None
    serper_api_key: Optional[str] = None
    
    # Serper Clone Configuration (self-hosted Serper-compatible API)
    use_serper_clone: bool = False  # Set to True if using Serper Clone
    serper_clone_url: str = ""  # URL to Serper Clone instance
    
    @property
    def serper_base_url(self) -> str:
        """Get the Serper API base URL (either official or clone)."""
        if self.use_serper_clone:
            return self.serper_clone_url.rstrip("/")
        return "https://google.serper.dev"
    
    @property
    def serper_verify_ssl(self) -> bool:
        """Whether to verify SSL certificates for Serper API.
        
        Disabled for Serper Clone (uses self-signed certs).
        """
        return not self.use_serper_clone
    
    @property
    def serper_is_free(self) -> bool:
        """Whether Serper searches are free (Serper Clone is free)."""
        return self.use_serper_clone
    
    # API key property aliases for consistent access
    @property
    def OPENAI_API_KEY(self) -> Optional[str]:
        return self.openai_api_key
    
    @property
    def ANTHROPIC_API_KEY(self) -> Optional[str]:
        return self.anthropic_api_key
    
    @property
    def SERPER_API_KEY(self) -> Optional[str]:
        return self.serper_api_key
    
    @property
    def ELEVENLABS_API_KEY(self) -> Optional[str]:
        return self.elevenlabs_api_key
    
    # LLM Provider Configuration
    # Provider priority order (comma-separated). Uses first available provider.
    # Default: glm,claude,openai,ollama (cheapest first, GLM has free flash tier, Ollama last)
    llm_provider_priority: str = "glm,claude,openai,ollama"
    
    # Force a specific provider (ignores priority, fails if unavailable)
    llm_force_provider: Optional[str] = None
    
    @property
    def llm_provider_priority_list(self) -> list[str]:
        """Get LLM provider priority as a list."""
        return [p.strip().lower() for p in self.llm_provider_priority.split(",")]
    
    # Ollama Configuration (Local LLM)
    use_ollama: bool = False  # Enable Ollama provider
    ollama_base_url: str = "http://localhost:11434"  # Ollama API endpoint
    # Model tiers: fast,reasoning,quality (comma-separated)
    ollama_model_tiers: str = "hf.co/mradermacher/Nanbeige4.1-3B-GGUF:Q8_0,mistral-nemo:12b,glm-4.7-flash:latest"
    # Context window sizes per tier (comma-separated, matching tier order)
    ollama_context_lengths: str = "262144,65536,8192"
    # Max concurrent requests to Ollama (rate-limiting)
    # Production-reviewed: with 4+ agents (scout, proposal writer, tool scout,
    # campaign manager) running concurrently, a limit of 1 serializes all
    # Ollama requests. 2 allows parallel agent work on modern GPUs (RTX 3090+).
    ollama_max_concurrent: int = 2
    
    @property
    def ollama_model_tiers_dict(self) -> dict[str, str]:
        """Parse Ollama model tiers to dict."""
        tiers = [t.strip() for t in self.ollama_model_tiers.split(",")]
        return {
            "fast": tiers[0] if len(tiers) > 0 else "hf.co/mradermacher/Nanbeige4.1-3B-GGUF:Q8_0",
            "reasoning": tiers[1] if len(tiers) > 1 else "mistral-nemo:12b",
            "quality": tiers[2] if len(tiers) > 2 else "glm-4.7-flash:latest",
        }
    
    @property
    def ollama_context_lengths_dict(self) -> dict[str, int]:
        """Parse Ollama context lengths to dict."""
        lengths = [l.strip() for l in self.ollama_context_lengths.split(",")]
        return {
            "fast": int(lengths[0]) if len(lengths) > 0 else 262144,
            "reasoning": int(lengths[1]) if len(lengths) > 1 else 65536,
            "quality": int(lengths[2]) if len(lengths) > 2 else 8192,
        }
    
    # Optional services
    use_suno: bool = False
    
    # GPU service authentication (shared key for all GPU services)
    # SGA-M7: SecretStr prevents accidental exposure via repr/logging/model_dump
    gpu_service_api_key: SecretStr = SecretStr("")  # X-API-Key header for GPU service auth
    gpu_internal_api_key: SecretStr = SecretStr("")  # GAP-5: Separate key for management endpoints (/unload, /shutdown)
    service_manager_api_key: SecretStr = SecretStr("")  # X-API-Key header for service manager auth
    
    # ACE-Step Local Music Generation
    use_acestep: bool = False  # Enable ACE-Step music generation
    acestep_api_url: str = "http://host.docker.internal:8001"  # ACE-Step API endpoint
    acestep_api_port: int = 8001  # ACE-Step API port
    acestep_api_key: Optional[str] = None  # API key for authentication
    acestep_model: str = "turbo"  # DiT model: turbo (fast, 8 steps max) or base (slow, 300 steps max)
    acestep_auto_start: bool = True  # Auto-start ACE-Step server
    acestep_download_source: str = "auto"  # Model download source: auto, huggingface, modelscope
    
    @property
    def acestep_max_steps(self) -> int:
        """Get maximum inference steps based on model type."""
        return 8 if self.acestep_model == "turbo" else 300
    
    @property
    def acestep_default_steps(self) -> int:
        """Get default inference steps based on model type."""
        # Turbo: always 8 (max), Base: 45 is balanced default (range 27-60)
        return 8 if self.acestep_model == "turbo" else 45
    
    # Qwen3-TTS Local Voice Generation
    use_qwen3_tts: bool = False  # Enable Qwen3-TTS voice generation
    qwen3_tts_api_url: str = "http://host.docker.internal:8002"  # Qwen3-TTS API endpoint
    qwen3_tts_api_port: int = 8002  # Qwen3-TTS API port
    qwen3_tts_tier: str = "auto"  # Model tier: auto, full (1.7B), lite (0.6B)
    qwen3_tts_auto_start: bool = True  # Auto-start Qwen3-TTS server
    qwen3_tts_idle_timeout: int = 300  # Seconds before unloading model from GPU
    
    # GPU Resource Management
    use_gpu: bool = True  # Enable GPU acceleration (gates all GPU tools)
    
    # GPU affinity — which GPU index each service uses (default: 0)
    # Supports comma-separated for services that may use multiple GPUs
    gpu_ollama: str = "0"
    gpu_acestep: str = "0"
    gpu_qwen3_tts: str = "0"
    gpu_zimage: str = "0"
    gpu_seedvr2: str = "0"
    gpu_canary_stt: str = "0"
    gpu_audiosr: str = "0"
    gpu_ltx_video: str = "0"
    
    # ComfyUI tools — auto-populated by start.py from discovered workflows
    # Format: name|display_name|wrapper_port|comfyui_url|gpu_indices (semicolon-separated)
    # Example: ltx-2|LTX-2 Video|9902|http://host.docker.internal:8189|0,1
    comfyui_tools: str = ""
    
    @property
    def gpu_ollama_indices(self) -> list[int]:
        """GPU indices for Ollama."""
        return _parse_gpu_indices(self.gpu_ollama)
    
    @property
    def gpu_acestep_indices(self) -> list[int]:
        """GPU indices for ACE-Step."""
        return _parse_gpu_indices(self.gpu_acestep)
    
    @property
    def gpu_qwen3_tts_indices(self) -> list[int]:
        """GPU indices for Qwen3-TTS."""
        return _parse_gpu_indices(self.gpu_qwen3_tts)
    
    @property
    def gpu_zimage_indices(self) -> list[int]:
        """GPU indices for Z-Image."""
        return _parse_gpu_indices(self.gpu_zimage)
    
    @property
    def gpu_seedvr2_indices(self) -> list[int]:
        """GPU indices for SeedVR2 Upscaler."""
        return _parse_gpu_indices(self.gpu_seedvr2)
    
    @property
    def gpu_canary_stt_indices(self) -> list[int]:
        """GPU indices for Canary-STT."""
        return _parse_gpu_indices(self.gpu_canary_stt)
    
    @property
    def gpu_audiosr_indices(self) -> list[int]:
        """GPU indices for AudioSR."""
        return _parse_gpu_indices(self.gpu_audiosr)
    
    @property
    def gpu_ltx_video_indices(self) -> list[int]:
        """GPU indices for LTX-2 Video."""
        return _parse_gpu_indices(self.gpu_ltx_video)
    
    @property
    def comfyui_tools_list(self) -> list[dict]:
        """Parse COMFYUI_TOOLS env var into structured list.
        
        Returns list of dicts with: slug, name, display_name, port,
        comfyui_url, gpu_indices.
        """
        if not self.comfyui_tools:
            return []
        entries = []
        for entry in self.comfyui_tools.split(";"):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split("|")
            if len(parts) >= 5:
                name = parts[0].strip()
                entries.append({
                    "slug": f"comfy-{name}",
                    "name": name,
                    "display_name": parts[1].strip(),
                    "port": int(parts[2].strip()),
                    "comfyui_url": parts[3].strip(),
                    "gpu_indices": _parse_gpu_indices(parts[4]),
                })
        return entries
    
    # Z-Image Local Image Generation
    use_zimage: bool = False  # Enable Z-Image image generation
    zimage_api_url: str = "http://host.docker.internal:8003"  # Z-Image API endpoint
    zimage_api_port: int = 8003  # Z-Image API port
    zimage_model: str = "turbo"  # Model variant: turbo (8 steps) or base (50 steps)
    zimage_auto_start: bool = True  # Auto-start Z-Image server
    zimage_idle_timeout: int = 300  # Seconds before unloading model from GPU
    
    # SeedVR2 Upscaler (Local Image & Video Upscaling)
    use_seedvr2: bool = False  # Enable SeedVR2 upscaler
    seedvr2_api_url: str = "http://host.docker.internal:8004"  # SeedVR2 API endpoint
    seedvr2_api_port: int = 8004  # SeedVR2 API port
    seedvr2_model: str = "seedvr2_ema_3b_fp8_e4m3fn.safetensors"  # DiT model
    seedvr2_auto_start: bool = True  # Auto-start SeedVR2 server
    seedvr2_idle_timeout: int = 300  # Seconds before unloading model from GPU
    
    # Canary-STT (Local Speech-to-Text)
    use_canary_stt: bool = False  # Enable Canary-STT transcription
    canary_stt_api_url: str = "http://host.docker.internal:8005"  # Canary-STT API endpoint
    canary_stt_api_port: int = 8005  # Canary-STT API port
    canary_stt_auto_start: bool = True  # Auto-start Canary-STT server
    canary_stt_idle_timeout: int = 300  # Seconds before unloading model from GPU
    
    # AudioSR (Local Audio Super-Resolution)
    use_audiosr: bool = False  # Enable AudioSR audio enhancement
    audiosr_api_url: str = "http://host.docker.internal:8007"  # AudioSR API endpoint
    audiosr_api_port: int = 8007  # AudioSR API port
    audiosr_model: str = "basic"  # Model variant: basic (all audio) or speech
    audiosr_auto_start: bool = True  # Auto-start AudioSR server
    audiosr_idle_timeout: int = 300  # Seconds before unloading model from GPU
    
    # Media Toolkit (FFmpeg-based Media Composition — CPU-only)
    use_media_toolkit: bool = False  # Enable Media Toolkit composition server
    media_toolkit_api_url: str = "http://host.docker.internal:8008"  # Media Toolkit API endpoint
    media_toolkit_api_port: int = 8008  # Media Toolkit API port
    media_toolkit_auto_start: bool = True  # Auto-start Media Toolkit server
    
    # Real-ESRGAN CPU (CPU-only Image & Video Upscaling)
    use_realesrgan_cpu: bool = False  # Enable Real-ESRGAN CPU upscaler
    realesrgan_cpu_api_url: str = "http://host.docker.internal:8009"  # Real-ESRGAN CPU API endpoint
    realesrgan_cpu_api_port: int = 8009  # Real-ESRGAN CPU API port
    realesrgan_cpu_model: str = "realesr-animevideov3"  # Model name
    realesrgan_cpu_auto_start: bool = True  # Auto-start Real-ESRGAN CPU server
    
    # Docling Document Parser (CPU-only Document Parsing)
    use_docling: bool = False  # Enable Docling document parser
    docling_api_url: str = "http://host.docker.internal:8010"  # Docling API endpoint
    docling_api_port: int = 8010  # Docling API port
    docling_auto_start: bool = True  # Auto-start Docling server
    
    # LTX-2 Video (Local Text-to-Video Generation)
    use_ltx_video: bool = False  # Enable LTX-2 video generation
    ltx_video_api_url: str = "http://host.docker.internal:8006"  # LTX-2 API endpoint
    ltx_video_api_port: int = 8006  # LTX-2 API port
    ltx_video_auto_start: bool = True  # Auto-start LTX-2 server
    ltx_video_idle_timeout: int = 300  # Seconds before unloading model from GPU
    ltx_video_model_dir: str = "models/ltx-2"  # Path to model weights
    
    # GPU Service Manager (host-side agent for restarting GPU services)
    service_manager_url: str = "http://host.docker.internal:9100"  # Service manager API endpoint
    
    # Dev Sandbox (Isolated Agent Development Environment)
    use_dev_sandbox: bool = True  # Enabled by default (Docker is already required)
    dev_sandbox_default_image: str = "python:3.12-slim"  # Default base image
    dev_sandbox_max_concurrent: int = 5  # Max sandboxes at once
    dev_sandbox_default_timeout: int = 300  # Default timeout (seconds)
    dev_sandbox_max_timeout: int = 1800  # Max allowed timeout (30 min)
    dev_sandbox_default_memory: str = "512m"  # Default memory limit
    dev_sandbox_max_memory: str = "2g"  # Max allowed memory
    dev_sandbox_default_cpus: float = 1.0  # Default CPU limit
    dev_sandbox_max_cpus: float = 4.0  # Max allowed CPUs
    dev_sandbox_network_access: bool = False  # Default: no internet
    dev_sandbox_allowed_images: list[str] = [
        "python:3.12-slim",
        "python:3.11-slim",
        "python:3.10-slim",
        "node:20-slim",
        "node:18-slim",
        "ubuntu:22.04",
        "debian:bookworm-slim",
    ]  # Allowed sandbox images (prevents pulling arbitrary images)
    dev_sandbox_artifact_dir: str = "tmp/sandboxes"  # Host-side artifact storage
    
    # Bitcoin / LND Lightning Node
    use_lnd: bool = False  # Enable LND wallet integration
    lnd_rest_url: str = "https://host.docker.internal:8080"  # LND REST API endpoint
    lnd_macaroon_hex: SecretStr = SecretStr("")  # SGA-M7: Hex-encoded admin macaroon (SecretStr prevents accidental logging)
    lnd_tls_verify: bool = True  # Verify TLS cert (set False only for self-signed dev certs)
    lnd_tls_cert: str = ""  # Optional: base64-encoded TLS certificate content
    lnd_tor_proxy: str = ""  # SOCKS5 proxy for Tor .onion addresses (e.g. socks5://tor-proxy:9050)
    lnd_mempool_url: str = "https://mempool.space"  # Mempool Explorer URL for tx links
    lnd_max_payment_sats: int = 10000  # Global failsafe: max sats per single payment (10k sats)
    lnd_rate_limit_sats: int = 100000  # Max cumulative sats in rolling window (100k sats, 0 = disabled)
    lnd_rate_limit_window_seconds: int = 3600  # Rolling window for rate limit (1 hour)
    lnd_velocity_max_txns: int = 5  # Max send txns per window before velocity breaker trips (0 = disabled)
    lnd_velocity_window_seconds: int = 900  # 15-minute window for velocity counting
    
    # Boltz Exchange (Cold Storage — Lightning → On-Chain swaps)
    boltz_api_url: str = "https://api.boltz.exchange/v2"
    boltz_onion_url: str = "http://boltzzzbnus4m7mta3cxmflnps4fp7dueu2tgurstbvrbt6xswzcocyd.onion/api/v2"
    boltz_use_tor: bool = True  # Route Boltz API calls via Tor proxy
    boltz_fallback_clearnet: bool = False  # Fall back to clearnet if Tor unavailable
    
    # Nostr
    use_nostr: bool = False
    nostr_default_relays: str = "wss://relay.damus.io,wss://nos.lol,wss://relay.nostr.band,wss://relay.snort.social"
    # Production-reviewed: social media campaigns need ~15-30 posts/hour for
    # active engagement (replies, reposts, original content across time zones).
    # 10/hour was too restrictive for real campaign execution.
    nostr_post_rate_limit_hour: int = 30  # Max posts per identity per hour
    nostr_post_rate_limit_day: int = 150  # Max posts per identity per day
    nostr_relay_timeout: int = 5  # Seconds to wait for relay response
    nostr_relay_connect_timeout: int = 3  # Seconds to wait for WebSocket connect
    nostr_lightning_address: str = ""  # Default Lightning Address (lud16) for agent profiles (e.g. you@getalby.com)
    
    # CORS
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    
    @property
    def cors_origins_list(self) -> list[str]:
        """Get CORS origins as a list."""
        return [origin.strip() for origin in self.cors_origins.split(",")]
    
    # Logging
    log_level: str = "INFO"

    # Set to True to suppress insecure secret key warnings/errors in development.
    # NEVER set this in production.
    allow_insecure_key: bool = False

    # Known insecure default secret keys — rejected at startup
    _INSECURE_SECRET_KEYS = {
        "dev_secret_key_change_in_production",
        "your_super_secret_key_here_change_this_in_production",
        "changeme",
        "secret",
    }

    def validate_secret_key(self) -> None:
        """Check that SECRET_KEY is not a known insecure default and meets minimum length.
        
        Always enforces unless allow_insecure_key is explicitly True.
        """
        import logging
        _logger = logging.getLogger("app.core.config")
        _key = self.secret_key.get_secret_value()
        if _key in self._INSECURE_SECRET_KEYS:
            if self.allow_insecure_key:
                _logger.warning(
                    "WARNING: SECRET_KEY is set to a known insecure default "
                    "(suppressed by ALLOW_INSECURE_KEY=true). "
                    "Do NOT use this setting in production."
                )
                return
            if self.environment == "production":
                raise RuntimeError(
                    "CRITICAL: SECRET_KEY is set to a known insecure default. "
                    "Generate a secure key with: python -c \"import secrets; print(secrets.token_urlsafe(32))\" "
                    "and set it in your .env file."
                )
            _logger.warning(
                "WARNING: SECRET_KEY is set to a known insecure default. "
                "This is acceptable for local development but MUST be changed before deployment. "
                "Generate a secure key with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        # RT-32: Minimum entropy check
        elif len(_key) < 32:
            if self.environment == "production":
                raise RuntimeError(
                    "CRITICAL: SECRET_KEY must be at least 32 characters. "
                    "Generate a secure key with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
                )
            _logger.warning(
                "WARNING: SECRET_KEY is shorter than 32 characters. "
                "Generate a stronger key with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )


def _parse_gpu_indices(s: str) -> list[int]:
    """Parse comma-separated GPU indices string into list of ints."""
    return [int(x.strip()) for x in s.split(",") if x.strip().isdigit()]


settings = Settings()
settings.validate_secret_key()
