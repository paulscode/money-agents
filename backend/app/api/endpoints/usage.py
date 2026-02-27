"""Usage statistics API endpoints."""
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, and_, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models import User
from app.services.usage_service import usage_service

router = APIRouter()


# -------------------------------------------------------------------------
# Response Models
# -------------------------------------------------------------------------

class TokenUsageResponse(BaseModel):
    """Token usage for a specific model."""
    model: str
    message_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float


class ToolUsageResponse(BaseModel):
    """Tool usage statistics."""
    tool_slug: str
    tool_name: str
    execution_count: int
    success_count: int
    failure_count: int
    total_cost_units: int
    estimated_cost_usd: float
    avg_duration_ms: float


class DailyUsageResponse(BaseModel):
    """Usage for a specific day."""
    date: str
    token_count: int
    message_count: int
    tool_executions: int
    estimated_cost_usd: float


class UsageSummaryResponse(BaseModel):
    """Overall usage summary."""
    period_start: datetime
    period_end: datetime
    total_tokens: int
    total_messages: int
    total_tool_executions: int
    total_estimated_cost_usd: float
    by_model: List[TokenUsageResponse]
    by_tool: List[ToolUsageResponse]
    daily: List[DailyUsageResponse]


class RecentExecutionResponse(BaseModel):
    """Recent tool execution."""
    id: str
    tool_name: str
    tool_slug: str
    status: str
    duration_ms: Optional[int]
    cost_units: Optional[int]
    agent_name: Optional[str]
    created_at: str
    error: Optional[str]


# -------------------------------------------------------------------------
# Endpoints
# -------------------------------------------------------------------------

@router.get("/summary", response_model=UsageSummaryResponse)
async def get_usage_summary(
    days: int = Query(default=30, ge=1, le=365, description="Number of days to include"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get comprehensive usage summary.
    
    Returns token usage by model, tool usage, and daily breakdown.
    Admin sees all usage, regular users see their own.
    """
    # Regular users only see their own usage
    user_id = None if current_user.role == "admin" else current_user.id
    
    summary = await usage_service.get_usage_summary(
        db=db,
        days=days,
        user_id=user_id,
    )
    
    return UsageSummaryResponse(
        period_start=summary.period_start,
        period_end=summary.period_end,
        total_tokens=summary.total_tokens,
        total_messages=summary.total_messages,
        total_tool_executions=summary.total_tool_executions,
        total_estimated_cost_usd=summary.total_estimated_cost_usd,
        by_model=[
            TokenUsageResponse(
                model=m.model,
                message_count=m.message_count,
                prompt_tokens=m.prompt_tokens,
                completion_tokens=m.completion_tokens,
                total_tokens=m.total_tokens,
                estimated_cost_usd=m.estimated_cost_usd,
            )
            for m in summary.by_model
        ],
        by_tool=[
            ToolUsageResponse(
                tool_slug=t.tool_slug,
                tool_name=t.tool_name,
                execution_count=t.execution_count,
                success_count=t.success_count,
                failure_count=t.failure_count,
                total_cost_units=t.total_cost_units,
                estimated_cost_usd=t.estimated_cost_usd,
                avg_duration_ms=t.avg_duration_ms,
            )
            for t in summary.by_tool
        ],
        daily=[
            DailyUsageResponse(
                date=d.date,
                token_count=d.token_count,
                message_count=d.message_count,
                tool_executions=d.tool_executions,
                estimated_cost_usd=d.estimated_cost_usd,
            )
            for d in summary.daily
        ],
    )


@router.get("/recent-executions", response_model=List[RecentExecutionResponse])
async def get_recent_executions(
    limit: int = Query(default=20, ge=1, le=100, description="Number of executions to return"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get recent tool executions.
    
    Admin sees all executions, regular users see their own.
    """
    user_id = None if current_user.role == "admin" else current_user.id
    
    executions = await usage_service.get_recent_executions(
        db=db,
        limit=limit,
        user_id=user_id,
    )
    
    return [RecentExecutionResponse(**e) for e in executions]


@router.get("/costs")
async def get_cost_breakdown(
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get cost breakdown with pricing details.
    
    Shows estimated costs and pricing used for calculations.
    """
    from app.services.usage_service import MODEL_PRICING, TOOL_PRICING
    
    user_id = None if current_user.role == "admin" else current_user.id
    
    summary = await usage_service.get_usage_summary(
        db=db,
        days=days,
        user_id=user_id,
    )
    
    # Build detailed cost breakdown
    model_costs = []
    for m in summary.by_model:
        pricing = MODEL_PRICING.get(m.model.split("-")[0], (1.0, 1.0))
        model_costs.append({
            "model": m.model,
            "tokens": m.total_tokens,
            "cost_usd": m.estimated_cost_usd,
            "pricing": {
                "input_per_1m": pricing[0],
                "output_per_1m": pricing[1],
            }
        })
    
    tool_costs = []
    for t in summary.by_tool:
        unit_price = TOOL_PRICING.get(t.tool_slug, 0.001)
        tool_costs.append({
            "tool": t.tool_name,
            "slug": t.tool_slug,
            "executions": t.execution_count,
            "cost_units": t.total_cost_units,
            "cost_usd": t.estimated_cost_usd,
            "pricing": {
                "per_unit": unit_price,
            }
        })
    
    return {
        "period_days": days,
        "total_cost_usd": summary.total_estimated_cost_usd,
        "llm_costs": {
            "total_tokens": summary.total_tokens,
            "total_cost_usd": sum(m.estimated_cost_usd for m in summary.by_model),
            "by_model": model_costs,
        },
        "tool_costs": {
            "total_executions": summary.total_tool_executions,
            "total_cost_usd": sum(t.estimated_cost_usd for t in summary.by_tool),
            "by_tool": tool_costs,
        },
        "pricing_reference": {
            "models": {k: {"input_per_1m": v[0], "output_per_1m": v[1]} for k, v in MODEL_PRICING.items()},
            "tools": TOOL_PRICING,
        }
    }


# -------------------------------------------------------------------------
# Pricing Status & Refresh Endpoints
# -------------------------------------------------------------------------

@router.get("/pricing")
async def get_pricing_status(
    current_user: User = Depends(get_current_user),
):
    """
    Get current model pricing and last refresh status.

    Returns the active MODEL_PRICING dict and metadata from the most
    recent automatic or manual pricing refresh.
    """
    from app.services.usage_service import MODEL_PRICING
    from app.services.pricing_update_service import get_last_pricing_update

    last = get_last_pricing_update()

    return {
        "models": {
            k: {"input_per_1m": v[0], "output_per_1m": v[1]}
            for k, v in MODEL_PRICING.items()
        },
        "last_refresh": last.to_dict() if last else None,
    }


@router.post("/pricing/refresh")
async def trigger_pricing_refresh(
    current_user: User = Depends(get_current_user),
):
    """
    Manually trigger a pricing refresh from OpenRouter.

    Admin only.  Fetches the latest per-token pricing and updates the
    in-memory MODEL_PRICING dictionary immediately.
    """
    if current_user.role != "admin":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin access required")

    from app.services.pricing_update_service import refresh_model_pricing

    result = await refresh_model_pricing()
    return result.to_dict()


# -------------------------------------------------------------------------
# Financial Dashboard Endpoint
# -------------------------------------------------------------------------

class CampaignFinancials(BaseModel):
    """Financial data for a single campaign."""
    id: str
    name: str
    status: str
    budget_allocated: float
    budget_spent: float
    revenue_generated: float
    profit_loss: float
    roi_percent: Optional[float]
    created_at: str
    last_activity_at: Optional[str]
    # Computed API cost breakdown for this campaign
    llm_cost: float = 0.0
    tool_cost: float = 0.0
    api_cost_total: float = 0.0  # llm_cost + tool_cost
    # Time series data for charts
    daily_data: List[dict]


class FinancialDashboard(BaseModel):
    """Comprehensive financial dashboard data."""
    # High-level rollups
    total_spent: float
    total_earned: float
    net_profit_loss: float
    is_profitable: bool
    
    # Bitcoin sats rollups (from bitcoin budget system)
    total_spent_sats: int = 0
    total_received_sats: int = 0
    net_sats: int = 0  # received - spent
    
    # Breakdown of spending
    spending_breakdown: dict
    
    # Campaign financials
    campaigns: List[CampaignFinancials]
    
    # Aggregate time series (all campaigns combined)
    daily_totals: List[dict]
    
    # Meta
    period_start: str
    period_end: str


@router.get("/financial-dashboard", response_model=FinancialDashboard)
async def get_financial_dashboard(
    days: int = Query(default=30, ge=0, le=3650, description="Number of days to include (0 = all time)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get comprehensive financial dashboard data.
    
    Returns:
    - Total spent (LLM costs + tool costs + campaign budgets spent)
    - Total earned (campaign revenue)
    - Profit/loss status
    - Per-campaign financial breakdown with daily time series
    - Aggregate daily totals
    """
    from app.models import ToolExecution, ToolExecutionStatus, LLMUsage
    from app.models.agent_scheduler import AgentRun
    from app.services.usage_service import MODEL_PRICING, TOOL_PRICING
    from sqlalchemy.orm import joinedload
    
    # Determine date range
    end_date = utc_now()
    if days == 0:
        # All time — use a very early date
        start_date = datetime(2020, 1, 1)
    else:
        start_date = end_date - timedelta(days=days)
    
    user_id = None if current_user.role == "admin" else current_user.id
    
    # -------------------------------------------------------------------------
    # 1. Get LLM costs from llm_usage table (single source of truth)
    # -------------------------------------------------------------------------
    llm_query = select(
        func.coalesce(func.sum(LLMUsage.cost_usd), 0).label('total_cost'),
        func.coalesce(func.sum(LLMUsage.total_tokens), 0).label('total_tokens')
    ).where(
        LLMUsage.created_at >= start_date
    )
    if user_id:
        llm_query = llm_query.where(LLMUsage.user_id == user_id)
    
    llm_result = await db.execute(llm_query)
    llm_row = llm_result.fetchone()
    llm_cost = float(llm_row.total_cost or 0)
    
    # -------------------------------------------------------------------------
    # 2. Get tool execution costs (per-tool pricing)
    # -------------------------------------------------------------------------
    from app.models import Tool
    tool_query = select(
        Tool.slug,
        func.coalesce(func.sum(ToolExecution.cost_units), 0).label('total_units')
    ).join(
        Tool, ToolExecution.tool_id == Tool.id
    ).where(
        and_(
            ToolExecution.created_at >= start_date,
            ToolExecution.status == ToolExecutionStatus.COMPLETED
        )
    ).group_by(Tool.slug)
    if user_id:
        tool_query = tool_query.where(ToolExecution.triggered_by_user_id == user_id)
    
    tool_result = await db.execute(tool_query)
    tool_rows = tool_result.all()
    tool_cost = sum(
        float(row.total_units or 0) * TOOL_PRICING.get(row.slug, 0.001)
        for row in tool_rows
    )
    
    # -------------------------------------------------------------------------
    # 2b. Get agent run costs (from autonomous agents like Tool Scout)
    # -------------------------------------------------------------------------
    agent_query = select(
        func.coalesce(func.sum(AgentRun.cost_usd), 0).label('total_cost')
    ).where(
        AgentRun.created_at >= start_date
    )
    # Note: AgentRun doesn't have user_id - agents run globally
    
    agent_result = await db.execute(agent_query)
    agent_row = agent_result.fetchone()
    agent_cost = float(agent_row.total_cost or 0)
    
    # -------------------------------------------------------------------------
    # 3. Get campaign data
    # -------------------------------------------------------------------------
    from app.models import Campaign, Proposal
    
    campaign_query = select(Campaign).options(
        joinedload(Campaign.proposal)
    ).where(
        Campaign.created_at >= start_date
    )
    if user_id:
        campaign_query = campaign_query.where(Campaign.user_id == user_id)
    
    campaign_result = await db.execute(campaign_query)
    campaigns = campaign_result.scalars().unique().all()
    
    campaign_financials = []
    total_campaign_spent = 0.0
    total_campaign_revenue = 0.0
    
    # Batch-query per-campaign LLM costs from llm_usage
    campaign_ids = [camp.id for camp in campaigns]
    per_campaign_llm = {}
    per_campaign_tool = {}
    
    if campaign_ids:
        # LLM costs grouped by campaign
        camp_llm_query = select(
            LLMUsage.campaign_id,
            func.coalesce(func.sum(LLMUsage.cost_usd), 0).label('total_cost')
        ).where(
            and_(
                LLMUsage.campaign_id.in_(campaign_ids),
                LLMUsage.created_at >= start_date,
            )
        ).group_by(LLMUsage.campaign_id)
        camp_llm_result = await db.execute(camp_llm_query)
        per_campaign_llm = {row.campaign_id: float(row.total_cost or 0) for row in camp_llm_result.all()}
        
        # Tool costs grouped by campaign (per-slug pricing)
        camp_tool_query = select(
            ToolExecution.campaign_id,
            Tool.slug,
            func.coalesce(func.sum(ToolExecution.cost_units), 0).label('total_units')
        ).join(
            Tool, ToolExecution.tool_id == Tool.id
        ).where(
            and_(
                ToolExecution.campaign_id.in_(campaign_ids),
                ToolExecution.created_at >= start_date,
                ToolExecution.status == ToolExecutionStatus.COMPLETED,
            )
        ).group_by(ToolExecution.campaign_id, Tool.slug)
        camp_tool_result = await db.execute(camp_tool_query)
        for row in camp_tool_result.all():
            cost = float(row.total_units or 0) * TOOL_PRICING.get(row.slug, 0.001)
            per_campaign_tool[row.campaign_id] = per_campaign_tool.get(row.campaign_id, 0.0) + cost
    
    for camp in campaigns:
        spent = float(camp.budget_spent or 0)
        revenue = float(camp.revenue_generated or 0)
        camp_llm_cost = per_campaign_llm.get(camp.id, 0.0)
        camp_tool_cost = per_campaign_tool.get(camp.id, 0.0)
        api_cost_total = camp_llm_cost + camp_tool_cost
        profit = revenue - spent
        
        total_campaign_spent += spent
        total_campaign_revenue += revenue
        
        # Calculate ROI
        roi = None
        if spent > 0:
            roi = round((profit / spent) * 100, 2)
        
        # Get daily data for this campaign
        # For now, generate daily data from the campaign's period
        camp_days = []
        camp_start = camp.created_at.date() if camp.created_at else start_date.date()
        camp_end = min(end_date.date(), (camp.end_date.date() if camp.end_date else end_date.date()))
        
        current = camp_start
        # Distribute budget spent evenly for visualization (simplified)
        days_active = max((camp_end - camp_start).days + 1, 1)
        daily_spend = spent / days_active
        daily_revenue = revenue / days_active
        
        while current <= camp_end:
            camp_days.append({
                "date": current.isoformat(),
                "spent": round(daily_spend, 4),
                "revenue": round(daily_revenue, 4),
                "cumulative_spent": round(daily_spend * ((current - camp_start).days + 1), 4),
                "cumulative_revenue": round(daily_revenue * ((current - camp_start).days + 1), 4),
            })
            current += timedelta(days=1)
        
        campaign_financials.append(CampaignFinancials(
            id=str(camp.id),
            name=camp.proposal.title if camp.proposal else f"Campaign {str(camp.id)[:8]}",
            status=camp.status.value if hasattr(camp.status, 'value') else str(camp.status),
            budget_allocated=float(camp.budget_allocated or 0),
            budget_spent=spent,
            revenue_generated=revenue,
            profit_loss=profit,
            roi_percent=roi,
            llm_cost=round(camp_llm_cost, 4),
            tool_cost=round(camp_tool_cost, 4),
            api_cost_total=round(api_cost_total, 4),
            created_at=camp.created_at.isoformat() if camp.created_at else "",
            last_activity_at=camp.last_activity_at.isoformat() if camp.last_activity_at else None,
            daily_data=camp_days,
        ))
    
    # -------------------------------------------------------------------------
    # 4. Calculate totals
    # -------------------------------------------------------------------------
    # Total spent = LLM costs + Tool costs + Agent costs + Campaign budget spent
    total_spent = llm_cost + tool_cost + agent_cost + total_campaign_spent
    total_earned = total_campaign_revenue
    net_profit_loss = total_earned - total_spent
    
    # -------------------------------------------------------------------------
    # 4b. Bitcoin sats totals (from campaign columns)
    # -------------------------------------------------------------------------
    sats_spent_query = select(
        func.coalesce(func.sum(Campaign.bitcoin_spent_sats), 0).label('spent'),
        func.coalesce(func.sum(Campaign.bitcoin_received_sats), 0).label('received'),
    ).where(
        Campaign.created_at >= start_date
    )
    if user_id:
        sats_spent_query = sats_spent_query.where(Campaign.user_id == user_id)
    
    sats_result = await db.execute(sats_spent_query)
    sats_row = sats_result.fetchone()
    total_spent_sats = int(sats_row.spent or 0)
    total_received_sats = int(sats_row.received or 0)
    
    # Also count pending approvals as "pending sats"
    from app.models.bitcoin_budget import BitcoinSpendApproval, SpendApprovalStatus
    pending_query = select(
        func.coalesce(func.sum(BitcoinSpendApproval.amount_sats), 0).label('pending')
    ).where(
        and_(
            BitcoinSpendApproval.status == SpendApprovalStatus.PENDING,
            BitcoinSpendApproval.created_at >= start_date,
        )
    )
    if user_id:
        pending_query = pending_query.where(BitcoinSpendApproval.requested_by_id == user_id)
    pending_result = await db.execute(pending_query)
    pending_row = pending_result.fetchone()
    total_pending_sats = int(pending_row.pending or 0)
    
    # -------------------------------------------------------------------------
    # 5. Generate aggregate daily totals
    # -------------------------------------------------------------------------
    daily_totals = []
    current = start_date.date()
    while current <= end_date.date():
        day_spent = 0.0
        day_revenue = 0.0
        
        for cf in campaign_financials:
            for dd in cf.daily_data:
                if dd["date"] == current.isoformat():
                    day_spent += dd["spent"]
                    day_revenue += dd["revenue"]
        
        # Add estimated API costs (distribute evenly)
        effective_days = max((end_date.date() - start_date.date()).days, 1)
        api_cost_per_day = (llm_cost + tool_cost) / effective_days
        day_spent += api_cost_per_day
        
        daily_totals.append({
            "date": current.isoformat(),
            "spent": round(day_spent, 4),
            "revenue": round(day_revenue, 4),
            "profit_loss": round(day_revenue - day_spent, 4),
            "cumulative_spent": round(sum(d["spent"] for d in daily_totals) + day_spent, 4),
            "cumulative_revenue": round(sum(d["revenue"] for d in daily_totals) + day_revenue, 4),
        })
        current += timedelta(days=1)
    
    # Recalculate cumulative values
    cum_spent = 0.0
    cum_revenue = 0.0
    for d in daily_totals:
        cum_spent += d["spent"]
        cum_revenue += d["revenue"]
        d["cumulative_spent"] = round(cum_spent, 4)
        d["cumulative_revenue"] = round(cum_revenue, 4)
        d["cumulative_profit_loss"] = round(cum_revenue - cum_spent, 4)
    
    return FinancialDashboard(
        total_spent=round(total_spent, 2),
        total_earned=round(total_earned, 2),
        net_profit_loss=round(net_profit_loss, 2),
        is_profitable=net_profit_loss >= 0,
        total_spent_sats=total_spent_sats,
        total_received_sats=total_received_sats,
        net_sats=total_received_sats - total_spent_sats,
        spending_breakdown={
            "llm_costs": round(llm_cost, 2),
            "tool_costs": round(tool_cost, 2),
            "agent_costs": round(agent_cost, 2),
            "campaign_budgets": round(total_campaign_spent, 2),
        },
        campaigns=campaign_financials,
        daily_totals=daily_totals,
        period_start=start_date.isoformat(),
        period_end=end_date.isoformat(),
    )
