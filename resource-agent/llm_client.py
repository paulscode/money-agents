"""
LLM Client for Campaign Worker.

Provides a robust interface for making LLM API calls from remote workers.
Supports multiple providers with automatic failover: GLM (Zhipu), Claude (Anthropic), OpenAI, Ollama.

Key Features:
- Provider priority with automatic failover (matches main app behavior)
- Model tier support (fast, reasoning, quality)
- Token tracking for cost reporting
- Max tokens standard: 6000 (matches main app standard)
- Ollama support with concurrency limiting
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple

import httpx

from config import CampaignWorkerConfig

logger = logging.getLogger(__name__)


# =============================================================================
# Model Tier Mappings (mirrors main app's llm_service.py MODEL_TIERS)
# =============================================================================

MODEL_TIERS: Dict[str, Dict[str, str]] = {
    "glm": {
        "fast": "glm-4-flash",
        "reasoning": "glm-4-flash",  # GLM doesn't have a reasoning tier, use flash
        "quality": "glm-4-plus",
    },
    "claude": {
        "fast": "claude-3-haiku-20240307",
        "reasoning": "claude-sonnet-4-20250514",
        "quality": "claude-opus-4-20250514",
    },
    "openai": {
        "fast": "gpt-4o-mini",
        "reasoning": "o1-mini",
        "quality": "gpt-4o",
    },
    # Ollama tiers are configured dynamically from config
}

# Provider API endpoints
DEFAULT_ENDPOINTS: Dict[str, str] = {
    "glm": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    "claude": "https://api.anthropic.com/v1/messages",
    "openai": "https://api.openai.com/v1/chat/completions",
    "ollama": "http://localhost:11434",  # Default Ollama endpoint
    # Aliases
    "zhipu": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
}

# Provider name normalization
PROVIDER_ALIASES: Dict[str, str] = {
    "zhipu": "glm",
    "anthropic": "claude",
}


@dataclass
class LLMMessage:
    """A message in the conversation."""
    role: str  # "system", "user", "assistant"
    content: str


@dataclass
class LLMResponse:
    """Response from an LLM call."""
    content: str
    model: str
    provider: str = ""
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    finish_reason: str = "stop"


class LLMProviderError(Exception):
    """Error from a specific LLM provider."""
    def __init__(self, provider: str, message: str):
        self.provider = provider
        super().__init__(f"{provider}: {message}")


class LLMClient:
    """
    Async LLM client with multi-provider failover support.
    
    Matches the main app's provider priority behavior:
    - Tries providers in configured priority order (default: glm, claude, openai)
    - Skips providers without API keys configured
    - Falls back to next provider on failure
    - Supports model tier resolution (fast, reasoning, quality)
    
    Usage:
        client = LLMClient(config.campaign_worker)
        
        # Basic call (uses default tier)
        response = await client.chat([
            LLMMessage("system", "You are helpful."),
            LLMMessage("user", "Hello!"),
        ])
        
        # Call with specific tier
        response = await client.chat(messages, model_tier="reasoning")
        
        # Call with explicit model
        response = await client.chat(messages, model="gpt-4o-mini")
    """
    
    # Standard max tokens (main app standard)
    DEFAULT_MAX_TOKENS = 6000
    
    def __init__(self, config: CampaignWorkerConfig):
        """
        Initialize LLM client.
        
        Args:
            config: Campaign worker configuration with LLM settings
        """
        self.config = config
        self.default_model_tier = config.llm_default_model_tier or "reasoning"
        self.max_tokens = config.llm_max_tokens or self.DEFAULT_MAX_TOKENS
        
        # Get available providers in priority order
        self.available_providers = config.get_available_providers()
        
        if not self.available_providers:
            raise ValueError(
                "No LLM providers configured. Set at least one of: "
                "ANTHROPIC_API_KEY, OPENAI_API_KEY, Z_AI_API_KEY, or USE_OLLAMA=true"
            )
        
        # Ollama concurrency tracking
        self._ollama_current_requests = 0
        self._ollama_max_concurrent = config.ollama_max_concurrent
        self._ollama_lock = asyncio.Lock()
        
        # Cache Ollama model tiers from config
        self._ollama_model_tiers = config.ollama_model_tiers_dict if config.use_ollama else {}
        
        logger.info(
            f"LLM client initialized: providers={self.available_providers}, "
            f"default_tier={self.default_model_tier}, max_tokens={self.max_tokens}"
        )
        if config.use_ollama:
            logger.info(
                f"Ollama enabled: base_url={config.ollama_base_url}, "
                f"max_concurrent={self._ollama_max_concurrent}, "
                f"models={self._ollama_model_tiers}"
            )
        
        # HTTP client with generous timeout
        self._client = httpx.AsyncClient(timeout=120.0)
        
        # Token tracking
        self.total_tokens_used = 0
        self.total_calls = 0
    
    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()
    
    def _normalize_provider(self, provider: str) -> str:
        """Normalize provider name (e.g., 'anthropic' -> 'claude')."""
        provider = provider.lower()
        return PROVIDER_ALIASES.get(provider, provider)
    
    def _get_endpoint(self, provider: str) -> str:
        """Get API endpoint for a provider."""
        custom = self.config.get_api_base(provider)
        if custom:
            return custom
        return DEFAULT_ENDPOINTS.get(provider, "")
    
    def _resolve_model(
        self,
        provider: str,
        model: Optional[str] = None,
        model_tier: Optional[str] = None,
    ) -> str:
        """
        Resolve the model to use for a provider.
        
        Priority:
        1. Explicit model parameter
        2. Model tier lookup
        3. Default tier lookup
        """
        if model:
            return model
        
        tier = model_tier or self.default_model_tier
        normalized = self._normalize_provider(provider)
        
        # Ollama uses config-driven model tiers
        if normalized == "ollama":
            tier_map = self._ollama_model_tiers
        else:
            tier_map = MODEL_TIERS.get(normalized, {})
        
        resolved = tier_map.get(tier)
        
        if not resolved:
            # Fallback to reasoning tier
            resolved = tier_map.get("reasoning", list(tier_map.values())[0] if tier_map else None)
        
        if not resolved:
            raise ValueError(f"Cannot resolve model for provider {provider}, tier {tier}")
        
        return resolved
    
    async def chat(
        self,
        messages: List[LLMMessage],
        model: Optional[str] = None,
        model_tier: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        force_provider: Optional[str] = None,
    ) -> LLMResponse:
        """
        Make a chat completion request with automatic failover.
        
        Args:
            messages: List of conversation messages
            model: Explicit model to use (overrides tier)
            model_tier: Model tier (fast, reasoning, quality)
            temperature: Sampling temperature
            max_tokens: Max tokens to generate (default: 6000)
            force_provider: Force a specific provider (no failover)
        
        Returns:
            LLMResponse with content and metadata
            
        Raises:
            LLMProviderError: If all providers fail
        """
        max_tokens = max_tokens or self.max_tokens
        
        # Determine providers to try
        if force_provider:
            providers = [self._normalize_provider(force_provider)]
        else:
            providers = [self._normalize_provider(p) for p in self.available_providers]
        
        errors = []
        
        for provider in providers:
            # Ollama doesn't use API keys, uses use_ollama flag instead
            if provider == "ollama":
                if not self.config.use_ollama:
                    continue
            else:
                api_key = self.config.get_api_key(provider)
                if not api_key:
                    continue
            
            resolved_model = self._resolve_model(provider, model, model_tier)
            endpoint = self._get_endpoint(provider)
            
            logger.debug(f"Trying provider {provider} with model {resolved_model}")
            
            try:
                start_time = time.time()
                
                if provider == "claude":
                    api_key = self.config.get_api_key(provider)
                    response = await self._call_anthropic(
                        messages, resolved_model, temperature, max_tokens, api_key, endpoint
                    )
                elif provider == "ollama":
                    response = await self._call_ollama(
                        messages, resolved_model, temperature, max_tokens, endpoint
                    )
                else:
                    # GLM and OpenAI use same format
                    api_key = self.config.get_api_key(provider)
                    response = await self._call_openai(
                        messages, resolved_model, temperature, max_tokens, api_key, endpoint
                    )
                
                response.provider = provider
                response.latency_ms = int((time.time() - start_time) * 1000)
                
                # Track usage
                self.total_tokens_used += response.total_tokens
                self.total_calls += 1
                
                logger.info(
                    f"LLM response: provider={provider}, model={response.model}, "
                    f"tokens={response.total_tokens}, latency={response.latency_ms}ms"
                )
                
                return response
                
            except Exception as e:
                error_msg = str(e)
                logger.warning(f"Provider {provider} failed: {error_msg}")
                errors.append((provider, error_msg))
                
                # Continue to next provider
                continue
        
        # All providers failed
        error_details = "; ".join([f"{p}: {e}" for p, e in errors])
        raise LLMProviderError("all", f"All providers failed: {error_details}")
    
    async def _call_anthropic(
        self,
        messages: List[LLMMessage],
        model: str,
        temperature: float,
        max_tokens: int,
        api_key: str,
        endpoint: str,
    ) -> LLMResponse:
        """Call Anthropic (Claude) API."""
        # Separate system message from others
        system_content = ""
        chat_messages = []
        
        for msg in messages:
            if msg.role == "system":
                system_content = msg.content
            else:
                chat_messages.append({
                    "role": msg.role,
                    "content": msg.content
                })
        
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": chat_messages,
        }
        
        if system_content:
            payload["system"] = system_content
        
        response = await self._client.post(
            endpoint,
            headers=headers,
            json=payload,
        )
        
        if response.status_code != 200:
            error_body = response.text
            raise LLMProviderError("claude", f"HTTP {response.status_code}: {error_body[:200]}")
        
        data = response.json()
        
        content = ""
        if data.get("content"):
            for block in data["content"]:
                if block.get("type") == "text":
                    content += block.get("text", "")
        
        usage = data.get("usage", {})
        
        return LLMResponse(
            content=content,
            model=data.get("model", model),
            total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            finish_reason=data.get("stop_reason", "stop"),
        )
    
    async def _call_openai(
        self,
        messages: List[LLMMessage],
        model: str,
        temperature: float,
        max_tokens: int,
        api_key: str,
        endpoint: str,
    ) -> LLMResponse:
        """Call OpenAI-compatible API (OpenAI, Zhipu GLM, etc.)."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        
        response = await self._client.post(
            endpoint,
            headers=headers,
            json=payload,
        )
        
        if response.status_code != 200:
            error_body = response.text
            raise LLMProviderError("openai", f"HTTP {response.status_code}: {error_body[:200]}")
        
        data = response.json()
        
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})
        
        return LLMResponse(
            content=message.get("content", ""),
            model=data.get("model", model),
            total_tokens=usage.get("total_tokens", 0),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", "stop"),
        )
    
    async def _call_ollama(
        self,
        messages: List[LLMMessage],
        model: str,
        temperature: float,
        max_tokens: int,
        base_url: str,
    ) -> LLMResponse:
        """Call Ollama API with concurrency limiting.
        
        Ollama is often rate-limited (default: 1 concurrent request) since it
        runs on limited hardware. This method enforces concurrency limits and
        queues requests if necessary.
        """
        # Check concurrency limit
        async with self._ollama_lock:
            if self._ollama_current_requests >= self._ollama_max_concurrent:
                raise LLMProviderError(
                    "ollama",
                    f"Concurrency limit reached ({self._ollama_max_concurrent}). "
                    "Ollama is likely processing another request."
                )
            self._ollama_current_requests += 1
        
        try:
            # Build Ollama API request
            endpoint = f"{base_url.rstrip('/')}/api/chat"
            
            payload = {
                "model": model,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                },
            }
            
            response = await self._client.post(
                endpoint,
                json=payload,
                timeout=300.0,  # Ollama may be slow for large models
            )
            
            if response.status_code != 200:
                error_body = response.text
                raise LLMProviderError("ollama", f"HTTP {response.status_code}: {error_body[:200]}")
            
            data = response.json()
            
            # Ollama response format
            message_content = data.get("message", {}).get("content", "")
            
            # Token tracking (Ollama provides these in different fields)
            prompt_tokens = data.get("prompt_eval_count", 0)
            completion_tokens = data.get("eval_count", 0)
            
            return LLMResponse(
                content=message_content,
                model=data.get("model", model),
                total_tokens=prompt_tokens + completion_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                finish_reason=data.get("done_reason", "stop"),
            )
        
        finally:
            # Release concurrency slot
            async with self._ollama_lock:
                self._ollama_current_requests -= 1


# =============================================================================
# Testing
# =============================================================================

async def _test_client():
    """Test the LLM client with failover."""
    from config import load_config
    
    config = load_config()
    
    available = config.campaign_worker.get_available_providers()
    if not available:
        print("No LLM API keys configured, skipping test")
        return
    
    print(f"Available providers: {available}")
    
    client = LLMClient(config.campaign_worker)
    
    try:
        # Test with default tier
        print("\n--- Testing with default tier (reasoning) ---")
        response = await client.chat([
            LLMMessage(role="user", content="Say 'Hello, World!' and nothing else.")
        ])
        print(f"Provider: {response.provider}")
        print(f"Model: {response.model}")
        print(f"Response: {response.content}")
        print(f"Tokens: {response.total_tokens}")
        print(f"Latency: {response.latency_ms}ms")
        
        # Test with fast tier
        print("\n--- Testing with fast tier ---")
        response = await client.chat(
            [LLMMessage(role="user", content="Say 'Fast!' and nothing else.")],
            model_tier="fast"
        )
        print(f"Provider: {response.provider}")
        print(f"Model: {response.model}")
        print(f"Response: {response.content}")
        
        print(f"\nTotal tokens used: {client.total_tokens_used}")
        print(f"Total calls: {client.total_calls}")
        
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(_test_client())
