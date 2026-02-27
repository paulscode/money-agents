"""LLM service with 3-tier fallback logic."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator, Dict, Iterable, List, Optional, Tuple

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger(__name__)


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Calculate cost in USD for token usage.
    
    Uses pricing from usage_service.MODEL_PRICING for consistency.
    
    Args:
        model: The model name used
        prompt_tokens: Number of input tokens
        completion_tokens: Number of output tokens
        
    Returns:
        Cost in USD (rounded to 6 decimal places for precision)
    """
    # Import here to avoid circular import
    from app.services.usage_service import get_model_pricing
    
    input_price, output_price = get_model_pricing(model)
    
    # Pricing is per 1M tokens
    cost = (prompt_tokens * input_price / 1_000_000) + (completion_tokens * output_price / 1_000_000)
    return round(cost, 6)


@dataclass(frozen=True)
class LLMMessage:
    role: str
    content: str


@dataclass(frozen=True)
class LLMResponse:
    content: str
    model: str
    provider: str
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    finish_reason: str = "stop"  # "stop" = natural, "length" = truncated (hit max_tokens)


def _check_truncation(finish_reason: str, model: str, provider: str, completion_tokens: int, max_tokens: int) -> None:
    """Log warning if response was truncated due to max_tokens limit.
    
    This helps identify potential runaway responses or cases where max_tokens
    is set too low. Logged as WARNING level for monitoring system integration.
    """
    if finish_reason == "length" or (finish_reason == "end_turn" and completion_tokens >= max_tokens * 0.95):
        logger.warning(
            "LLM response truncated (hit max_tokens limit)",
            extra={
                "event_type": "llm_truncation",
                "model": model,
                "provider": provider,
                "completion_tokens": completion_tokens,
                "max_tokens": max_tokens,
                "finish_reason": finish_reason,
            }
        )


@dataclass
class StreamChunk:
    """A chunk of streaming response."""
    content: str
    is_final: bool = False
    model: Optional[str] = None
    provider: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0


class LLMError(Exception):
    """Base LLM error."""


class LLMProviderUnavailable(LLMError):
    """Raised when an LLM provider is not configured or not available."""


# Placeholder values from .env.example that should be treated as unconfigured
_PLACEHOLDER_KEYS = {
    "your_openai_api_key_here",
    "your_anthropic_api_key_here",
    "your_zai_api_key_here",
}


def _is_real_api_key(key: Optional[str]) -> bool:
    """Check if an API key is a real key (not empty, not a placeholder)."""
    return bool(key) and key not in _PLACEHOLDER_KEYS


class BaseProvider:
    name: str

    def is_configured(self) -> bool:
        return True

    async def generate(
        self,
        model: str,
        messages: List[LLMMessage],
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        raise NotImplementedError

    async def generate_stream(
        self,
        model: str,
        messages: List[LLMMessage],
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError
        yield  # Make it a generator


class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(self, api_key_attr: str = "openai_api_key") -> None:
        # Store the settings attribute name, not the key itself (GAP: LOW-3)
        self._api_key_attr = api_key_attr
        self._client: Optional[AsyncOpenAI] = None

    @property
    def _api_key(self) -> Optional[str]:
        """Read API key lazily from settings to reduce exposure window."""
        return getattr(settings, self._api_key_attr, None)

    def is_configured(self) -> bool:
        return _is_real_api_key(self._api_key)

    def _client_instance(self) -> AsyncOpenAI:
        if not self._client:
            self._client = AsyncOpenAI(api_key=self._api_key)
        return self._client

    def _build_kwargs(self, model: str, messages: List[dict], temperature: float, max_tokens: int) -> dict:
        """Build kwargs for OpenAI API call."""
        model_lower = model.lower()
        # Only o1 reasoning models require max_completion_tokens instead of max_tokens
        use_completion_tokens = "o1" in model_lower and "gpt" not in model_lower
        
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if use_completion_tokens:
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
        return kwargs

    async def generate(
        self,
        model: str,
        messages: List[LLMMessage],
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        if not self.is_configured():
            raise LLMProviderUnavailable("OpenAI API key not configured")

        payload = [{"role": m.role, "content": m.content} for m in messages]
        start = time.monotonic()

        kwargs = self._build_kwargs(model, payload, temperature, max_tokens)
        completion = await self._client_instance().chat.completions.create(**kwargs)

        choice = completion.choices[0]
        content = choice.message.content or ""
        finish_reason = choice.finish_reason or "stop"
        latency_ms = int((time.monotonic() - start) * 1000)
        usage = completion.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        
        # Check for truncation
        _check_truncation(finish_reason, model, self.name, completion_tokens, max_tokens)
        
        return LLMResponse(
            content=content,
            model=model,
            provider=self.name,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=usage.total_tokens if usage else 0,
            cost_usd=calculate_cost(model, prompt_tokens, completion_tokens),
            finish_reason=finish_reason,
        )

    async def generate_stream(
        self,
        model: str,
        messages: List[LLMMessage],
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamChunk]:
        if not self.is_configured():
            raise LLMProviderUnavailable("OpenAI API key not configured")

        payload = [{"role": m.role, "content": m.content} for m in messages]
        start = time.monotonic()

        kwargs = self._build_kwargs(model, payload, temperature, max_tokens)
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}

        stream = await self._client_instance().chat.completions.create(**kwargs)

        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        async for chunk in stream:
            # Capture usage from final chunk
            if chunk.usage:
                prompt_tokens = chunk.usage.prompt_tokens
                completion_tokens = chunk.usage.completion_tokens
                total_tokens = chunk.usage.total_tokens

            if chunk.choices and chunk.choices[0].delta.content:
                yield StreamChunk(content=chunk.choices[0].delta.content)

        # Send final chunk with metadata
        latency_ms = int((time.monotonic() - start) * 1000)
        yield StreamChunk(
            content="",
            is_final=True,
            model=model,
            provider=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self, api_key_attr: str = "anthropic_api_key") -> None:
        # Store the settings attribute name, not the key itself (GAP: LOW-3)
        self._api_key_attr = api_key_attr
        self._client: Optional[AsyncAnthropic] = None

    @property
    def _api_key(self) -> Optional[str]:
        """Read API key lazily from settings to reduce exposure window."""
        return getattr(settings, self._api_key_attr, None)

    def is_configured(self) -> bool:
        return _is_real_api_key(self._api_key)

    def _client_instance(self) -> AsyncAnthropic:
        if not self._client:
            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    def _prepare_messages(self, messages: List[LLMMessage]) -> tuple[Optional[str], List[dict]]:
        """Prepare messages for Anthropic API format."""
        system_chunks: List[str] = []
        anthropic_messages: List[dict] = []

        for message in messages:
            if message.role == "system":
                system_chunks.append(message.content)
            elif message.role in {"user", "assistant"}:
                anthropic_messages.append({"role": message.role, "content": message.content})
            else:
                anthropic_messages.append({"role": "user", "content": message.content})

        system = "\n\n".join(system_chunks) if system_chunks else None
        return system, anthropic_messages

    async def generate(
        self,
        model: str,
        messages: List[LLMMessage],
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        if not self.is_configured():
            raise LLMProviderUnavailable("Anthropic API key not configured")

        system, anthropic_messages = self._prepare_messages(messages)

        start = time.monotonic()
        
        # Only include system parameter if we have a system prompt
        kwargs = {
            "model": model,
            "messages": anthropic_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system:
            kwargs["system"] = system
            
        response = await self._client_instance().messages.create(**kwargs)

        content_blocks = response.content or []
        content = "".join(block.text for block in content_blocks if hasattr(block, "text"))
        # Anthropic uses "end_turn" for normal completion, "max_tokens" for truncation
        finish_reason = response.stop_reason or "end_turn"
        # Normalize to OpenAI-style finish_reason for consistency
        normalized_finish_reason = "length" if finish_reason == "max_tokens" else "stop"
        latency_ms = int((time.monotonic() - start) * 1000)
        usage = response.usage
        prompt_tokens = usage.input_tokens if usage else 0
        completion_tokens = usage.output_tokens if usage else 0
        
        # Check for truncation
        _check_truncation(normalized_finish_reason, model, self.name, completion_tokens, max_tokens)
        
        return LLMResponse(
            content=content,
            model=model,
            provider=self.name,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=(prompt_tokens + completion_tokens),
            cost_usd=calculate_cost(model, prompt_tokens, completion_tokens),
            finish_reason=normalized_finish_reason,
        )

    async def generate_stream(
        self,
        model: str,
        messages: List[LLMMessage],
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamChunk]:
        if not self.is_configured():
            raise LLMProviderUnavailable("Anthropic API key not configured")

        system, anthropic_messages = self._prepare_messages(messages)

        start = time.monotonic()
        
        prompt_tokens = 0
        completion_tokens = 0

        # Only include system parameter if we have a system prompt
        kwargs = {
            "model": model,
            "messages": anthropic_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system:
            kwargs["system"] = system

        async with self._client_instance().messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield StreamChunk(content=text)

            # Get final message for usage stats
            final_message = await stream.get_final_message()
            if final_message.usage:
                prompt_tokens = final_message.usage.input_tokens
                completion_tokens = final_message.usage.output_tokens

        latency_ms = int((time.monotonic() - start) * 1000)
        yield StreamChunk(
            content="",
            is_final=True,
            model=model,
            provider=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            latency_ms=latency_ms,
        )


class ZaiProvider(BaseProvider):
    """Zhipu AI (GLM) provider using native SDK for reasoning model support."""
    name = "zai"

    def __init__(self, api_key_attr: str = "z_ai_api_key", base_url: str = "") -> None:
        # Store the settings attribute name, not the key itself (GAP: LOW-3)
        self._api_key_attr = api_key_attr
        self._base_url = base_url  # Not used with native SDK
        self._client = None

    @property
    def _api_key(self) -> Optional[str]:
        """Read API key lazily from settings to reduce exposure window."""
        return getattr(settings, self._api_key_attr, None)

    def is_configured(self) -> bool:
        return _is_real_api_key(self._api_key)

    def _client_instance(self):
        if not self._client:
            try:
                from zhipuai import ZhipuAI
                self._client = ZhipuAI(api_key=self._api_key)
            except ImportError:
                raise LLMProviderUnavailable("zhipuai package not installed")
        return self._client

    async def generate(
        self,
        model: str,
        messages: List[LLMMessage],
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        if not self.is_configured():
            raise LLMProviderUnavailable("Z.ai API key not configured")

        payload = [{"role": m.role, "content": m.content} for m in messages]
        start = time.monotonic()

        # Use sync client in thread pool (zhipuai SDK is sync)
        import asyncio
        loop = asyncio.get_event_loop()
        completion = await loop.run_in_executor(
            None,
            lambda: self._client_instance().chat.completions.create(
                model=model,
                messages=payload,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        )

        # Handle reasoning models (GLM-4.7) that have reasoning_content
        choice = completion.choices[0]
        message = choice.message
        content = message.content or ""
        finish_reason = choice.finish_reason or "stop"
        # If content is empty but reasoning_content exists, the model may still be "thinking"
        # For reasoning models, we need the final content after reasoning completes
        if not content and hasattr(message, 'reasoning_content') and message.reasoning_content:
            # Log that we got reasoning but no final content (likely max_tokens too low)
            logger.warning(
                "GLM reasoning model returned reasoning_content but no content - may need higher max_tokens",
                extra={"model": model, "reasoning_content_length": len(message.reasoning_content)}
            )
        
        latency_ms = int((time.monotonic() - start) * 1000)
        usage = completion.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        
        # Check for truncation
        _check_truncation(finish_reason, model, self.name, completion_tokens, max_tokens)
        
        return LLMResponse(
            content=content,
            model=model,
            provider=self.name,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=usage.total_tokens if usage else 0,
            cost_usd=calculate_cost(model, prompt_tokens, completion_tokens),
            finish_reason=finish_reason,
        )

    async def generate_stream(
        self,
        model: str,
        messages: List[LLMMessage],
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamChunk]:
        if not self.is_configured():
            raise LLMProviderUnavailable("Z.ai API key not configured")

        payload = [{"role": m.role, "content": m.content} for m in messages]
        start = time.monotonic()

        # Use sync streaming in thread with queue for async iteration
        import asyncio
        import queue
        import threading
        
        q: queue.Queue = queue.Queue()
        
        def stream_worker():
            try:
                stream = self._client_instance().chat.completions.create(
                    model=model,
                    messages=payload,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                )
                for chunk in stream:
                    q.put(("chunk", chunk))
                q.put(("done", None))
            except Exception as e:
                q.put(("error", e))
        
        thread = threading.Thread(target=stream_worker)
        thread.start()

        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        while True:
            # Non-blocking check with small sleep
            try:
                msg_type, data = q.get(timeout=0.1)
            except queue.Empty:
                await asyncio.sleep(0.01)
                continue
                
            if msg_type == "error":
                raise data
            if msg_type == "done":
                break
            
            chunk = data
            # Some providers include usage in streaming
            if hasattr(chunk, "usage") and chunk.usage:
                prompt_tokens = getattr(chunk.usage, "prompt_tokens", 0)
                completion_tokens = getattr(chunk.usage, "completion_tokens", 0)
                total_tokens = getattr(chunk.usage, "total_tokens", 0)

            if chunk.choices and chunk.choices[0].delta:
                delta = chunk.choices[0].delta
                content = getattr(delta, 'content', None) or ""
                if content:
                    yield StreamChunk(content=content)

        thread.join()
        latency_ms = int((time.monotonic() - start) * 1000)
        yield StreamChunk(
            content="",
            is_final=True,
            model=model,
            provider=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
        )


class OllamaProvider(BaseProvider):
    """Ollama local LLM provider.
    
    Ollama runs models locally, providing free inference at the cost of:
    - Lower throughput (usually 1 concurrent request)
    - Slower first request (model loading)
    - Hardware requirements (GPU recommended)
    
    This provider is always tried LAST in the priority chain.
    """
    name = "ollama"

    def __init__(
        self, 
        base_url: str, 
        enabled: bool, 
        model_tiers: dict[str, str],
        context_lengths: dict[str, int],
        max_concurrent: int = 1,
    ) -> None:
        self._base_url = base_url.rstrip('/')
        self._enabled = enabled
        self._model_tiers = model_tiers  # {"fast": "hf.co/mradermacher/Nanbeige4.1-3B-GGUF:Q8_0", ...}
        self._context_lengths = context_lengths  # {"fast": 262144, ...}
        self._max_concurrent = max_concurrent
        self._available_models: Optional[set] = None  # Cache of available models
        self._current_requests = 0  # Track concurrent requests
        # Build reverse map: model_name -> tier for context length lookup
        self._model_to_tier = {v: k for k, v in model_tiers.items()}
        # Shared httpx client — avoids FD exhaustion under load (GAP: LOW-1)
        self._http_client: Optional[httpx.AsyncClient] = None

    def _get_http_client(self) -> httpx.AsyncClient:
        """Return a reusable httpx client.  Created lazily on first call."""
        import httpx as _httpx
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = _httpx.AsyncClient(timeout=300.0)
        return self._http_client

    def is_configured(self) -> bool:
        """Check if Ollama is enabled."""
        return self._enabled

    def get_model_for_tier(self, tier: str) -> Optional[str]:
        """Get the configured model for a tier."""
        return self._model_tiers.get(tier)
    
    def get_context_length(self, tier: str) -> int:
        """Get the context length for a tier."""
        return self._context_lengths.get(tier, 4096)

    def _get_num_ctx_for_model(self, model: str) -> int:
        """Get the num_ctx value for a model by reverse-mapping to its tier."""
        tier = self._model_to_tier.get(model)
        if tier:
            return self._context_lengths.get(tier, 4096)
        return 4096  # Safe default

    async def _check_server_available(self) -> bool:
        """Quick health check for Ollama server."""
        try:
            client = self._get_http_client()
            response = await client.get(f"{self._base_url}/api/tags", timeout=5.0)
            return response.status_code == 200
        except Exception:
            return False

    async def _get_available_models(self) -> set:
        """Get list of available models from Ollama."""
        if self._available_models is not None:
            return self._available_models
        
        try:
            client = self._get_http_client()
            response = await client.get(f"{self._base_url}/api/tags", timeout=10.0)
            if response.status_code == 200:
                data = response.json()
                self._available_models = {m['name'] for m in data.get('models', [])}
                return self._available_models
        except Exception as e:
            logger.warning(f"Failed to get Ollama models: {e}")
        return set()

    async def _check_model_available(self, model: str) -> bool:
        """Check if a specific model is available in Ollama."""
        available = await self._get_available_models()
        # Check exact match or base name match (mistral:7b vs mistral:7b-instruct)
        if model in available:
            return True
        # Check if base name matches (e.g., "mistral:7b" matches "mistral:7b-instruct-v0.2")
        base_name = model.split(':')[0] if ':' in model else model
        return any(m.startswith(base_name) for m in available)

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count from text (rough approximation)."""
        # Rough estimate: ~4 characters per token for English
        return len(text) // 4

    async def generate(
        self,
        model: str,
        messages: List[LLMMessage],
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        if not self.is_configured():
            raise LLMProviderUnavailable("Ollama not enabled (set USE_OLLAMA=true)")
        
        # Check if server is available
        if not await self._check_server_available():
            raise LLMProviderUnavailable("Ollama server not available")
        
        # Check if model is available
        if not await self._check_model_available(model):
            raise LLMProviderUnavailable(
                f"Ollama model '{model}' not found. Pull it with: ollama pull {model}"
            )
        
        # Check concurrency limit
        if self._current_requests >= self._max_concurrent:
            raise LLMProviderUnavailable(
                f"Ollama at max concurrent requests ({self._max_concurrent})"
            )
        
        self._current_requests += 1
        
        try:
            # Convert messages to Ollama format
            ollama_messages = [{"role": m.role, "content": m.content} for m in messages]
            
            # Ollama is free — use context window for num_predict instead of
            # the hard-coded max_tokens ceiling designed for paid providers.
            # Ollama automatically caps output at (num_ctx - prompt_tokens).
            ctx_window = self._get_num_ctx_for_model(model)
            effective_max_tokens = max(max_tokens, ctx_window)
            
            start = time.monotonic()
            
            client = self._get_http_client()
            response = await client.post(
                f"{self._base_url}/api/chat",
                json={
                    "model": model,
                    "messages": ollama_messages,
                    "stream": False,
                    "keep_alive": "5m",  # Auto-unload after 5min idle to free GPU VRAM
                    "options": {
                        "temperature": temperature,
                        "num_predict": effective_max_tokens,
                        "num_ctx": ctx_window,
                    },
                },
            )
                
            if response.status_code != 200:
                raise LLMError(f"Ollama error: {response.status_code} - {response.text}")
                
            data = response.json()
            
            content = data.get("message", {}).get("content", "")
            latency_ms = int((time.monotonic() - start) * 1000)
            
            # Ollama may or may not provide token counts
            prompt_tokens = data.get("prompt_eval_count", 0)
            completion_tokens = data.get("eval_count", 0)
            
            # Estimate if not provided
            if prompt_tokens == 0:
                prompt_tokens = sum(self._estimate_tokens(m.content) for m in messages)
            if completion_tokens == 0:
                completion_tokens = self._estimate_tokens(content)
            
            return LLMResponse(
                content=content,
                model=model,
                provider=self.name,
                latency_ms=latency_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                cost_usd=0.0,  # Ollama is free
                finish_reason="stop",
            )
        finally:
            self._current_requests -= 1

    async def generate_stream(
        self,
        model: str,
        messages: List[LLMMessage],
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[StreamChunk]:
        if not self.is_configured():
            raise LLMProviderUnavailable("Ollama not enabled (set USE_OLLAMA=true)")
        
        # Check if server is available
        if not await self._check_server_available():
            raise LLMProviderUnavailable("Ollama server not available")
        
        # Check if model is available
        if not await self._check_model_available(model):
            raise LLMProviderUnavailable(
                f"Ollama model '{model}' not found. Pull it with: ollama pull {model}"
            )
        
        # Check concurrency limit
        if self._current_requests >= self._max_concurrent:
            raise LLMProviderUnavailable(
                f"Ollama at max concurrent requests ({self._max_concurrent})"
            )
        
        self._current_requests += 1
        
        try:
            # Convert messages to Ollama format
            ollama_messages = [{"role": m.role, "content": m.content} for m in messages]
            
            # Ollama is free — use context window for num_predict instead of
            # the hard-coded max_tokens ceiling designed for paid providers.
            ctx_window = self._get_num_ctx_for_model(model)
            effective_max_tokens = max(max_tokens, ctx_window)
            
            start = time.monotonic()
            prompt_tokens = sum(self._estimate_tokens(m.content) for m in messages)
            completion_tokens = 0
            
            client = self._get_http_client()
            async with client.stream(
                "POST",
                f"{self._base_url}/api/chat",
                json={
                    "model": model,
                    "messages": ollama_messages,
                    "stream": True,
                    "keep_alive": "5m",  # Auto-unload after 5min idle to free GPU VRAM
                    "options": {
                        "temperature": temperature,
                        "num_predict": effective_max_tokens,
                        "num_ctx": ctx_window,
                    },
                },
            ) as response:
                if response.status_code != 200:
                    raise LLMError(f"Ollama error: {response.status_code}")
                
                import json
                async for line in response.aiter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            content = data.get("message", {}).get("content", "")
                            if content:
                                completion_tokens += self._estimate_tokens(content)
                                yield StreamChunk(content=content)
                            
                            # Check for completion
                            if data.get("done", False):
                                # Update token counts if provided
                                if "prompt_eval_count" in data:
                                    prompt_tokens = data["prompt_eval_count"]
                                if "eval_count" in data:
                                    completion_tokens = data["eval_count"]
                                break
                        except json.JSONDecodeError:
                            continue
            
            latency_ms = int((time.monotonic() - start) * 1000)
            yield StreamChunk(
                content="",
                is_final=True,
                model=model,
                provider=self.name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                latency_ms=latency_ms,
            )
        finally:
            self._current_requests -= 1


class LLMService:
    """LLM service with tier-based model selection and provider failover.
    
    Tiers:
    - fast: Quick responses, lowest cost (glm-4.7-flash is FREE)
    - reasoning: Models with reasoning capabilities
    - quality: Highest quality output
    
    Agents request a tier (e.g., "fast", "reasoning", "quality") and the service
    selects the appropriate model based on provider priority and availability.
    
    Model format options:
    - "fast" / "reasoning" / "quality" - use provider priority
    - "glm:fast" / "claude:reasoning" - force specific provider
    - "claude-sonnet-4" - explicit model name (bypass tier system)
    """

    # Model tiers by provider
    MODEL_TIERS = {
        "glm": {
            "fast": "glm-4.7-flash",      # FREE!
            "reasoning": "glm-4.7",        # Has reasoning_content
            "quality": "glm-5",            # Best GLM model (2025)
        },
        "claude": {
            "fast": "claude-haiku-4-5",         # Fast, cheap
            "reasoning": "claude-sonnet-4-6",   # Balanced
            "quality": "claude-opus-4-6",        # Best quality
        },
        "openai": {
            "fast": "gpt-4.1-mini",      # $0.40/$1.60 – newer, more capable than gpt-4o-mini
            "reasoning": "o4-mini",       # $1.10/$4.40 – latest optimised reasoning
            "quality": "gpt-4.1",         # $2.00/$8.00 – current flagship (vs gpt-4o $2.50/$10)
        },
        # Ollama tiers are populated from settings
        "ollama": {},
    }

    def __init__(self) -> None:
        self._providers = {
            "glm": ZaiProvider(api_key_attr="z_ai_api_key", base_url=settings.z_ai_base_url),
            "claude": AnthropicProvider(api_key_attr="anthropic_api_key"),
            "openai": OpenAIProvider(api_key_attr="openai_api_key"),
            "ollama": OllamaProvider(
                base_url=settings.ollama_base_url,
                enabled=settings.use_ollama,
                model_tiers=settings.ollama_model_tiers_dict,
                context_lengths=settings.ollama_context_lengths_dict,
                max_concurrent=settings.ollama_max_concurrent,
            ),
        }
        
        # Update MODEL_TIERS with Ollama config from settings
        if settings.use_ollama:
            self.MODEL_TIERS["ollama"] = settings.ollama_model_tiers_dict

    def _provider_for_model(self, model: str) -> tuple[BaseProvider, str]:
        """Get provider for an explicit model name.
        
        Returns (provider, provider_name) tuple.
        """
        lowered = model.lower()
        if lowered.startswith("glm"):
            return self._providers["glm"], "glm"
        if lowered.startswith("claude"):
            return self._providers["claude"], "claude"
        if lowered.startswith("gpt") or lowered.startswith("o1"):
            return self._providers["openai"], "openai"
        # Check for Ollama model format (contains ":" like "mistral:7b")
        if ":" in lowered and "ollama" in self._providers:
            return self._providers["ollama"], "ollama"
        return self._providers["openai"], "openai"

    def _parse_model_spec(self, model_spec: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Parse model specification.
        
        Returns (tier, forced_provider, explicit_model) tuple.
        Only one of these will be set.
        
        Examples:
        - "fast" -> ("fast", None, None)
        - "claude:reasoning" -> ("reasoning", "claude", None)
        - "claude-sonnet-4" -> (None, None, "claude-sonnet-4")
        """
        spec = model_spec.strip().lower()
        
        # Check if it's a tier-only spec
        if spec in ("fast", "reasoning", "quality"):
            return spec, None, None
        
        # Check if it's provider:tier format
        if ":" in spec:
            parts = spec.split(":", 1)
            if len(parts) == 2 and parts[0] in self._providers and parts[1] in ("fast", "reasoning", "quality"):
                return parts[1], parts[0], None
        
        # It's an explicit model name
        return None, None, model_spec

    def _get_provider_priority(self) -> List[str]:
        """Get provider priority list from config."""
        if settings.llm_force_provider:
            return [settings.llm_force_provider]
        return settings.llm_provider_priority_list

    def _resolve_model_sequence(self, model_spec: Optional[str]) -> List[tuple[str, BaseProvider]]:
        """Resolve model spec to ordered list of (model_name, provider) to try.
        
        Returns list of tuples for failover support.
        """
        if not model_spec:
            model_spec = "fast"  # Default tier
            
        tier, forced_provider, explicit_model = self._parse_model_spec(model_spec)
        
        # Explicit model - just return that one
        if explicit_model:
            provider, _ = self._provider_for_model(explicit_model)
            return [(explicit_model, provider)]
        
        # Tier-based resolution
        sequence = []
        providers_to_try = [forced_provider] if forced_provider else self._get_provider_priority()
        
        for provider_name in providers_to_try:
            if provider_name not in self._providers:
                continue
            provider = self._providers[provider_name]
            if provider_name in self.MODEL_TIERS and tier in self.MODEL_TIERS[provider_name]:
                model = self.MODEL_TIERS[provider_name][tier]
                sequence.append((model, provider))
        
        return sequence

    async def generate(
        self,
        messages: Iterable[LLMMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generate LLM response with tier-based selection and failover.
        
        Args:
            messages: Input messages
            model: Model specification - can be:
                - Tier: "fast", "reasoning", "quality"
                - Provider:tier: "glm:fast", "claude:reasoning"
                - Explicit model: "claude-sonnet-4-6"
            temperature: Generation temperature
            max_tokens: Maximum tokens to generate
        """
        attempts: List[str] = []
        message_list = list(messages)
        
        model_sequence = self._resolve_model_sequence(model)
        
        if not model_sequence:
            raise LLMError(f"No providers available for model spec: {model}")

        for candidate_model, provider in model_sequence:
            if not provider.is_configured():
                attempts.append(f"{candidate_model} ({provider.name} not configured)")
                continue

            try:
                return await provider.generate(
                    model=candidate_model,
                    messages=message_list,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except Exception as exc:  # noqa: BLE001 - capture provider error for fallback
                logger.warning(
                    "LLM provider failed, falling back",
                    extra={"model": candidate_model, "provider": provider.name, "error": str(exc)},
                )
                attempts.append(f"{candidate_model} ({provider.name} failed: {exc})")

        raise LLMError("All LLM providers failed: " + " | ".join(attempts))

    async def generate_stream(
        self,
        messages: Iterable[LLMMessage],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[StreamChunk]:
        """Stream LLM response with tier-based selection and failover.
        
        Args:
            messages: Input messages
            model: Model specification - can be:
                - Tier: "fast", "reasoning", "quality"
                - Provider:tier: "glm:fast", "claude:reasoning"
                - Explicit model: "claude-sonnet-4-6"
            temperature: Generation temperature
            max_tokens: Maximum tokens to generate
        """
        attempts: List[str] = []
        message_list = list(messages)
        
        model_sequence = self._resolve_model_sequence(model)
        
        if not model_sequence:
            raise LLMError(f"No providers available for model spec: {model}")

        for candidate_model, provider in model_sequence:
            if not provider.is_configured():
                attempts.append(f"{candidate_model} ({provider.name} not configured)")
                continue

            try:
                async for chunk in provider.generate_stream(
                    model=candidate_model,
                    messages=message_list,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    yield chunk
                return  # Success, exit after completing stream
            except Exception as exc:  # noqa: BLE001 - capture provider error for fallback
                logger.warning(
                    "LLM streaming provider failed, falling back",
                    extra={"model": candidate_model, "provider": provider.name, "error": str(exc)},
                )
                attempts.append(f"{candidate_model} ({provider.name} failed: {exc})")

        raise LLMError("All LLM providers failed: " + " | ".join(attempts))


llm_service = LLMService()