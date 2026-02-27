"""
Configuration loader for Resource Agent.

Supports YAML config files and environment variable overrides.
"""
import os
import socket
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class BrokerConfig(BaseModel):
    """Broker connection configuration."""
    url: str = Field(default="ws://localhost:8000/api/v1/broker/agent")
    api_key: str = Field(default="")
    reconnect_delay: int = Field(default=5, ge=1)
    max_reconnect_delay: int = Field(default=60, ge=5)
    heartbeat_interval: int = Field(default=30, ge=5)


class AgentConfig(BaseModel):
    """Agent identity configuration."""
    name: Optional[str] = None
    description: str = Field(default="")
    tags: list[str] = Field(default_factory=list)
    work_dir: Optional[str] = None
    
    def get_name(self) -> str:
        """Get agent name, defaulting to hostname."""
        return self.name or socket.gethostname()
    
    def get_work_dir(self) -> Path:
        """Get work directory, creating if needed."""
        if self.work_dir:
            path = Path(self.work_dir)
        else:
            path = Path(__file__).parent / "work"
        path.mkdir(parents=True, exist_ok=True)
        return path


class CapabilitiesConfig(BaseModel):
    """Capabilities override configuration."""
    gpu_enabled: Optional[bool] = None  # None = auto-detect
    max_concurrent_jobs: int = Field(default=1, ge=1)
    storage_paths: list[str] = Field(default_factory=list)


class CampaignWorkerConfig(BaseModel):
    """Campaign worker configuration for distributed campaign execution."""
    enabled: bool = Field(default=False)  # Enable campaign worker mode
    max_campaigns: int = Field(default=3, ge=1)  # Max simultaneous campaigns
    heartbeat_interval: int = Field(default=60, ge=10)  # Lease renewal interval
    
    # LLM Provider Priority - tries providers in order, skipping unavailable ones
    # Matches main app's LLM_PROVIDER_PRIORITY behavior
    # Ollama should always be last (quaternary) since it's rate-limited
    llm_provider_priority: str = Field(default="glm,claude,openai,ollama")
    
    # API Keys - one per provider (set the ones you have)
    anthropic_api_key: str = Field(default="")  # For claude provider
    openai_api_key: str = Field(default="")  # For openai provider  
    zhipu_api_key: str = Field(default="")  # For glm provider (Z.ai)
    
    # Optional custom endpoints
    anthropic_api_base: Optional[str] = None
    openai_api_base: Optional[str] = None
    zhipu_api_base: Optional[str] = None
    
    # Ollama local LLM configuration
    use_ollama: bool = Field(default=False)  # Enable Ollama as quaternary provider
    ollama_base_url: str = Field(default="http://localhost:11434")
    # Model tiers: fast,reasoning,quality (comma-separated)
    ollama_model_tiers: str = Field(default="mistral:7b,mistral-nemo:12b,glm-4.7-flash:latest")
    # Context lengths for each tier (comma-separated, matching tier order)
    ollama_context_lengths: str = Field(default="32768,128000,32768")
    # Max concurrent requests (Ollama is often rate-limited to 1)
    ollama_max_concurrent: int = Field(default=1, ge=1)
    
    # Model settings - these can be overridden by campaign assignment
    llm_default_model_tier: str = Field(default="reasoning")  # fast, reasoning, quality
    llm_max_tokens: int = Field(default=6000)  # Standard: always use 6000
    
    # Legacy single-provider fields (deprecated, use above instead)
    llm_provider: str = Field(default="")  # Deprecated
    llm_api_key: str = Field(default="")  # Deprecated
    llm_api_base: Optional[str] = None  # Deprecated
    llm_default_model: str = Field(default="")  # Deprecated
    
    @property
    def provider_priority_list(self) -> list[str]:
        """Get provider priority as list."""
        return [p.strip() for p in self.llm_provider_priority.split(",") if p.strip()]
    
    @property
    def ollama_model_tiers_dict(self) -> dict[str, str]:
        """Parse ollama_model_tiers into {tier: model} dict."""
        models = [m.strip() for m in self.ollama_model_tiers.split(",")]
        tiers = ["fast", "reasoning", "quality"]
        return {tier: models[i] if i < len(models) else models[-1] for i, tier in enumerate(tiers)}
    
    @property
    def ollama_context_lengths_dict(self) -> dict[str, int]:
        """Parse ollama_context_lengths into {tier: length} dict."""
        lengths = [int(l.strip()) for l in self.ollama_context_lengths.split(",")]
        tiers = ["fast", "reasoning", "quality"]
        return {tier: lengths[i] if i < len(lengths) else lengths[-1] for i, tier in enumerate(tiers)}
    
    def get_api_key(self, provider: str) -> str:
        """Get API key for a specific provider."""
        key_map = {
            "claude": self.anthropic_api_key,
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "glm": self.zhipu_api_key,
            "zhipu": self.zhipu_api_key,
        }
        # Check new keys first, fall back to legacy
        key = key_map.get(provider, "")
        if not key and self.llm_api_key:
            # Legacy: single key configured
            return self.llm_api_key
        return key
    
    def get_api_base(self, provider: str) -> Optional[str]:
        """Get API base URL for a provider."""
        base_map = {
            "claude": self.anthropic_api_base,
            "anthropic": self.anthropic_api_base,
            "openai": self.openai_api_base,
            "glm": self.zhipu_api_base,
            "zhipu": self.zhipu_api_base,
            "ollama": self.ollama_base_url,
        }
        # Check new bases first, fall back to legacy
        base = base_map.get(provider)
        if not base and self.llm_api_base:
            return self.llm_api_base
        return base
    
    def is_provider_available(self, provider: str) -> bool:
        """Check if a provider is available (has API key or is Ollama with use_ollama=True)."""
        if provider == "ollama":
            return self.use_ollama
        return bool(self.get_api_key(provider))
    
    def get_available_providers(self) -> list[str]:
        """Get list of providers that are available, in priority order.
        
        For cloud providers, requires API key. For Ollama, requires use_ollama=True.
        """
        available = []
        for provider in self.provider_priority_list:
            if self.is_provider_available(provider):
                available.append(provider)
        return available


class LoggingConfig(BaseModel):
    """Logging configuration."""
    level: str = Field(default="INFO")
    format: str = Field(default="console")  # console or json
    file: Optional[str] = None


class Config(BaseModel):
    """Complete agent configuration."""
    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    capabilities: CapabilitiesConfig = Field(default_factory=CapabilitiesConfig)
    campaign_worker: CampaignWorkerConfig = Field(default_factory=CampaignWorkerConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def load_config(config_path: Optional[str] = None) -> Config:
    """
    Load configuration from file and environment.
    
    Priority (highest to lowest):
    1. Environment variables
    2. Config file
    3. Defaults
    
    Args:
        config_path: Path to YAML config file. If None, looks for config.yaml
                    in the current directory and script directory.
    """
    config_data = {}
    
    # Find config file
    if config_path:
        paths_to_try = [Path(config_path)]
    else:
        paths_to_try = [
            Path("config.yaml"),
            Path(__file__).parent / "config.yaml",
        ]
    
    for path in paths_to_try:
        if path.exists():
            with open(path) as f:
                config_data = yaml.safe_load(f) or {}
            break
    
    # Create config with file values
    config = Config(**config_data)
    
    # Apply environment variable overrides
    if os.environ.get("BROKER_URL"):
        config.broker.url = os.environ["BROKER_URL"]
    
    if os.environ.get("BROKER_API_KEY"):
        config.broker.api_key = os.environ["BROKER_API_KEY"]
    
    if os.environ.get("AGENT_NAME"):
        config.agent.name = os.environ["AGENT_NAME"]
    
    if os.environ.get("LOG_LEVEL"):
        config.logging.level = os.environ["LOG_LEVEL"]
    
    if os.environ.get("WORK_DIR"):
        config.agent.work_dir = os.environ["WORK_DIR"]
    
    if os.environ.get("MAX_CONCURRENT_JOBS"):
        config.capabilities.max_concurrent_jobs = int(os.environ["MAX_CONCURRENT_JOBS"])
    
    # Campaign worker environment overrides
    if os.environ.get("CAMPAIGN_WORKER_ENABLED"):
        config.campaign_worker.enabled = os.environ["CAMPAIGN_WORKER_ENABLED"].lower() in ("true", "1", "yes")
    
    if os.environ.get("CAMPAIGN_MAX_CAMPAIGNS"):
        config.campaign_worker.max_campaigns = int(os.environ["CAMPAIGN_MAX_CAMPAIGNS"])
    
    # Provider priority (same env var name as main app)
    if os.environ.get("LLM_PROVIDER_PRIORITY"):
        config.campaign_worker.llm_provider_priority = os.environ["LLM_PROVIDER_PRIORITY"]
    
    # API keys per provider (same env var names as main app)
    if os.environ.get("ANTHROPIC_API_KEY"):
        config.campaign_worker.anthropic_api_key = os.environ["ANTHROPIC_API_KEY"]
    
    if os.environ.get("OPENAI_API_KEY"):
        config.campaign_worker.openai_api_key = os.environ["OPENAI_API_KEY"]
    
    if os.environ.get("Z_AI_API_KEY"):
        config.campaign_worker.zhipu_api_key = os.environ["Z_AI_API_KEY"]
    
    # Custom API bases
    if os.environ.get("ANTHROPIC_API_BASE"):
        config.campaign_worker.anthropic_api_base = os.environ["ANTHROPIC_API_BASE"]
    
    if os.environ.get("OPENAI_API_BASE"):
        config.campaign_worker.openai_api_base = os.environ["OPENAI_API_BASE"]
    
    if os.environ.get("ZHIPU_API_BASE"):
        config.campaign_worker.zhipu_api_base = os.environ["ZHIPU_API_BASE"]
    
    # Model tier override
    if os.environ.get("LLM_MODEL_TIER"):
        config.campaign_worker.llm_default_model_tier = os.environ["LLM_MODEL_TIER"]
    
    # Legacy single-provider support (deprecated)
    if os.environ.get("LLM_PROVIDER"):
        config.campaign_worker.llm_provider = os.environ["LLM_PROVIDER"]
    
    if os.environ.get("LLM_API_KEY"):
        config.campaign_worker.llm_api_key = os.environ["LLM_API_KEY"]
    
    if os.environ.get("LLM_API_BASE"):
        config.campaign_worker.llm_api_base = os.environ["LLM_API_BASE"]
    
    if os.environ.get("LLM_DEFAULT_MODEL"):
        config.campaign_worker.llm_default_model = os.environ["LLM_DEFAULT_MODEL"]
    
    return config
