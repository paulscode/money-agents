"""LLM Usage Tracking Service.

Provides a simple interface for tracking LLM API calls throughout the application.
This is the single point for recording all LLM usage for cost analysis.

Usage:
    from app.services.llm_usage_service import llm_usage_service, LLMUsageSource
    
    # Track a brainstorm call
    await llm_usage_service.track(
        db=db,
        user_id=user.id,
        source=LLMUsageSource.BRAINSTORM,
        provider="claude",
        model="claude-haiku-4-5",
        prompt_tokens=100,
        completion_tokens=50,
        cost_usd=0.00025,
        latency_ms=500,
    )
"""
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm_usage import LLMUsage, LLMUsageSource
from app.services.usage_service import calculate_cost

logger = logging.getLogger(__name__)


class LLMUsageService:
    """Service for tracking LLM API usage."""
    
    async def track(
        self,
        db: AsyncSession,
        source: LLMUsageSource,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        user_id: Optional[UUID] = None,
        conversation_id: Optional[UUID] = None,
        message_id: Optional[UUID] = None,
        agent_run_id: Optional[UUID] = None,
        campaign_id: Optional[UUID] = None,
        cost_usd: Optional[float] = None,
        latency_ms: Optional[int] = None,
        meta_data: Optional[dict] = None,
    ) -> LLMUsage:
        """
        Track an LLM API call.
        
        Args:
            db: Database session
            source: Where the call originated
            provider: LLM provider (glm, claude, openai, ollama)
            model: Model name
            prompt_tokens: Input tokens
            completion_tokens: Output tokens
            user_id: User who initiated the call
            conversation_id: Related conversation
            message_id: Related message
            agent_run_id: Related agent run
            campaign_id: Related campaign
            cost_usd: Pre-calculated cost (will calculate if not provided)
            latency_ms: Response latency
            meta_data: Additional context
            
        Returns:
            Created LLMUsage record
        """
        # Calculate cost if not provided
        if cost_usd is None and model:
            cost_usd = calculate_cost(model, prompt_tokens, completion_tokens)
        
        total_tokens = prompt_tokens + completion_tokens
        
        usage = LLMUsage(
            user_id=user_id,
            source=source,
            conversation_id=conversation_id,
            message_id=message_id,
            agent_run_id=agent_run_id,
            campaign_id=campaign_id,
            provider=provider,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            meta_data=meta_data,
        )
        
        db.add(usage)
        await db.flush()
        
        logger.debug(
            f"Tracked LLM usage: source={source.value}, model={model}, "
            f"tokens={total_tokens}, cost=${cost_usd or 0:.6f}"
        )
        
        return usage
    
    async def track_from_response(
        self,
        db: AsyncSession,
        source: LLMUsageSource,
        response,  # LLMResponse or similar object with token info
        user_id: Optional[UUID] = None,
        conversation_id: Optional[UUID] = None,
        message_id: Optional[UUID] = None,
        agent_run_id: Optional[UUID] = None,
        campaign_id: Optional[UUID] = None,
        meta_data: Optional[dict] = None,
    ) -> LLMUsage:
        """
        Track usage from an LLMResponse object.
        
        Convenience method that extracts token info from the response.
        """
        return await self.track(
            db=db,
            source=source,
            provider=getattr(response, 'provider', ''),
            model=getattr(response, 'model', ''),
            prompt_tokens=getattr(response, 'prompt_tokens', 0),
            completion_tokens=getattr(response, 'completion_tokens', 0),
            cost_usd=getattr(response, 'cost_usd', None),
            latency_ms=getattr(response, 'latency_ms', None),
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            agent_run_id=agent_run_id,
            campaign_id=campaign_id,
            meta_data=meta_data,
        )


# Global service instance
llm_usage_service = LLMUsageService()
