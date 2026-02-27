"""
Usage Statistics Service - Tracks and aggregates token/API usage and costs.

Provides:
- Token usage aggregation from agent messages
- Tool execution cost tracking
- Daily/weekly/monthly usage breakdowns
- Cost estimation based on provider pricing
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, func, and_, cast, Date, Integer
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ToolExecution, ToolExecutionStatus
from app.models.llm_usage import LLMUsage

logger = logging.getLogger(__name__)


# Pricing per 1M tokens (as of June 2025 - update as needed)
# Format: {model_prefix: (input_price_per_1M, output_price_per_1M)}
# NOTE: get_model_pricing() matches these as *prefixes* (longest-match-first).
# When adding new models, all date-suffixed variants (e.g. "gpt-4o-2024-11-20")
# are automatically covered by the prefix entry.
MODEL_PRICING = {
    # ==========================================================================
    # Anthropic Claude Models
    # ==========================================================================
    # Opus 4.6 - Best reasoning ($5/$25)
    "claude-opus-4-6": (5.00, 25.00),
    # Opus 4.5 - Same price tier as 4.6 ($5/$25); MUST precede "claude-opus-4" prefix
    "claude-opus-4-5": (5.00, 25.00),
    # Opus 4.1 - Older Opus 4.x at higher price ($15/$75)
    "claude-opus-4-1": (15.00, 75.00),
    # Opus 4 base / legacy
    "claude-opus-4": (15.00, 75.00),
    "claude-4-opus": (15.00, 75.00),
    # Sonnet 4.6 - Balanced quality/speed
    "claude-sonnet-4-6": (3.00, 15.00),
    # Sonnet 4.5 - Same price as 4.6
    "claude-sonnet-4-5": (3.00, 15.00),
    # Sonnet 4 - legacy
    "claude-sonnet-4": (3.00, 15.00),
    "claude-4-sonnet": (3.00, 15.00),
    # Haiku 4.5 - Fast
    "claude-haiku-4-5": (1.00, 5.00),
    # Haiku 3.5 - legacy
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-3.5-haiku": (0.80, 4.00),
    # Legacy Claude 3 models
    "claude-3-7-sonnet": (3.00, 15.00),   # Sonnet 3.7 (deprecated) uses claude-3-7-sonnet-* IDs
    "claude-3-5-sonnet": (3.00, 15.00),
    "claude-3-opus": (15.00, 75.00),
    "claude-3-sonnet": (3.00, 15.00),
    "claude-3-haiku": (0.25, 1.25),

    # ==========================================================================
    # OpenAI Models  (prices: USD per 1M tokens, standard tier)
    # https://platform.openai.com/docs/pricing
    # ==========================================================================
    # GPT-5 Series (flagship, 2025)
    "gpt-5.2": (1.75, 14.00),          # GPT-5.2 - latest flagship
    "gpt-5.1": (1.25, 10.00),          # GPT-5.1
    "gpt-5-mini": (0.25, 2.00),        # GPT-5 mini  (must precede "gpt-5")
    "gpt-5-nano": (0.05, 0.40),        # GPT-5 nano  (must precede "gpt-5")
    "gpt-5": (1.25, 10.00),            # GPT-5 (base)
    # GPT-4.1 Series
    "gpt-4.1-mini": (0.40, 1.60),      # GPT-4.1 mini (must precede "gpt-4.1")
    "gpt-4.1-nano": (0.10, 0.40),      # GPT-4.1 nano (must precede "gpt-4.1")
    "gpt-4.1": (2.00, 8.00),           # GPT-4.1
    # GPT-4o Series
    "gpt-4o-mini": (0.15, 0.60),       # Very cheap, still capable (must precede "gpt-4o")
    "gpt-4o": (2.50, 10.00),           # High quality, fast
    # o-series reasoning models
    "o4-mini": (1.10, 4.40),           # Optimised mini reasoning
    "o3-pro": (20.00, 80.00),          # o3 Pro (must precede "o3")
    "o3-mini": (1.10, 4.40),           # o3 mini  (must precede "o3")
    "o3": (2.00, 8.00),                # o3 full reasoning
    "o1-preview": (15.00, 60.00),      # o1 preview (must precede "o1")
    "o1-mini": (1.10, 4.40),           # o1 mini  (must precede "o1")
    "o1": (15.00, 60.00),              # o1 full reasoning
    # Legacy GPT-4
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-4": (30.00, 60.00),
    "gpt-3.5-turbo": (0.50, 1.50),

    # ==========================================================================
    # Z.ai / Zhipu GLM Models (BEST VALUE - has FREE options!)
    # https://open.bigmodel.cn/pricing
    # Prices listed in USD; Z.ai CNY prices converted at ~7.26 CNY/USD.
    # ==========================================================================
    # GLM-5 Series (top of range, 2025)
    "glm-5": (0.55, 2.50),             # ¥4/¥18 per M → ~$0.55/$2.50
    # GLM-4.7 Series
    "glm-4.7-flashx": (0.07, 0.40),    # Enhanced flash  (must precede "glm-4.7-flash")
    "glm-4.7-flash": (0.00, 0.00),     # FREE! Fast responses
    "glm-4.7": (0.60, 2.20),           # Reasoning model
    # GLM-4.6 Series (with vision support)
    "glm-4.6": (0.60, 2.20),
    "glm-4.6v": (0.30, 0.90),          # Vision model
    "glm-4.6v-flash": (0.00, 0.00),    # FREE! Vision
    "glm-4.6v-flashx": (0.04, 0.40),
    # GLM-4.5 Series
    "glm-4.5": (0.60, 2.20),
    "glm-4.5v": (0.60, 1.80),
    "glm-4.5-x": (2.20, 8.90),         # Premium
    "glm-4.5-air": (0.20, 1.10),       # Lightweight
    "glm-4.5-airx": (1.10, 4.50),
    "glm-4.5-flash": (0.00, 0.00),     # FREE!
    # Other GLM models
    "glm-4-32b-0414-128k": (0.10, 0.10),
    # Legacy naming fallbacks
    "glm-4-flash": (0.00, 0.00),
    "glm-4": (1.00, 1.00),
    
    # ==========================================================================
    # Ollama Models (LOCAL - FREE)
    # ==========================================================================
    # All Ollama models are free since they run locally
    # Common models that users might configure:
    "mistral": (0.00, 0.00),
    "mistral-nemo": (0.00, 0.00),
    "qwen": (0.00, 0.00),
    "qwen2": (0.00, 0.00),
    "qwen2.5": (0.00, 0.00),
    "llama": (0.00, 0.00),
    "llama2": (0.00, 0.00),
    "llama3": (0.00, 0.00),
    "llama3.1": (0.00, 0.00),
    "llama3.2": (0.00, 0.00),
    "codellama": (0.00, 0.00),
    "deepseek": (0.00, 0.00),
    "deepseek-coder": (0.00, 0.00),
    "phi": (0.00, 0.00),
    "phi3": (0.00, 0.00),
    "gemma": (0.00, 0.00),
    "gemma2": (0.00, 0.00),
    "yi": (0.00, 0.00),
    "vicuna": (0.00, 0.00),
    "neural-chat": (0.00, 0.00),
    "starling": (0.00, 0.00),
    "orca": (0.00, 0.00),
    "dolphin": (0.00, 0.00),
}

# Tool pricing (per cost_unit)
# For hardcoded tools, the meaning of cost_unit varies:
#   serper: 1 unit = 1 search
#   dalle:  1 unit = 1 image
#   elevenlabs: 1 unit = 1 character
# LLM tools (glm/claude/gpt): cost_units = 0 (tracked via llm_usage table)
# Custom tools: cost_units = 1 per execution if cost_per_execution is set
TOOL_PRICING = {
    "serper-web-search": 0.001,  # $0.001 per search
    "openai-dalle-3": 0.04,  # $0.04 per image (standard 1024x1024)
    "elevenlabs-voice-generation": 0.00003,  # ~$0.03 per 1000 characters
    # LLM tools priced at 0 here (costs tracked in llm_usage table)
    "zai-glm-47": 0.0,
    "anthropic-claude-sonnet-45": 0.0,
    "openai-gpt-52": 0.0,
}


@dataclass
class TokenUsage:
    """Token usage for a specific model."""
    model: str
    message_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float


@dataclass
class ToolUsage:
    """Tool usage statistics."""
    tool_slug: str
    tool_name: str
    execution_count: int
    success_count: int
    failure_count: int
    total_cost_units: int
    estimated_cost_usd: float
    avg_duration_ms: float


@dataclass
class DailyUsage:
    """Usage for a specific day."""
    date: str  # YYYY-MM-DD
    token_count: int
    message_count: int
    tool_executions: int
    estimated_cost_usd: float


@dataclass
class UsageSummary:
    """Overall usage summary."""
    period_start: datetime
    period_end: datetime
    total_tokens: int
    total_messages: int
    total_tool_executions: int
    total_estimated_cost_usd: float
    by_model: List[TokenUsage]
    by_tool: List[ToolUsage]
    daily: List[DailyUsage]


def get_model_pricing(model_name: str) -> tuple[float, float]:
    """Get pricing for a model (input, output per 1M tokens).

    Matching strategy (in order):
    1. Ollama local models (``model:tag`` format, e.g. ``mistral:7b``) → free.
    2. Exact case-insensitive match in MODEL_PRICING.
    3. Longest-prefix-first match using ``startswith`` so that a key like
       ``"gpt-4o-mini"`` is always preferred over the shorter ``"gpt-4o"``.
    4. Unknown model → ``($1.00, $1.00)`` conservative fallback.
    """
    if not model_name:
        return (0.0, 0.0)

    model_lower = model_name.lower().strip()

    # Ollama models use "model:tag" format (e.g., "mistral:7b") - always free.
    # Guard against paid API models if they ever adopt a colon convention.
    _PAID_PREFIXES = ("claude", "gpt", "glm", "o1", "o3", "o4")
    if ":" in model_lower and not model_lower.startswith(_PAID_PREFIXES):
        return (0.0, 0.0)

    # Exact match (handles full versioned IDs like "gpt-4o-2024-11-20" if listed)
    if model_lower in MODEL_PRICING:
        return MODEL_PRICING[model_lower]

    # Prefix match — longest key wins so more-specific entries beat shorter ones.
    # e.g. "gpt-4o-mini" (10 chars) beats "gpt-4o" (6 chars) for "gpt-4o-mini-..."
    for prefix in sorted(MODEL_PRICING.keys(), key=len, reverse=True):
        if model_lower.startswith(prefix):
            return MODEL_PRICING[prefix]

    # Unknown model — conservative fallback
    logger.warning("Unknown model for pricing: %s — using $1.00/$1.00 fallback", model_name)
    return (1.0, 1.0)


def calculate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """
    Calculate the cost for a specific LLM call with known token counts.
    
    Args:
        model: The model name/identifier
        prompt_tokens: Number of input tokens
        completion_tokens: Number of output tokens
        
    Returns:
        Cost in USD rounded to 6 decimal places
    """
    input_price, output_price = get_model_pricing(model)
    
    input_cost = (prompt_tokens / 1_000_000) * input_price
    output_cost = (completion_tokens / 1_000_000) * output_price
    
    return round(input_cost + output_cost, 6)


def estimate_token_cost(model: str, total_tokens: int) -> float:
    """
    Estimate cost for tokens when only total is known.
    
    Note: Without separate prompt/completion counts, we assume 
    roughly 30% prompt, 70% completion for agent responses.
    """
    input_price, output_price = get_model_pricing(model)
    
    # Estimate split (agent responses are mostly output)
    prompt_tokens = int(total_tokens * 0.3)
    completion_tokens = total_tokens - prompt_tokens
    
    input_cost = (prompt_tokens / 1_000_000) * input_price
    output_cost = (completion_tokens / 1_000_000) * output_price
    
    return round(input_cost + output_cost, 6)


def estimate_tool_cost(tool_slug: str, cost_units: int) -> float:
    """Estimate cost for tool executions."""
    unit_price = TOOL_PRICING.get(tool_slug, 0.001)  # Default $0.001/unit
    return round(cost_units * unit_price, 6)


class UsageService:
    """Service for aggregating and reporting usage statistics."""
    
    async def get_usage_summary(
        self,
        db: AsyncSession,
        days: int = 30,
        user_id: Optional[UUID] = None,
    ) -> UsageSummary:
        """
        Get comprehensive usage summary for a period.
        
        Args:
            db: Database session
            days: Number of days to include (default 30)
            user_id: Optional filter by user
            
        Returns:
            UsageSummary with all aggregated data
        """
        end_date = utc_now()
        start_date = end_date - timedelta(days=days)
        
        # Get token usage by model
        by_model = await self._get_token_usage_by_model(db, start_date, end_date, user_id)
        
        # Get tool usage
        by_tool = await self._get_tool_usage(db, start_date, end_date, user_id)
        
        # Get daily breakdown
        daily = await self._get_daily_usage(db, start_date, end_date, user_id)
        
        # Calculate totals
        total_tokens = sum(m.total_tokens for m in by_model)
        total_messages = sum(m.message_count for m in by_model)
        total_tool_executions = sum(t.execution_count for t in by_tool)
        total_cost = sum(m.estimated_cost_usd for m in by_model) + sum(t.estimated_cost_usd for t in by_tool)
        
        return UsageSummary(
            period_start=start_date,
            period_end=end_date,
            total_tokens=total_tokens,
            total_messages=total_messages,
            total_tool_executions=total_tool_executions,
            total_estimated_cost_usd=round(total_cost, 4),
            by_model=by_model,
            by_tool=by_tool,
            daily=daily,
        )
    
    async def _get_token_usage_by_model(
        self,
        db: AsyncSession,
        start_date: datetime,
        end_date: datetime,
        user_id: Optional[UUID] = None,
    ) -> List[TokenUsage]:
        """Get token usage aggregated by model from llm_usage table (single source of truth)."""
        query = (
            select(
                LLMUsage.model,
                func.count(LLMUsage.id).label('call_count'),
                func.coalesce(func.sum(LLMUsage.prompt_tokens), 0).label('prompt_tokens'),
                func.coalesce(func.sum(LLMUsage.completion_tokens), 0).label('completion_tokens'),
                func.coalesce(func.sum(LLMUsage.total_tokens), 0).label('total_tokens'),
                func.coalesce(func.sum(LLMUsage.cost_usd), 0).label('total_cost'),
            )
            .where(
                and_(
                    LLMUsage.created_at >= start_date,
                    LLMUsage.created_at <= end_date,
                    LLMUsage.model.isnot(None),
                )
            )
            .group_by(LLMUsage.model)
            .order_by(func.sum(LLMUsage.total_tokens).desc())
        )
        
        if user_id:
            query = query.where(LLMUsage.user_id == user_id)
        
        result = await db.execute(query)
        rows = result.all()
        
        usage_list = []
        for row in rows:
            prompt_tokens = int(row.prompt_tokens or 0)
            completion_tokens = int(row.completion_tokens or 0)
            total_tokens = int(row.total_tokens or 0)
            cost = float(row.total_cost or 0)
            
            usage_list.append(TokenUsage(
                model=row.model or "unknown",
                message_count=row.call_count,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=round(cost, 6) if cost > 0 else calculate_cost(row.model, prompt_tokens, completion_tokens),
            ))
        
        return usage_list
    
    async def _get_tool_usage(
        self,
        db: AsyncSession,
        start_date: datetime,
        end_date: datetime,
        user_id: Optional[UUID] = None,
    ) -> List[ToolUsage]:
        """Get tool usage aggregated by tool."""
        from app.models import Tool
        
        query = (
            select(
                Tool.slug,
                Tool.name,
                func.count(ToolExecution.id).label('execution_count'),
                func.sum(
                    func.cast(ToolExecution.status == ToolExecutionStatus.COMPLETED, Integer)
                ).label('success_count'),
                func.coalesce(func.sum(ToolExecution.cost_units), 0).label('total_cost_units'),
                func.coalesce(func.avg(ToolExecution.duration_ms), 0).label('avg_duration_ms'),
            )
            .join(Tool, ToolExecution.tool_id == Tool.id)
            .where(
                and_(
                    ToolExecution.created_at >= start_date,
                    ToolExecution.created_at <= end_date,
                )
            )
            .group_by(Tool.slug, Tool.name)
            .order_by(func.count(ToolExecution.id).desc())
        )
        
        if user_id:
            query = query.where(ToolExecution.triggered_by_user_id == user_id)
        
        result = await db.execute(query)
        rows = result.all()
        
        usage_list = []
        for row in rows:
            execution_count = row.execution_count
            success_count = int(row.success_count or 0)
            total_cost_units = int(row.total_cost_units or 0)
            
            usage_list.append(ToolUsage(
                tool_slug=row.slug,
                tool_name=row.name,
                execution_count=execution_count,
                success_count=success_count,
                failure_count=execution_count - success_count,
                total_cost_units=total_cost_units,
                estimated_cost_usd=estimate_tool_cost(row.slug, total_cost_units),
                avg_duration_ms=float(row.avg_duration_ms or 0),
            ))
        
        return usage_list
    
    async def _get_daily_usage(
        self,
        db: AsyncSession,
        start_date: datetime,
        end_date: datetime,
        user_id: Optional[UUID] = None,
    ) -> List[DailyUsage]:
        """Get daily usage breakdown from llm_usage table (single source of truth)."""
        # LLM usage by day
        llm_query = (
            select(
                cast(LLMUsage.created_at, Date).label('date'),
                func.coalesce(func.sum(LLMUsage.total_tokens), 0).label('tokens'),
                func.count(LLMUsage.id).label('call_count'),
                func.coalesce(func.sum(LLMUsage.cost_usd), 0).label('cost'),
            )
            .where(
                and_(
                    LLMUsage.created_at >= start_date,
                    LLMUsage.created_at <= end_date,
                )
            )
            .group_by(cast(LLMUsage.created_at, Date))
        )
        
        if user_id:
            llm_query = llm_query.where(LLMUsage.user_id == user_id)
        
        llm_result = await db.execute(llm_query)
        llm_rows = {str(row.date): row for row in llm_result.all()}
        
        # Tool executions by day
        tool_query = (
            select(
                cast(ToolExecution.created_at, Date).label('date'),
                func.count(ToolExecution.id).label('executions'),
                func.coalesce(func.sum(ToolExecution.cost_units), 0).label('cost_units'),
            )
            .where(
                and_(
                    ToolExecution.created_at >= start_date,
                    ToolExecution.created_at <= end_date,
                )
            )
            .group_by(cast(ToolExecution.created_at, Date))
        )
        
        if user_id:
            tool_query = tool_query.where(ToolExecution.triggered_by_user_id == user_id)
        
        tool_result = await db.execute(tool_query)
        tool_rows = {str(row.date): row for row in tool_result.all()}
        
        # Combine into daily records
        all_dates = set(llm_rows.keys()) | set(tool_rows.keys())
        daily_list = []
        
        for date_str in sorted(all_dates):
            llm_data = llm_rows.get(date_str)
            tool_data = tool_rows.get(date_str)
            
            tokens = int(llm_data.tokens) if llm_data else 0
            call_count = int(llm_data.call_count) if llm_data else 0
            llm_cost = float(llm_data.cost) if llm_data else 0
            executions = int(tool_data.executions) if tool_data else 0
            tool_cost_units = int(tool_data.cost_units) if tool_data else 0
            tool_cost = tool_cost_units * 0.001
            
            daily_list.append(DailyUsage(
                date=date_str,
                token_count=tokens,
                message_count=call_count,
                tool_executions=executions,
                estimated_cost_usd=round(llm_cost + tool_cost, 4),
            ))
        
        return daily_list
    
    async def get_recent_executions(
        self,
        db: AsyncSession,
        limit: int = 20,
        user_id: Optional[UUID] = None,
    ) -> List[dict]:
        """Get recent tool executions for activity feed."""
        from app.models import Tool
        
        query = (
            select(ToolExecution, Tool.name, Tool.slug)
            .join(Tool, ToolExecution.tool_id == Tool.id)
            .order_by(ToolExecution.created_at.desc())
            .limit(limit)
        )
        
        if user_id:
            query = query.where(ToolExecution.triggered_by_user_id == user_id)
        
        result = await db.execute(query)
        rows = result.all()
        
        return [
            {
                "id": str(row.ToolExecution.id),
                "tool_name": row.name,
                "tool_slug": row.slug,
                "status": row.ToolExecution.status.value,
                "duration_ms": row.ToolExecution.duration_ms,
                "cost_units": row.ToolExecution.cost_units,
                "agent_name": row.ToolExecution.agent_name,
                "created_at": row.ToolExecution.created_at.isoformat(),
                "error": row.ToolExecution.error_message,
            }
            for row in rows
        ]


# Global service instance
usage_service = UsageService()
