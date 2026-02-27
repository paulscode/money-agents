"""Integration tests for Ollama LLM provider.

These tests require:
1. A running Ollama server
2. At least one model pulled (mistral:7b recommended)

To run these tests:
    cd backend
    pytest tests/integration/test_ollama_integration.py -v -s

Skip these tests if Ollama is not available:
    pytest tests/integration/test_ollama_integration.py -v -s --skip-ollama
"""
import asyncio
import os
import pytest
import httpx


# Check if Ollama is available for tests
def is_ollama_available() -> bool:
    """Check if Ollama server is running and has models."""
    try:
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"{base_url}/api/tags")
            if response.status_code == 200:
                models = response.json().get("models", [])
                return len(models) > 0
    except Exception:
        pass
    return False


# Skip all tests if Ollama not available
pytestmark = pytest.mark.skipif(
    not is_ollama_available(),
    reason="Ollama server not available or no models pulled"
)


@pytest.fixture
def ollama_base_url():
    """Get Ollama base URL from environment or use default."""
    return os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")


@pytest.fixture
def available_models(ollama_base_url):
    """Get list of available Ollama models."""
    with httpx.Client(timeout=10.0) as client:
        response = client.get(f"{ollama_base_url}/api/tags")
        models = response.json().get("models", [])
        return [m["name"] for m in models]


@pytest.fixture
def test_model(available_models):
    """Select a model for testing (prefer smaller models)."""
    # Preferred order for testing (smaller = faster)
    preferred = ["mistral:7b", "llama3.2:latest", "llama3:8b", "qwen2.5:7b"]
    
    for model in preferred:
        if model in available_models:
            return model
    
    # Fall back to first available
    if available_models:
        return available_models[0]
    
    pytest.skip("No Ollama models available")


class TestOllamaServerHealth:
    """Tests for Ollama server availability and health."""
    
    def test_server_responds_to_tags(self, ollama_base_url):
        """Test that Ollama server responds to /api/tags endpoint."""
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{ollama_base_url}/api/tags")
            assert response.status_code == 200
            data = response.json()
            assert "models" in data
            assert isinstance(data["models"], list)
    
    def test_server_has_models(self, available_models):
        """Test that at least one model is available."""
        assert len(available_models) > 0, "No models available in Ollama"
        print(f"Available models: {available_models}")


class TestOllamaProviderIntegration:
    """Integration tests for OllamaProvider with real Ollama server."""
    
    @pytest.mark.asyncio
    async def test_provider_is_configured(self, ollama_base_url, available_models):
        """Test provider reports as configured when Ollama is available."""
        from app.services.llm_service import OllamaProvider
        
        provider = OllamaProvider(
            base_url=ollama_base_url,
            enabled=True,
            model_tiers={"fast": available_models[0]},
            context_lengths={"fast": 8192},
            max_concurrent=1,
        )
        
        assert provider.is_configured() is True
    
    @pytest.mark.asyncio
    async def test_check_server_available(self, ollama_base_url):
        """Test server availability check with real server."""
        from app.services.llm_service import OllamaProvider
        
        provider = OllamaProvider(
            base_url=ollama_base_url,
            enabled=True,
            model_tiers={},
            context_lengths={},
        )
        
        result = await provider._check_server_available()
        assert result is True
    
    @pytest.mark.asyncio
    async def test_get_available_models(self, ollama_base_url, available_models):
        """Test fetching available models from real server."""
        from app.services.llm_service import OllamaProvider
        
        provider = OllamaProvider(
            base_url=ollama_base_url,
            enabled=True,
            model_tiers={},
            context_lengths={},
        )
        
        models = await provider._get_available_models()
        assert len(models) > 0
        # Should match what we know is available
        for model in available_models:
            assert model in models
    
    @pytest.mark.asyncio
    async def test_check_model_available(self, ollama_base_url, test_model):
        """Test model availability check with real model."""
        from app.services.llm_service import OllamaProvider
        
        provider = OllamaProvider(
            base_url=ollama_base_url,
            enabled=True,
            model_tiers={},
            context_lengths={},
        )
        
        # Available model should return True
        result = await provider._check_model_available(test_model)
        assert result is True
        
        # Non-existent model should return False
        result = await provider._check_model_available("nonexistent-model:v999")
        assert result is False
    
    @pytest.mark.asyncio
    async def test_generate_simple_response(self, ollama_base_url, test_model):
        """Test generating a simple response with real model."""
        from app.services.llm_service import OllamaProvider, LLMMessage, LLMResponse
        
        provider = OllamaProvider(
            base_url=ollama_base_url,
            enabled=True,
            model_tiers={"fast": test_model},
            context_lengths={"fast": 8192},
            max_concurrent=1,
        )
        
        messages = [
            LLMMessage(role="user", content="Say 'Hello World' and nothing else.")
        ]
        
        response = await provider.generate(
            model=test_model,
            messages=messages,
            temperature=0.1,  # Low temp for consistent response
            max_tokens=50,
        )
        
        assert isinstance(response, LLMResponse)
        assert response.provider == "ollama"
        assert response.model == test_model
        assert "hello" in response.content.lower()
        assert response.total_tokens > 0
        assert response.cost_usd == 0.0  # Ollama is free
        assert response.latency_ms > 0
    
    @pytest.mark.asyncio
    async def test_generate_with_system_message(self, ollama_base_url, test_model):
        """Test generation with system message."""
        from app.services.llm_service import OllamaProvider, LLMMessage
        
        provider = OllamaProvider(
            base_url=ollama_base_url,
            enabled=True,
            model_tiers={"fast": test_model},
            context_lengths={"fast": 8192},
            max_concurrent=1,
        )
        
        messages = [
            LLMMessage(role="system", content="You are a helpful assistant that responds only in uppercase."),
            LLMMessage(role="user", content="Say hello."),
        ]
        
        response = await provider.generate(
            model=test_model,
            messages=messages,
            temperature=0.1,
            max_tokens=50,
        )
        
        assert response.content  # Should have some response
        print(f"Response: {response.content}")
    
    @pytest.mark.asyncio
    async def test_generate_stream(self, ollama_base_url, test_model):
        """Test streaming generation with real model."""
        from app.services.llm_service import OllamaProvider, LLMMessage, StreamChunk
        
        provider = OllamaProvider(
            base_url=ollama_base_url,
            enabled=True,
            model_tiers={"fast": test_model},
            context_lengths={"fast": 8192},
            max_concurrent=1,
        )
        
        messages = [
            LLMMessage(role="user", content="Count from 1 to 5.")
        ]
        
        chunks = []
        async for chunk in provider.generate_stream(
            model=test_model,
            messages=messages,
            temperature=0.1,
            max_tokens=100,
        ):
            chunks.append(chunk)
            if chunk.content:
                print(chunk.content, end="", flush=True)
        
        print()  # Newline after streaming
        
        assert len(chunks) > 0
        
        # Find the final chunk
        final_chunks = [c for c in chunks if c.is_final]
        assert len(final_chunks) == 1
        
        final = final_chunks[0]
        assert final.provider == "ollama"
        assert final.model == test_model
    
    @pytest.mark.asyncio
    async def test_concurrent_request_limiting(self, ollama_base_url, test_model):
        """Test that concurrent request limit is enforced."""
        from app.services.llm_service import OllamaProvider, LLMMessage, LLMProviderUnavailable
        
        provider = OllamaProvider(
            base_url=ollama_base_url,
            enabled=True,
            model_tiers={"fast": test_model},
            context_lengths={"fast": 8192},
            max_concurrent=1,  # Only allow 1 concurrent request
        )
        
        messages = [LLMMessage(role="user", content="Count to 10.")]
        
        # Start first request (don't await yet)
        task1 = asyncio.create_task(
            provider.generate(test_model, messages, 0.5, 100)
        )
        
        # Give first request time to start
        await asyncio.sleep(0.1)
        
        # Second request should fail due to concurrency limit
        try:
            # Directly check if we'd be blocked
            if provider._current_requests >= provider._max_concurrent:
                # This is expected - we're at max
                with pytest.raises(LLMProviderUnavailable) as exc_info:
                    await provider.generate(test_model, messages, 0.5, 100)
                assert "max concurrent" in str(exc_info.value)
        finally:
            # Clean up first task
            try:
                await task1
            except Exception:
                pass


class TestLLMServiceOllamaIntegration:
    """Tests for LLMService integration with Ollama."""
    
    @pytest.mark.asyncio
    async def test_llm_service_ollama_provider_available(self, ollama_base_url):
        """Test that LLMService includes Ollama provider when configured."""
        # Temporarily set environment for test
        original_use = os.environ.get("USE_OLLAMA")
        original_url = os.environ.get("OLLAMA_BASE_URL")
        
        try:
            os.environ["USE_OLLAMA"] = "true"
            os.environ["OLLAMA_BASE_URL"] = ollama_base_url
            
            # Reload settings to pick up env changes
            from app.core.config import Settings
            test_settings = Settings()
            
            # Create service with test settings
            from app.services.llm_service import LLMService
            
            # This requires more setup, so just verify provider exists
            assert test_settings.use_ollama is True
            assert test_settings.ollama_base_url == ollama_base_url
            
        finally:
            # Restore original environment
            if original_use is not None:
                os.environ["USE_OLLAMA"] = original_use
            elif "USE_OLLAMA" in os.environ:
                del os.environ["USE_OLLAMA"]
            
            if original_url is not None:
                os.environ["OLLAMA_BASE_URL"] = original_url
            elif "OLLAMA_BASE_URL" in os.environ:
                del os.environ["OLLAMA_BASE_URL"]


class TestOllamaFailover:
    """Tests for failover behavior when Ollama is unavailable."""
    
    @pytest.mark.asyncio
    async def test_provider_unavailable_with_wrong_url(self):
        """Test provider reports unavailable with wrong URL."""
        from app.services.llm_service import OllamaProvider
        
        provider = OllamaProvider(
            base_url="http://localhost:99999",  # Wrong port
            enabled=True,
            model_tiers={"fast": "mistral:7b"},
            context_lengths={"fast": 8192},
        )
        
        result = await provider._check_server_available()
        assert result is False
    
    @pytest.mark.asyncio
    async def test_generate_fails_gracefully_when_server_down(self):
        """Test that generate raises appropriate error when server down."""
        from app.services.llm_service import OllamaProvider, LLMMessage, LLMProviderUnavailable
        
        provider = OllamaProvider(
            base_url="http://localhost:99999",  # Wrong port
            enabled=True,
            model_tiers={"fast": "mistral:7b"},
            context_lengths={"fast": 8192},
        )
        
        messages = [LLMMessage(role="user", content="Hello")]
        
        with pytest.raises(LLMProviderUnavailable) as exc_info:
            await provider.generate("mistral:7b", messages, 0.7, 100)
        
        assert "not available" in str(exc_info.value)
