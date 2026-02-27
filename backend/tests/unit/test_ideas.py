"""Tests for the Ideas system - capture, review, and strategic context."""
import pytest
from datetime import datetime
from uuid import uuid4

from app.models import (
    IdeaStatus,
    IdeaSource,
    StrategicContextCategory,
    UserIdea,
    StrategicContextEntry,
)
from app.services.ideas_service import IdeasService
from app.services.strategic_context_service import StrategicContextService


class TestIdeasService:
    """Tests for IdeasService."""
    
    @pytest.mark.asyncio
    async def test_create_idea(self, db_session, test_user):
        """Test creating a new idea."""
        service = IdeasService(db_session)
        
        idea = await service.create_idea(
            user_id=test_user.id,
            original_content="What if we use Ollama for documentation tasks?",
            reformatted_content="Use Ollama (local LLM) for documentation generation tasks to reduce costs",
            source=IdeaSource.BRAINSTORM,
        )
        
        assert idea.id is not None
        assert idea.user_id == test_user.id
        assert idea.status == IdeaStatus.NEW.value
        assert idea.source == IdeaSource.BRAINSTORM.value
        assert "Ollama" in idea.reformatted_content
    
    @pytest.mark.asyncio
    async def test_get_new_ideas(self, db_session, test_user):
        """Test fetching new (unreviewed) ideas."""
        service = IdeasService(db_session)
        
        # Create a few ideas
        for i in range(3):
            await service.create_idea(
                user_id=test_user.id,
                original_content=f"Test idea {i}",
                reformatted_content=f"Test idea {i} reformatted",
            )
        
        ideas = await service.get_new_ideas(test_user.id)
        assert len(ideas) == 3
        assert all(idea.status == IdeaStatus.NEW.value for idea in ideas)
    
    @pytest.mark.asyncio
    async def test_mark_for_tool_scout(self, db_session, test_user):
        """Test marking an idea for Tool Scout."""
        service = IdeasService(db_session)
        
        idea = await service.create_idea(
            user_id=test_user.id,
            original_content="Add support for ElevenLabs TTS",
            reformatted_content="Integrate ElevenLabs text-to-speech API",
        )
        
        updated = await service.mark_for_tool_scout(
            idea_id=idea.id,
            agent_name="opportunity_scout",
            notes="This is a tool integration request",
        )
        
        assert updated.status == IdeaStatus.TOOL.value
        assert updated.reviewed_at is not None
        assert updated.reviewed_by_agent == "opportunity_scout"
        assert "tool integration" in updated.review_notes
    
    @pytest.mark.asyncio
    async def test_mark_for_opportunity(self, db_session, test_user):
        """Test marking an idea for opportunity processing."""
        service = IdeasService(db_session)
        
        idea = await service.create_idea(
            user_id=test_user.id,
            original_content="Create a course on prompt engineering",
            reformatted_content="Business opportunity: create and sell prompt engineering course",
        )
        
        updated = await service.mark_for_opportunity(
            idea_id=idea.id,
            agent_name="opportunity_scout",
            notes="Valid business opportunity",
        )
        
        assert updated.status == IdeaStatus.OPPORTUNITY.value
        assert updated.reviewed_at is not None
    
    @pytest.mark.asyncio
    async def test_mark_as_processed(self, db_session, test_user):
        """Test marking an idea as fully processed."""
        service = IdeasService(db_session)
        
        idea = await service.create_idea(
            user_id=test_user.id,
            original_content="Focus on AI automation services",
            reformatted_content="User interest: AI automation services for businesses",
        )
        
        updated = await service.mark_as_processed(
            idea_id=idea.id,
            distilled_content="Interested in providing AI automation services to businesses",
        )
        
        assert updated.status == IdeaStatus.PROCESSED.value
        assert updated.processed_at is not None
        assert "AI automation" in updated.distilled_content
    
    @pytest.mark.asyncio
    async def test_get_idea_counts(self, db_session, test_user):
        """Test getting idea counts by status."""
        service = IdeasService(db_session)
        
        # Create ideas with different statuses
        idea1 = await service.create_idea(
            user_id=test_user.id,
            original_content="New idea 1",
            reformatted_content="New idea 1",
        )
        
        idea2 = await service.create_idea(
            user_id=test_user.id,
            original_content="Tool idea",
            reformatted_content="Tool idea",
        )
        await service.mark_for_tool_scout(idea2.id)
        
        idea3 = await service.create_idea(
            user_id=test_user.id,
            original_content="Opportunity idea",
            reformatted_content="Opportunity idea",
        )
        await service.mark_for_opportunity(idea3.id)
        
        counts = await service.get_idea_counts(test_user.id)
        
        assert counts["new"] == 1
        assert counts["tool"] == 1
        assert counts["opportunity"] == 1
        assert counts["total"] == 3  # Excludes archived


class TestStrategicContextService:
    """Tests for StrategicContextService."""
    
    @pytest.mark.asyncio
    async def test_add_entry(self, db_session, test_user):
        """Test adding a strategic context entry."""
        service = StrategicContextService(db_session)
        
        entry = await service.add_entry(
            user_id=test_user.id,
            content="User is interested in AI automation services",
            category=StrategicContextCategory.INTEREST,
            keywords=["AI", "automation", "services"],
        )
        
        assert entry.id is not None
        assert entry.user_id == test_user.id
        assert entry.category == StrategicContextCategory.INTEREST.value
        assert entry.relevance_score == 1.0
        assert "AI" in entry.keywords
    
    @pytest.mark.asyncio
    async def test_get_context_for_planning(self, db_session, test_user):
        """Test getting context entries for planning."""
        service = StrategicContextService(db_session)
        
        # Add entries
        await service.add_entry(
            user_id=test_user.id,
            content="Skilled in Python and machine learning",
            category=StrategicContextCategory.CAPABILITY,
        )
        await service.add_entry(
            user_id=test_user.id,
            content="Limited to 10 hours per week",
            category=StrategicContextCategory.CONSTRAINT,
        )
        
        entries = await service.get_context_for_planning(test_user.id)
        
        assert len(entries) == 2
        # Entries should have use_count incremented
        for entry in entries:
            assert entry.use_count == 1
            assert entry.last_used_at is not None
    
    @pytest.mark.asyncio
    async def test_find_similar_entry(self, db_session, test_user):
        """Test finding similar existing entries."""
        service = StrategicContextService(db_session)
        
        # Add initial entry with keywords
        await service.add_entry(
            user_id=test_user.id,
            content="Interested in SaaS automation tools",
            category=StrategicContextCategory.INTEREST,
            keywords=["saas", "automation", "tools"],
        )
        
        # Try to find similar entry
        similar = await service.find_similar_entry(
            user_id=test_user.id,
            content="Looking at SaaS automation products",
            category=StrategicContextCategory.INTEREST,
        )
        
        assert similar is not None
        assert "SaaS automation" in similar.content
    
    @pytest.mark.asyncio
    async def test_merge_with_existing(self, db_session, test_user):
        """Test merging new content with existing entry."""
        service = StrategicContextService(db_session)
        
        # Add initial entry with reduced relevance (simulating decay)
        entry = await service.add_entry(
            user_id=test_user.id,
            content="Interested in content creation",
            category=StrategicContextCategory.INTEREST,
        )
        # Simulate relevance decay
        entry.relevance_score = 0.7
        await db_session.commit()
        initial_relevance = entry.relevance_score
        
        # Merge with new content
        updated = await service.merge_with_existing(
            existing_entry_id=entry.id,
            new_content="Also interested in video content",
        )
        
        # Relevance should be boosted
        assert updated.relevance_score > initial_relevance
    
    @pytest.mark.asyncio
    async def test_format_context_for_prompt(self, db_session, test_user):
        """Test formatting context for LLM prompts."""
        service = StrategicContextService(db_session)
        
        # Add entries
        await service.add_entry(
            user_id=test_user.id,
            content="Python developer with ML experience",
            category=StrategicContextCategory.CAPABILITY,
        )
        await service.add_entry(
            user_id=test_user.id,
            content="Goal to earn $5000/month passive income",
            category=StrategicContextCategory.GOAL,
        )
        
        formatted = await service.format_context_for_prompt(test_user.id)
        
        assert "Strategic Context" in formatted
        assert "Python developer" in formatted
        assert "$5000" in formatted
    
    @pytest.mark.asyncio
    async def test_context_summary(self, db_session, test_user):
        """Test getting context summary."""
        service = StrategicContextService(db_session)
        
        # Add entries in different categories
        await service.add_entry(
            user_id=test_user.id,
            content="Python skills",
            category=StrategicContextCategory.CAPABILITY,
        )
        await service.add_entry(
            user_id=test_user.id,
            content="Another capability",
            category=StrategicContextCategory.CAPABILITY,
        )
        await service.add_entry(
            user_id=test_user.id,
            content="Business goal",
            category=StrategicContextCategory.GOAL,
        )
        
        summary = await service.get_context_summary(test_user.id)
        
        assert summary["total_entries"] == 3
        assert "capability" in summary["by_category"]
        assert summary["by_category"]["capability"]["count"] == 2


class TestIdeaDetection:
    """Tests for idea detection in messages."""
    
    def test_idea_pattern_extraction(self):
        """Test extracting ideas from LLM responses."""
        import re
        
        IDEA_PATTERN = re.compile(r'\[IDEA:\s*(.+?)\]', re.IGNORECASE | re.DOTALL)
        
        # Test simple idea
        text1 = "I've captured that. [IDEA: Use Ollama for documentation tasks]"
        matches = IDEA_PATTERN.findall(text1)
        assert len(matches) == 1
        assert "Ollama" in matches[0]
        
        # Test multiple ideas
        text2 = """Here are your ideas:
        [IDEA: First idea about automation]
        [IDEA: Second idea about AI services]
        """
        matches = IDEA_PATTERN.findall(text2)
        assert len(matches) == 2
        
        # Test no ideas
        text3 = "This is just a regular response with no ideas."
        matches = IDEA_PATTERN.findall(text3)
        assert len(matches) == 0
    
    def test_search_pattern_vs_idea_pattern(self):
        """Ensure search and idea patterns don't conflict."""
        import re
        
        SEARCH_PATTERN = re.compile(r'\[SEARCH:\s*(.+?)\]', re.IGNORECASE)
        IDEA_PATTERN = re.compile(r'\[IDEA:\s*(.+?)\]', re.IGNORECASE | re.DOTALL)
        
        text = """Let me search for that [SEARCH: AI automation trends 2026]
        And I've captured your idea [IDEA: Focus on AI automation services]"""
        
        search_matches = SEARCH_PATTERN.findall(text)
        idea_matches = IDEA_PATTERN.findall(text)
        
        assert len(search_matches) == 1
        assert len(idea_matches) == 1
        assert "automation trends" in search_matches[0]
        assert "AI automation services" in idea_matches[0]


class TestOpportunityScoutIdeaReview:
    """Tests for Opportunity Scout's idea review functionality."""
    
    @pytest.mark.asyncio
    async def test_idea_review_system_prompt(self, db_session):
        """Test that idea review system prompt is appropriate."""
        from app.agents.opportunity_scout import OpportunityScoutAgent
        
        agent = OpportunityScoutAgent()
        prompt = agent._get_idea_review_system_prompt()
        
        assert "TOOL" in prompt
        assert "OPPORTUNITY" in prompt
        assert "classification" in prompt.lower()
    
    @pytest.mark.asyncio
    async def test_build_idea_review_prompt(self, db_session, test_user):
        """Test building the idea review prompt."""
        from app.agents.opportunity_scout import OpportunityScoutAgent
        
        # Create mock ideas
        ideas = [
            type('MockIdea', (), {
                'id': uuid4(),
                'reformatted_content': 'Use Ollama for text generation'
            })(),
            type('MockIdea', (), {
                'id': uuid4(),
                'reformatted_content': 'Create an AI automation course'
            })(),
        ]
        
        agent = OpportunityScoutAgent()
        prompt = agent._build_idea_review_prompt(ideas)
        
        assert "2" in prompt  # Should mention 2 ideas
        assert "Ollama" in prompt
        assert "AI automation course" in prompt


class TestEndToEndIdeaFlow:
    """End-to-end tests for the complete idea flow."""
    
    @pytest.mark.asyncio
    async def test_idea_captured_and_processed(self, db_session, test_user):
        """Test the complete flow: capture → review → strategic context."""
        ideas_service = IdeasService(db_session)
        context_service = StrategicContextService(db_session)
        
        # 1. Capture idea (simulating Brainstorm capture)
        idea = await ideas_service.create_idea(
            user_id=test_user.id,
            original_content="I want to focus on AI writing tools for businesses",
            reformatted_content="Focus on developing AI writing tools for business applications",
            source=IdeaSource.BRAINSTORM,
        )
        
        assert idea.status == IdeaStatus.NEW.value
        
        # 2. Simulate Scout classification (opportunity)
        await ideas_service.mark_for_opportunity(
            idea_id=idea.id,
            agent_name="opportunity_scout",
            notes="Valid business opportunity in AI writing tools space",
        )
        
        # 3. Add to strategic context
        entry = await context_service.add_entry(
            user_id=test_user.id,
            content="Focused on AI writing tools for business applications",
            category=StrategicContextCategory.INTEREST,
            keywords=["AI", "writing", "business", "tools"],
            source_idea_id=idea.id,
        )
        
        # 4. Mark idea as processed
        await ideas_service.mark_as_processed(
            idea_id=idea.id,
            distilled_content="Focused on AI writing tools for business applications",
            strategic_context_id=entry.id,
        )
        
        # Verify final state
        await db_session.refresh(idea)
        assert idea.status == IdeaStatus.PROCESSED.value
        assert idea.strategic_context_id == entry.id
        
        # Verify context is available for planning
        formatted = await context_service.format_context_for_prompt(test_user.id)
        assert "AI writing tools" in formatted
    
    @pytest.mark.asyncio
    async def test_tool_idea_captured_and_flagged(self, db_session, test_user):
        """Test the flow for tool-related ideas."""
        ideas_service = IdeasService(db_session)
        
        # 1. Capture tool idea
        idea = await ideas_service.create_idea(
            user_id=test_user.id,
            original_content="What if we add support for DALL-E image generation?",
            reformatted_content="Add DALL-E image generation integration",
            source=IdeaSource.BRAINSTORM,
        )
        
        # 2. Simulate Scout classification (tool)
        await ideas_service.mark_for_tool_scout(
            idea_id=idea.id,
            agent_name="opportunity_scout",
            notes="Tool integration request for DALL-E",
        )
        
        # Verify it's marked for tool scout
        await db_session.refresh(idea)
        assert idea.status == IdeaStatus.TOOL.value
        
        # Verify counts
        counts = await ideas_service.get_idea_counts(test_user.id)
        assert counts["tool"] == 1
