"""Unit tests for Context Compression Service."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.context_compression_service import (
    TokenCounter,
    QueryIntent,
    ContextAnalyzer,
    ContextCompressor,
    ContextBudgetManager,
    ContextBudget,
    CompressionResult,
    QueryAnalysis,
)


class TestTokenCounter:
    """Test suite for TokenCounter."""
    
    def test_count_single_word(self):
        """Test counting tokens in a single word."""
        count = TokenCounter.count_tokens("Hello")
        assert count >= 1
    
    def test_count_multiple_words(self):
        """Test counting tokens in multiple words."""
        count = TokenCounter.count_tokens("Hello world, how are you today?")
        assert count >= 5  # Should be multiple tokens
    
    def test_count_empty_string(self):
        """Test counting tokens in empty string."""
        count = TokenCounter.count_tokens("")
        assert count == 0
    
    def test_truncate_preserves_content(self):
        """Test that truncation preserves start of text."""
        text = "This is a test message that should be truncated"
        truncated = TokenCounter.truncate_to_tokens(text, 5)
        
        assert truncated.startswith("This")
        assert truncated.endswith("...")
    
    def test_truncate_short_text_unchanged(self):
        """Test that short text is not truncated."""
        text = "Hello"
        truncated = TokenCounter.truncate_to_tokens(text, 100)
        
        assert truncated == text
    
    def test_count_messages_includes_overhead(self):
        """Test message counting includes role overhead."""
        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        count = TokenCounter.count_messages_tokens(messages)
        
        # Should include overhead per message (~4 tokens each) + 2 for structure
        assert count > 2


class TestQueryAnalysis:
    """Test suite for ContextAnalyzer query analysis."""
    
    @pytest.fixture
    def analyzer(self):
        """Create ContextAnalyzer instance."""
        return ContextAnalyzer()
    
    def test_status_query_detection(self, analyzer):
        """Test detection of status queries."""
        result = analyzer.analyze("How is the campaign going?")
        
        assert result.intent == QueryIntent.STATUS
    
    def test_task_detail_query_detection(self, analyzer):
        """Test detection of task detail queries."""
        result = analyzer.analyze("Tell me more about task-123")
        
        assert result.intent == QueryIntent.TASK_DETAIL
    
    def test_input_help_query_detection(self, analyzer):
        """Test detection of input help queries."""
        # Use query that matches INPUT_HELP patterns: what should, help
        result = analyzer.analyze("Help me decide what value to use")
        
        assert result.intent == QueryIntent.INPUT_HELP
    
    def test_blocker_query_detection(self, analyzer):
        """Test detection of blocker queries."""
        # Use query that matches BLOCKER patterns: block, stuck, wait
        result = analyzer.analyze("What's blocking the content_production stream?")
        
        assert result.intent == QueryIntent.BLOCKER
    
    def test_budget_query_detection(self, analyzer):
        """Test detection of budget queries."""
        result = analyzer.analyze("How much budget have we spent?")
        
        assert result.intent == QueryIntent.BUDGET
    
    def test_strategy_query_detection(self, analyzer):
        """Test detection of strategy queries."""
        result = analyzer.analyze("What's the plan for next week?")
        
        assert result.intent == QueryIntent.STRATEGY
    
    def test_keyword_extraction(self, analyzer):
        """Test keyword extraction from query."""
        result = analyzer.analyze("Tell me about content_production and task-456")
        
        assert len(result.keywords) > 0
    
    def test_confidence_score(self, analyzer):
        """Test that confidence scores are reasonable."""
        result = analyzer.analyze("What is the current status?")
        
        assert 0 <= result.confidence <= 1.0
    
    def test_query_analysis_structure(self, analyzer):
        """Test that query analysis has correct structure."""
        result = analyzer.analyze("What tasks are blocked?")
        
        assert hasattr(result, 'intent')
        assert hasattr(result, 'keywords')
        assert hasattr(result, 'mentioned_streams')
        assert hasattr(result, 'mentioned_tasks')
        assert hasattr(result, 'mentioned_inputs')
        assert hasattr(result, 'needs_tier2')
        assert hasattr(result, 'needs_tier3')


class TestContextCompressor:
    """Test suite for ContextCompressor."""
    
    @pytest.fixture
    def compressor(self):
        """Create ContextCompressor instance with mock DB."""
        mock_db = MagicMock()
        return ContextCompressor(mock_db)
    
    @pytest.mark.asyncio
    async def test_compress_execution_history_small(self, compressor):
        """Test that small history is not compressed."""
        executions = [
            {"status": "completed", "task_title": "Task 1", "completed_at": "2026-01-15"}
            for _ in range(3)
        ]
        
        result = await compressor.compress_execution_history(
            executions, max_tokens=1000, recent_count=3
        )
        
        assert result.compression_ratio == 1.0  # No compression needed
        assert result.preserved_count == 3
    
    @pytest.mark.asyncio
    async def test_compress_execution_history_large(self, compressor):
        """Test that large history is compressed."""
        executions = [
            {
                "status": "completed",
                "task_title": f"Task {i} with a longer title",
                "completed_at": f"2026-01-{i+1:02d}",
                "result_summary": f"Detailed result for task {i} with lots of information",
            }
            for i in range(50)
        ]
        
        result = await compressor.compress_execution_history(
            executions, max_tokens=200, recent_count=5
        )
        
        assert result.compression_ratio < 1.0  # Should compress
        assert result.preserved_count == 5  # Recent items preserved
        assert result.compressed_count == 45  # Older items compressed
    
    @pytest.mark.asyncio
    async def test_compress_conversation_history_preserves_recent(self, compressor):
        """Test that recent messages are always preserved."""
        messages = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"Message {i}"}
            for i in range(20)
        ]
        
        result = await compressor.compress_conversation_history(
            messages, max_tokens=200, recent_count=5
        )
        
        # Check that the last 5 messages are preserved
        assert result.preserved_count >= 5
    
    @pytest.mark.asyncio
    async def test_compress_task_details_keeps_relevant(self, compressor):
        """Test that relevant tasks are preserved during compression."""
        tasks = [
            {"id": f"task-{i}", "title": f"Task {i}", "status": "pending"}
            for i in range(20)
        ]
        
        relevant_ids = {"task-5", "task-15"}
        result = await compressor.compress_task_details(
            tasks, max_tokens=100, relevant_ids=relevant_ids
        )
        
        # Relevant tasks should be preserved
        assert result.preserved_count >= 2
    
    @pytest.mark.asyncio
    async def test_compression_result_structure(self, compressor):
        """Test that compression result has correct structure."""
        executions = [{"status": "completed", "task_title": "Task 1"}]
        
        result = await compressor.compress_execution_history(
            executions, max_tokens=1000, recent_count=3
        )
        
        assert isinstance(result, CompressionResult)
        assert hasattr(result, 'original_tokens')
        assert hasattr(result, 'compressed_tokens')
        assert hasattr(result, 'compression_ratio')
        assert hasattr(result, 'preserved_count')
        assert hasattr(result, 'compressed_count')
        assert hasattr(result, 'summary')


class TestContextBudgetManager:
    """Test suite for ContextBudgetManager."""
    
    def test_default_model_budget(self):
        """Test default budget calculation for unknown models."""
        manager = ContextBudgetManager("unknown-model")
        
        # Should use default limit
        assert manager.limit == 32000
        assert manager.budget is not None
    
    def test_gpt4_model_budget(self):
        """Test budget calculation for GPT-4."""
        manager = ContextBudgetManager("gpt-4")
        
        # GPT-4 has 8192 token limit
        assert manager.limit == 8192
    
    def test_claude_model_budget(self):
        """Test budget calculation for Claude models."""
        manager = ContextBudgetManager("claude-3-opus")
        
        # Claude models have large context
        assert manager.limit == 200000
    
    def test_gpt4_turbo_model_budget(self):
        """Test budget calculation for GPT-4 Turbo."""
        manager = ContextBudgetManager("gpt-4-turbo")
        
        # GPT-4 Turbo has 128k limit
        assert manager.limit == 128000
    
    def test_get_available_budget(self):
        """Test available budget calculation."""
        manager = ContextBudgetManager("gpt-4-turbo")
        
        available = manager.get_available_budget(
            system_tokens=1000,
            tier1_tokens=2000,
            conversation_tokens=3000,
        )
        
        assert "tier2" in available
        assert "tier3" in available
        assert "total_remaining" in available
        assert available["total_remaining"] > 0
    
    def test_check_overflow_under_limit(self):
        """Test overflow check when under limit."""
        manager = ContextBudgetManager("gpt-4-turbo")
        
        # Well under limit
        assert not manager.check_overflow(10000)
    
    def test_check_overflow_over_limit(self):
        """Test overflow check when over limit."""
        manager = ContextBudgetManager("gpt-4")
        
        # Over limit for GPT-4 (8192)
        assert manager.check_overflow(10000)
    
    def test_get_compression_threshold(self):
        """Test compression threshold calculation."""
        manager = ContextBudgetManager("gpt-4-turbo")
        
        threshold = manager.get_compression_threshold()
        
        # Should be 80% of limit
        assert threshold == int(128000 * 0.8)
    
    def test_model_limits_exist(self):
        """Test that model limits dictionary exists."""
        manager = ContextBudgetManager()
        
        assert "gpt-4" in manager.MODEL_LIMITS
        assert "gpt-4-turbo" in manager.MODEL_LIMITS
        assert "claude-3-opus" in manager.MODEL_LIMITS
        assert "default" in manager.MODEL_LIMITS


class TestContextBudget:
    """Test suite for ContextBudget dataclass."""
    
    def test_budget_dataclass_creation(self):
        """Test creating a ContextBudget."""
        budget = ContextBudget(
            system_prompt=1000,
            tier1_core=2000,
            tier2_detailed=3000,
            tier3_historical=1500,
            conversation_history=5000,
            user_message=1000,
            response_buffer=2000,
            safety_margin=1000,
        )
        
        assert budget.system_prompt == 1000
        assert budget.tier1_core == 2000
        assert budget.tier2_detailed == 3000
    
    def test_budget_default_values(self):
        """Test that ContextBudget has sensible defaults."""
        budget = ContextBudget()
        
        assert budget.system_prompt > 0
        assert budget.response_buffer > 0


class TestCompressionResult:
    """Test suite for CompressionResult dataclass."""
    
    def test_compression_result_creation(self):
        """Test creating a CompressionResult."""
        result = CompressionResult(
            original_tokens=1000,
            compressed_tokens=500,
            compression_ratio=0.5,
            preserved_count=10,
            compressed_count=90,
            summary="10 tasks completed",
        )
        
        assert result.original_tokens == 1000
        assert result.compressed_tokens == 500
        assert result.compression_ratio == 0.5
    
    def test_compression_ratio_calculation(self):
        """Test that compression ratio is calculated correctly."""
        result = CompressionResult(
            original_tokens=1000,
            compressed_tokens=200,
            compression_ratio=0.2,  # 200/1000
            preserved_count=5,
            compressed_count=95,
            summary="Summary text",
        )
        
        # Ratio should be compressed/original
        assert result.compression_ratio == 0.2
