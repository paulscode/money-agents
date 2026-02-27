"""Unit tests for OllamaProvider class."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.services.llm_service import (
    OllamaProvider,
    LLMProviderUnavailable,
    LLMError,
    LLMMessage,
    LLMResponse,
    StreamChunk,
)


class TestOllamaProviderConfiguration:
    """Tests for OllamaProvider configuration and initialization."""
    
    def test_init_with_all_settings(self):
        """Test initialization with all settings provided."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={"fast": "mistral:7b", "reasoning": "llama3:8b", "quality": "qwen2.5:14b"},
            context_lengths={"fast": 8192, "reasoning": 32768, "quality": 32768},
            max_concurrent=2,
        )
        
        assert provider.name == "ollama"
        assert provider._base_url == "http://localhost:11434"
        assert provider._enabled is True
        assert provider._max_concurrent == 2
        assert provider.get_model_for_tier("fast") == "mistral:7b"
        assert provider.get_model_for_tier("reasoning") == "llama3:8b"
        assert provider.get_model_for_tier("quality") == "qwen2.5:14b"
    
    def test_init_strips_trailing_slash(self):
        """Test that trailing slash is stripped from base_url."""
        provider = OllamaProvider(
            base_url="http://localhost:11434/",
            enabled=True,
            model_tiers={},
            context_lengths={},
        )
        assert provider._base_url == "http://localhost:11434"
    
    def test_is_configured_when_enabled(self):
        """Test is_configured returns True when enabled."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={},
            context_lengths={},
        )
        assert provider.is_configured() is True
    
    def test_is_configured_when_disabled(self):
        """Test is_configured returns False when disabled."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=False,
            model_tiers={},
            context_lengths={},
        )
        assert provider.is_configured() is False
    
    def test_get_model_for_tier_returns_configured_model(self):
        """Test tier lookup returns configured model."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={"fast": "mistral:7b", "quality": "qwen2.5:14b"},
            context_lengths={},
        )
        assert provider.get_model_for_tier("fast") == "mistral:7b"
        assert provider.get_model_for_tier("quality") == "qwen2.5:14b"
        assert provider.get_model_for_tier("unknown") is None
    
    def test_get_context_length_returns_configured_or_default(self):
        """Test context length lookup with fallback."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={},
            context_lengths={"fast": 8192, "quality": 32768},
        )
        assert provider.get_context_length("fast") == 8192
        assert provider.get_context_length("quality") == 32768
        assert provider.get_context_length("unknown") == 4096  # Default fallback
    
    def test_estimate_tokens_approximation(self):
        """Test token estimation is reasonable."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={},
            context_lengths={},
        )
        # ~4 chars per token
        assert provider._estimate_tokens("hello world") == 2  # 11 chars / 4 = 2
        assert provider._estimate_tokens("a" * 100) == 25  # 100 / 4 = 25
        assert provider._estimate_tokens("") == 0


class TestOllamaProviderServerChecks:
    """Tests for Ollama server availability checks."""
    
    @pytest.mark.asyncio
    async def test_check_server_available_success(self):
        """Test server availability check when server responds."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={},
            context_lengths={},
        )
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(provider, "_get_http_client", return_value=mock_client):
            result = await provider._check_server_available()
            assert result is True
            mock_client.get.assert_called_once_with("http://localhost:11434/api/tags", timeout=5.0)
    
    @pytest.mark.asyncio
    async def test_check_server_available_failure(self):
        """Test server availability check when server is down."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={},
            context_lengths={},
        )
        
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))

        with patch.object(provider, "_get_http_client", return_value=mock_client):
            result = await provider._check_server_available()
            assert result is False
    
    @pytest.mark.asyncio
    async def test_get_available_models_success(self):
        """Test fetching available models from Ollama."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={},
            context_lengths={},
        )
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {"name": "mistral:7b"},
                {"name": "llama3:8b"},
                {"name": "qwen2.5:14b"},
            ]
        }
        
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch.object(provider, "_get_http_client", return_value=mock_client):
            result = await provider._get_available_models()
            assert result == {"mistral:7b", "llama3:8b", "qwen2.5:14b"}
    
    @pytest.mark.asyncio
    async def test_check_model_available_exact_match(self):
        """Test model availability check with exact match."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={},
            context_lengths={},
        )
        provider._available_models = {"mistral:7b", "llama3:8b"}
        
        result = await provider._check_model_available("mistral:7b")
        assert result is True
    
    @pytest.mark.asyncio
    async def test_check_model_available_prefix_match(self):
        """Test model availability check with prefix match."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={},
            context_lengths={},
        )
        provider._available_models = {"mistral:7b-instruct-v0.2"}
        
        # "mistral:7b" should match "mistral:7b-instruct-v0.2"
        result = await provider._check_model_available("mistral:7b")
        assert result is True
    
    @pytest.mark.asyncio
    async def test_check_model_available_not_found(self):
        """Test model availability check when model not found."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={},
            context_lengths={},
        )
        provider._available_models = {"mistral:7b"}
        
        result = await provider._check_model_available("llama3:8b")
        assert result is False


class TestOllamaProviderGenerate:
    """Tests for OllamaProvider.generate() method."""
    
    @pytest.mark.asyncio
    async def test_generate_not_enabled_raises(self):
        """Test generate raises when Ollama not enabled."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=False,
            model_tiers={},
            context_lengths={},
        )
        
        messages = [LLMMessage(role="user", content="Hello")]
        
        with pytest.raises(LLMProviderUnavailable) as exc_info:
            await provider.generate("mistral:7b", messages, 0.7, 1000)
        
        assert "not enabled" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_generate_server_unavailable_raises(self):
        """Test generate raises when Ollama server is down."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={},
            context_lengths={},
        )
        
        with patch.object(provider, "_check_server_available", return_value=False):
            messages = [LLMMessage(role="user", content="Hello")]
            
            with pytest.raises(LLMProviderUnavailable) as exc_info:
                await provider.generate("mistral:7b", messages, 0.7, 1000)
            
            assert "not available" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_generate_model_not_found_raises(self):
        """Test generate raises when model not found in Ollama."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={},
            context_lengths={},
        )
        
        with patch.object(provider, "_check_server_available", return_value=True):
            with patch.object(provider, "_check_model_available", return_value=False):
                messages = [LLMMessage(role="user", content="Hello")]
                
                with pytest.raises(LLMProviderUnavailable) as exc_info:
                    await provider.generate("nonexistent:model", messages, 0.7, 1000)
                
                assert "not found" in str(exc_info.value)
                assert "ollama pull" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_generate_max_concurrent_reached_raises(self):
        """Test generate raises when max concurrent requests reached."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={},
            context_lengths={},
            max_concurrent=1,
        )
        provider._current_requests = 1  # Already at max
        
        with patch.object(provider, "_check_server_available", return_value=True):
            with patch.object(provider, "_check_model_available", return_value=True):
                messages = [LLMMessage(role="user", content="Hello")]
                
                with pytest.raises(LLMProviderUnavailable) as exc_info:
                    await provider.generate("mistral:7b", messages, 0.7, 1000)
                
                assert "max concurrent" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_generate_success(self):
        """Test successful generation."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={},
            context_lengths={},
            max_concurrent=2,
        )
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": "Hello! How can I help you?"},
            "prompt_eval_count": 10,
            "eval_count": 8,
        }
        
        with patch.object(provider, "_check_server_available", return_value=True):
            with patch.object(provider, "_check_model_available", return_value=True):
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_response)

                with patch.object(provider, "_get_http_client", return_value=mock_client):
                    messages = [LLMMessage(role="user", content="Hi!")]
                    result = await provider.generate("mistral:7b", messages, 0.7, 1000)
                    
                    assert isinstance(result, LLMResponse)
                    assert result.content == "Hello! How can I help you?"
                    assert result.model == "mistral:7b"
                    assert result.provider == "ollama"
                    assert result.prompt_tokens == 10
                    assert result.completion_tokens == 8
                    assert result.total_tokens == 18
                    assert result.cost_usd == 0.0
    
    @pytest.mark.asyncio
    async def test_generate_concurrent_tracking(self):
        """Test that concurrent request counter is properly managed."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={},
            context_lengths={},
            max_concurrent=2,
        )
        
        assert provider._current_requests == 0
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "message": {"content": "Response"},
            "prompt_eval_count": 5,
            "eval_count": 5,
        }
        
        with patch.object(provider, "_check_server_available", return_value=True):
            with patch.object(provider, "_check_model_available", return_value=True):
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_response)

                with patch.object(provider, "_get_http_client", return_value=mock_client):
                    messages = [LLMMessage(role="user", content="Test")]
                    await provider.generate("mistral:7b", messages, 0.7, 1000)
                    
                    # Counter should be back to 0 after completion
                    assert provider._current_requests == 0
    
    @pytest.mark.asyncio
    async def test_generate_error_resets_counter(self):
        """Test that counter is reset even on error."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={},
            context_lengths={},
            max_concurrent=2,
        )
        
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal server error"
        
        with patch.object(provider, "_check_server_available", return_value=True):
            with patch.object(provider, "_check_model_available", return_value=True):
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_response)

                with patch.object(provider, "_get_http_client", return_value=mock_client):
                    messages = [LLMMessage(role="user", content="Test")]
                    
                    with pytest.raises(LLMError):
                        await provider.generate("mistral:7b", messages, 0.7, 1000)
                    
                    # Counter should still be reset
                    assert provider._current_requests == 0


class TestOllamaProviderStreaming:
    """Tests for OllamaProvider.generate_stream() method."""
    
    @pytest.mark.asyncio
    async def test_generate_stream_not_enabled_raises(self):
        """Test streaming raises when Ollama not enabled."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=False,
            model_tiers={},
            context_lengths={},
        )
        
        messages = [LLMMessage(role="user", content="Hello")]
        
        with pytest.raises(LLMProviderUnavailable):
            async for _ in provider.generate_stream("mistral:7b", messages, 0.7, 1000):
                pass
    
    @pytest.mark.asyncio
    async def test_generate_stream_server_unavailable_raises(self):
        """Test streaming raises when server is down."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            enabled=True,
            model_tiers={},
            context_lengths={},
        )
        
        with patch.object(provider, "_check_server_available", return_value=False):
            messages = [LLMMessage(role="user", content="Hello")]
            
            with pytest.raises(LLMProviderUnavailable):
                async for _ in provider.generate_stream("mistral:7b", messages, 0.7, 1000):
                    pass


class TestOllamaUsageServiceIntegration:
    """Tests for Ollama integration with usage_service pricing."""
    
    def test_ollama_models_have_free_pricing(self):
        """Test that Ollama models are priced at $0."""
        from app.services.usage_service import get_model_pricing
        
        # Common Ollama model names
        ollama_models = [
            "mistral:7b",
            "llama3:8b",
            "qwen2.5:14b",
            "mistral-nemo:12b",
            "deepseek-r1:32b",
        ]
        
        for model in ollama_models:
            input_price, output_price = get_model_pricing(model)
            assert input_price == 0.0, f"Expected free input for {model}"
            assert output_price == 0.0, f"Expected free output for {model}"
    
    def test_ollama_model_detection_by_colon(self):
        """Test that models with colon are detected as Ollama."""
        from app.services.usage_service import get_model_pricing
        
        # Any model with colon followed by tag should be free (Ollama format)
        custom_ollama = "my-custom-model:v1.0"
        input_price, output_price = get_model_pricing(custom_ollama)
        assert input_price == 0.0
        assert output_price == 0.0
    
    def test_cost_calculation_for_ollama(self):
        """Test that cost calculation returns 0 for Ollama models."""
        from app.services.llm_service import calculate_cost
        
        cost = calculate_cost("mistral:7b", prompt_tokens=1000, completion_tokens=500)
        assert cost == 0.0
        
        cost = calculate_cost("qwen2.5:14b", prompt_tokens=10000, completion_tokens=5000)
        assert cost == 0.0
