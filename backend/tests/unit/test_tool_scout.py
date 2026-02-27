"""Tests for Tool Scout agent and services."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.models.tool_scout import (
    ToolKnowledge,
    ToolKnowledgeCategory,
    ToolKnowledgeStatus,
    ToolIdeaEntry,
)
from app.models import UserIdea, IdeaStatus
from app.services.tool_knowledge_service import ToolKnowledgeService
from app.services.tool_idea_service import ToolIdeaService
from app.agents.tool_scout import ToolScoutAgent


class TestToolKnowledgeService:
    """Tests for ToolKnowledgeService."""
    
    @pytest.fixture
    async def service(self, db_session):
        """Create a service instance."""
        return ToolKnowledgeService(db_session)
    
    @pytest.mark.asyncio
    async def test_add_entry(self, service):
        """Test adding a knowledge entry."""
        entry = await service.add_entry(
            title="Test Tool",
            summary="A test tool for testing",
            category=ToolKnowledgeCategory.TOOL,
            keywords=["test", "tool"],
        )
        
        assert entry.id is not None
        assert entry.title == "Test Tool"
        assert entry.summary == "A test tool for testing"
        assert entry.category == ToolKnowledgeCategory.TOOL.value
        assert entry.status == ToolKnowledgeStatus.ACTIVE.value
        assert entry.relevance_score == 1.0
    
    @pytest.mark.asyncio
    async def test_get_active_entries(self, service):
        """Test retrieving active entries."""
        # Add some entries
        await service.add_entry(
            title="Tool 1",
            summary="First tool",
            category=ToolKnowledgeCategory.TOOL,
        )
        await service.add_entry(
            title="Tool 2", 
            summary="Second tool",
            category=ToolKnowledgeCategory.PLATFORM,
        )
        
        entries = await service.get_active_entries()
        assert len(entries) >= 2
    
    @pytest.mark.asyncio
    async def test_find_similar_entry(self, service):
        """Test finding similar entries."""
        await service.add_entry(
            title="OpenAI GPT-4",
            summary="Large language model from OpenAI",
            category=ToolKnowledgeCategory.TOOL,
            keywords=["openai", "gpt", "llm", "language-model"],
        )
        
        # Should find by title match
        similar = await service.find_similar_entry(
            title="OpenAI GPT-4",
            keywords=[],
        )
        assert similar is not None
        
        # Should find by keyword overlap
        similar = await service.find_similar_entry(
            title="Something else",
            keywords=["openai", "gpt", "llm"],
        )
        assert similar is not None
    
    @pytest.mark.asyncio
    async def test_validate_entry(self, service):
        """Test validating an entry boosts relevance."""
        entry = await service.add_entry(
            title="Test Tool",
            summary="Test",
            category=ToolKnowledgeCategory.TOOL,
            relevance_score=0.5,
        )
        
        updated = await service.validate_entry(entry.id, boost_relevance=0.2)
        assert updated.relevance_score == 0.7
        assert updated.validation_count == 1
    
    @pytest.mark.asyncio
    async def test_archive_entry(self, service):
        """Test archiving an entry."""
        entry = await service.add_entry(
            title="Old Tool",
            summary="No longer relevant",
            category=ToolKnowledgeCategory.TOOL,
        )
        
        result = await service.archive_entry(entry.id)
        assert result is True
        
        # Verify archived
        retrieved = await service.get_entry(entry.id)
        assert retrieved.status == ToolKnowledgeStatus.ARCHIVED.value
    
    @pytest.mark.asyncio
    async def test_format_knowledge_for_prompt(self, service):
        """Test formatting knowledge for prompt."""
        await service.add_entry(
            title="Claude 3",
            summary="Anthropic's advanced AI assistant",
            category=ToolKnowledgeCategory.TOOL,
            keywords=["anthropic", "claude", "ai"],
        )
        
        formatted = await service.format_knowledge_for_prompt(limit=10)
        assert "Claude 3" in formatted
        assert "tool" in formatted.lower()


class TestToolIdeaService:
    """Tests for ToolIdeaService."""
    
    @pytest.fixture
    async def service(self, db_session):
        """Create a service instance."""
        return ToolIdeaService(db_session)
    
    @pytest.fixture
    async def user(self, db_session):
        """Create a test user."""
        from app.models import User
        user = User(
            username=f"testuser_{uuid4().hex[:8]}",
            email=f"test_{uuid4().hex[:8]}@example.com",
            password_hash="hashedpassword",
            role="user",
            is_active=True,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user
    
    @pytest.mark.asyncio
    async def test_add_entry(self, service, user):
        """Test adding a tool idea entry."""
        entry = await service.add_entry(
            user_id=user.id,
            summary="Need a tool for PDF extraction",
            use_case="Extract text from PDF documents for analysis",
            keywords=["pdf", "extraction", "text"],
        )
        
        assert entry.id is not None
        assert entry.summary == "Need a tool for PDF extraction"
        assert entry.is_addressed is False
    
    @pytest.mark.asyncio
    async def test_get_user_entries(self, service, user):
        """Test getting entries for a user."""
        await service.add_entry(
            user_id=user.id,
            summary="Tool idea 1",
        )
        await service.add_entry(
            user_id=user.id,
            summary="Tool idea 2",
        )
        
        entries = await service.get_user_entries(user.id)
        assert len(entries) == 2
    
    @pytest.mark.asyncio
    async def test_mark_as_addressed(self, service, user, db_session):
        """Test marking an entry as addressed."""
        from app.models import Tool, ToolCategory, ToolStatus
        
        # Create a tool
        tool = Tool(
            name="PDF Extractor",
            slug="pdf-extractor",
            category=ToolCategory.DATA_SOURCE,
            description="Extracts text from PDFs",
            status=ToolStatus.IMPLEMENTED,
            requester_id=user.id,
        )
        db_session.add(tool)
        await db_session.commit()
        await db_session.refresh(tool)
        
        entry = await service.add_entry(
            user_id=user.id,
            summary="Need PDF extraction",
        )
        
        updated = await service.mark_as_addressed(entry.id, tool.id)
        assert updated.is_addressed is True
        assert updated.addressed_by_tool_id == tool.id
    
    @pytest.mark.asyncio
    async def test_format_for_prompt(self, service, user):
        """Test formatting ideas for prompt."""
        await service.add_entry(
            user_id=user.id,
            summary="PDF extraction tool",
            use_case="Extract text for analysis",
            priority="high",
        )
        
        formatted = await service.format_for_prompt(user.id)
        assert "PDF extraction" in formatted
        assert "high" in formatted.lower()


class TestToolScoutAgent:
    """Tests for ToolScoutAgent."""
    
    @pytest.fixture
    def agent(self):
        """Create agent instance."""
        return ToolScoutAgent()
    
    def test_agent_properties(self, agent):
        """Test basic agent properties."""
        assert agent.name == "tool_scout"
        assert agent.model_tier == "quality"
    
    def test_get_system_prompt(self, agent):
        """Test system prompt generation for tool discussions."""
        prompt = agent.get_system_prompt(tools=[])
        
        assert "Tool Scout Agent" in prompt
        assert "<tool_edit" in prompt
        assert "status" in prompt.lower()
        assert "implementation_notes" in prompt.lower()
    
    def test_get_system_prompt_with_tool_context(self, agent):
        """Test system prompt with tool context."""
        tool_context = {
            "name": "Test Tool",
            "status": "implementing",
            "category": "automation",
            "description": "A test tool",
        }
        
        prompt = agent.get_system_prompt(tools=[], tool_context=tool_context)
        assert "Test Tool" in prompt
        assert "implementing" in prompt
    
    def test_idea_processing_prompt(self, agent):
        """Test idea processing prompt."""
        prompt = agent._get_idea_processing_prompt()
        
        assert "distill" in prompt.lower()
        assert "summary" in prompt
        assert "keywords" in prompt
    
    def test_discovery_system_prompt(self, agent):
        """Test discovery system prompt."""
        prompt = agent._get_discovery_system_prompt()
        
        assert "discover" in prompt.lower()
        assert "[SEARCH:" in prompt
    
    def test_extract_searches(self, agent):
        """Test search query extraction."""
        content = """
        I'll search for relevant tools.
        [SEARCH: best AI automation tools 2026]
        Let me also check:
        [SEARCH: new API services for data extraction]
        """
        
        searches = agent._extract_searches(content)
        assert len(searches) == 2
        assert "AI automation tools" in searches[0]
        assert "data extraction" in searches[1]
    
    def test_extract_json_from_response(self, agent):
        """Test JSON extraction from response."""
        # Simple JSON
        content = '{"key": "value"}'
        result = agent._extract_json_from_response(content)
        assert result == {"key": "value"}
        
        # JSON in code block
        content = """Here's the result:
```json
{"findings": [{"title": "Test"}]}
```
"""
        result = agent._extract_json_from_response(content)
        assert result["findings"][0]["title"] == "Test"
    
    def test_build_discovery_prompt(self, agent):
        """Test discovery prompt building."""
        prompt = agent._build_discovery_prompt(
            knowledge_context="- Tool 1: Does X",
            ideas_context="- Need tool for Y",
            search_focus="AI automation",
        )
        
        assert "Tool 1" in prompt
        assert "Need tool for Y" in prompt
        assert "AI automation" in prompt
    
    def test_evaluation_system_prompt(self, agent):
        """Test evaluation system prompt."""
        prompt = agent._get_evaluation_system_prompt()
        
        assert "evaluate" in prompt.lower() or "recommend" in prompt.lower()
        assert "name" in prompt
        assert "slug" in prompt
        assert "category" in prompt
