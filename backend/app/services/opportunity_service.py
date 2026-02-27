"""Service layer for Opportunity Scout operations."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select, func, text, and_, or_, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Opportunity,
    OpportunityStatus,
    OpportunityType,
    RankingTier,
    DiscoveryStrategy,
    StrategyStatus,
    StrategyOutcome,
    AgentInsight,
    InsightType,
    MemorySummary,
    SummaryType,
    UserScoutSettings,
    ScoringRubric,
    Proposal,
    ProposalStatus,
)

logger = logging.getLogger(__name__)


class OpportunityService:
    """
    Service for managing opportunities and the Opportunity Scout system.
    
    Handles:
    - CRUD operations for opportunities
    - Hopper status and capacity management
    - Bulk operations (dismiss, archive)
    - User settings
    - Scoring rubric management
    """
    
    # ==========================================================================
    # Opportunity CRUD
    # ==========================================================================
    
    async def get_opportunity(
        self,
        db: AsyncSession,
        opportunity_id: UUID,
    ) -> Optional[Opportunity]:
        """Get a single opportunity by ID."""
        return await db.get(Opportunity, opportunity_id)
    
    async def get_opportunities(
        self,
        db: AsyncSession,
        status: Optional[OpportunityStatus] = None,
        tier: Optional[RankingTier] = None,
        opportunity_type: Optional[OpportunityType] = None,
        skip: int = 0,
        limit: int = 50,
        include_dismissed: bool = False,
        search: Optional[str] = None,
    ) -> Tuple[List[Opportunity], int]:
        """
        Get opportunities with filtering and pagination.
        
        Returns tuple of (opportunities, total_count).
        """
        # Build base query
        query = select(Opportunity)
        count_query = select(func.count(Opportunity.id))
        
        # Apply filters
        conditions = []
        
        if status:
            conditions.append(Opportunity.status == status)
        
        if tier:
            conditions.append(Opportunity.ranking_tier == tier)
        
        if opportunity_type:
            conditions.append(Opportunity.opportunity_type == opportunity_type)
        
        if not include_dismissed:
            conditions.append(Opportunity.status != OpportunityStatus.DISMISSED)
        
        if search:
            search_term = f"%{search}%"
            conditions.append(
                or_(
                    Opportunity.title.ilike(search_term),
                    Opportunity.summary.ilike(search_term),
                )
            )
        
        if conditions:
            query = query.where(and_(*conditions))
            count_query = count_query.where(and_(*conditions))
        
        # Get total count
        total = (await db.execute(count_query)).scalar() or 0
        
        # Apply ordering - by rank position, then by score, then by time sensitivity
        query = query.order_by(
            Opportunity.rank_position.asc().nullslast(),
            Opportunity.overall_score.desc().nullslast(),
            Opportunity.time_sensitivity.asc().nullslast(),
            Opportunity.discovered_at.desc(),
        )
        
        # Apply pagination
        query = query.offset(skip).limit(limit)
        
        result = await db.execute(query)
        opportunities = list(result.scalars().all())
        
        return opportunities, total
    
    async def get_opportunities_by_tier(
        self,
        db: AsyncSession,
    ) -> Dict[str, List[Opportunity]]:
        """Get all opportunities grouped by ranking tier."""
        query = select(Opportunity).where(
            Opportunity.status.in_([
                OpportunityStatus.EVALUATED,
                OpportunityStatus.PRESENTED,
            ])
        ).order_by(
            Opportunity.rank_position.asc().nullslast(),
            Opportunity.overall_score.desc().nullslast(),
        )
        
        result = await db.execute(query)
        opportunities = list(result.scalars().all())
        
        grouped = {
            "top_pick": [],
            "promising": [],
            "maybe": [],
            "unlikely": [],
            "unranked": [],
        }
        
        for opp in opportunities:
            if opp.ranking_tier == RankingTier.TOP_PICK:
                grouped["top_pick"].append(opp)
            elif opp.ranking_tier == RankingTier.PROMISING:
                grouped["promising"].append(opp)
            elif opp.ranking_tier == RankingTier.MAYBE:
                grouped["maybe"].append(opp)
            elif opp.ranking_tier == RankingTier.UNLIKELY:
                grouped["unlikely"].append(opp)
            else:
                grouped["unranked"].append(opp)
        
        return grouped
    
    # ==========================================================================
    # User Actions
    # ==========================================================================
    
    async def approve_opportunity(
        self,
        db: AsyncSession,
        opportunity_id: UUID,
        user_notes: Optional[str] = None,
    ) -> Optional[Opportunity]:
        """Approve an opportunity to become a proposal."""
        opportunity = await self.get_opportunity(db, opportunity_id)
        if not opportunity:
            return None
        
        opportunity.status = OpportunityStatus.APPROVED
        opportunity.user_decision = "approved"
        opportunity.user_feedback = user_notes
        opportunity.decision_made_at = utc_now()
        
        # Update strategy statistics
        if opportunity.discovery_strategy_id:
            strategy = await db.get(DiscoveryStrategy, opportunity.discovery_strategy_id)
            if strategy:
                strategy.opportunities_approved += 1
                # Update effectiveness score
                if strategy.opportunities_found > 0:
                    strategy.effectiveness_score = (
                        strategy.opportunities_approved / strategy.opportunities_found
                    )
        
        await db.commit()
        await db.refresh(opportunity)
        
        # Create event to trigger Proposal Writer agent
        await self._create_approval_event(db, opportunity)
        
        return opportunity
    
    async def _create_approval_event(
        self,
        db: AsyncSession,
        opportunity: Opportunity,
    ) -> None:
        """Create an agent event when an opportunity is approved."""
        from app.services.agent_scheduler_service import agent_scheduler_service
        
        await agent_scheduler_service.create_event(
            db,
            event_type="opportunity.approved",
            source_type="opportunity",
            source_id=opportunity.id,
            target_agent_slug="proposal_writer",
            event_data={
                "opportunity_id": str(opportunity.id),
                "title": opportunity.title,
                "summary": opportunity.summary,
                "type": opportunity.opportunity_type.value if opportunity.opportunity_type else None,
                "score": opportunity.overall_score,
                "tier": opportunity.ranking_tier.value if opportunity.ranking_tier else None,
            },
        )
    
    async def dismiss_opportunity(
        self,
        db: AsyncSession,
        opportunity_id: UUID,
        reason: Optional[str] = None,
    ) -> Optional[Opportunity]:
        """Dismiss an opportunity."""
        opportunity = await self.get_opportunity(db, opportunity_id)
        if not opportunity:
            return None
        
        opportunity.status = OpportunityStatus.DISMISSED
        opportunity.user_decision = "dismissed"
        opportunity.user_feedback = reason
        opportunity.decision_made_at = utc_now()
        
        await db.commit()
        await db.refresh(opportunity)
        
        return opportunity
    
    async def bulk_dismiss(
        self,
        db: AsyncSession,
        opportunity_ids: Optional[List[UUID]] = None,
        tier: Optional[RankingTier] = None,
        below_score: Optional[float] = None,
        reason: Optional[str] = None,
    ) -> int:
        """
        Bulk dismiss opportunities.
        
        Can dismiss by:
        - Specific IDs
        - All in a tier
        - All below a score threshold
        
        Returns count of dismissed opportunities.
        """
        # Build conditions
        conditions = [
            Opportunity.status.in_([
                OpportunityStatus.DISCOVERED,
                OpportunityStatus.RESEARCHING,
                OpportunityStatus.EVALUATED,
                OpportunityStatus.PRESENTED,
            ])
        ]
        
        if opportunity_ids:
            conditions.append(Opportunity.id.in_(opportunity_ids))
        
        if tier:
            conditions.append(Opportunity.ranking_tier == tier)
        
        if below_score is not None:
            conditions.append(
                or_(
                    Opportunity.overall_score < below_score,
                    Opportunity.overall_score.is_(None),
                )
            )
        
        # Update opportunities
        stmt = (
            update(Opportunity)
            .where(and_(*conditions))
            .values(
                status=OpportunityStatus.DISMISSED,
                user_decision="bulk_dismissed",
                user_feedback=reason or f"Bulk dismissed: tier={tier}, below_score={below_score}",
            )
        )
        
        result = await db.execute(stmt)
        await db.commit()
        
        return result.rowcount
    
    async def research_more(
        self,
        db: AsyncSession,
        opportunity_id: UUID,
        research_questions: Optional[List[str]] = None,
    ) -> Optional[Opportunity]:
        """Mark an opportunity for additional research."""
        opportunity = await self.get_opportunity(db, opportunity_id)
        if not opportunity:
            return None
        
        opportunity.status = OpportunityStatus.RESEARCHING
        
        # Store research questions in metadata
        if research_questions:
            current_meta = opportunity.raw_signal or ""
            opportunity.raw_signal = f"{current_meta}\n\nResearch Questions:\n" + "\n".join(
                f"- {q}" for q in research_questions
            )
        
        await db.commit()
        await db.refresh(opportunity)
        
        return opportunity
    
    # ==========================================================================
    # Hopper Management
    # ==========================================================================
    
    async def get_hopper_status(
        self,
        db: AsyncSession,
        user_id: Optional[UUID] = None,
    ) -> Dict[str, Any]:
        """
        Get the current hopper status.
        
        Returns capacity info and warning status.
        """
        # Get user settings
        settings = await self.get_user_settings(db, user_id)
        max_capacity = settings.max_active_proposals if settings else 10
        warning_threshold = settings.hopper_warning_threshold if settings else 8
        
        # Count active proposals (all proposals that will need review/attention)
        # This includes:
        # - DRAFT_FROM_SCOUT: Being refined by AI, will need review soon
        # - PENDING: Ready for user review
        # - UNDER_REVIEW: Currently being reviewed
        # - APPROVED: Approved but not yet converted to campaign
        active_query = select(func.count(Proposal.id)).where(
            Proposal.status.in_([
                ProposalStatus.DRAFT_FROM_SCOUT,
                ProposalStatus.PENDING,
                ProposalStatus.UNDER_REVIEW,
                ProposalStatus.APPROVED,
            ])
        )
        active_count = (await db.execute(active_query)).scalar() or 0
        
        # Count pending opportunities (approved but not yet have proposals)
        # Only count those without a proposal_id to avoid double-counting
        pending_query = select(func.count(Opportunity.id)).where(
            and_(
                Opportunity.status == OpportunityStatus.APPROVED,
                Opportunity.proposal_id.is_(None),  # No proposal created yet
            )
        )
        pending_count = (await db.execute(pending_query)).scalar() or 0
        
        # Calculate available slots
        total_committed = active_count + pending_count
        available_slots = max(0, max_capacity - total_committed)
        
        # Determine status
        if total_committed >= max_capacity:
            hopper_status = "full"
        elif total_committed >= warning_threshold:
            hopper_status = "warning"
        else:
            hopper_status = "available"
        
        return {
            "max_capacity": max_capacity,
            "active_proposals": active_count,
            "pending_approvals": pending_count,
            "total_committed": total_committed,
            "available_slots": available_slots,
            "status": hopper_status,
            "can_accept_more": available_slots > 0,
        }
    
    # ==========================================================================
    # Strategy Management
    # ==========================================================================
    
    async def get_strategies(
        self,
        db: AsyncSession,
        status: Optional[StrategyStatus] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[DiscoveryStrategy], int]:
        """Get discovery strategies with optional filtering."""
        query = select(DiscoveryStrategy)
        count_query = select(func.count(DiscoveryStrategy.id))
        
        if status:
            query = query.where(DiscoveryStrategy.status == status)
            count_query = count_query.where(DiscoveryStrategy.status == status)
        
        total = (await db.execute(count_query)).scalar() or 0
        
        query = query.order_by(
            DiscoveryStrategy.effectiveness_score.desc().nullslast(),
            DiscoveryStrategy.created_at.desc(),
        ).offset(skip).limit(limit)
        
        result = await db.execute(query)
        strategies = list(result.scalars().all())
        
        return strategies, total
    
    async def pause_strategy(
        self,
        db: AsyncSession,
        strategy_id: UUID,
    ) -> Optional[DiscoveryStrategy]:
        """Pause a discovery strategy."""
        strategy = await db.get(DiscoveryStrategy, strategy_id)
        if strategy:
            strategy.status = StrategyStatus.PAUSED
            await db.commit()
            await db.refresh(strategy)
        return strategy
    
    async def activate_strategy(
        self,
        db: AsyncSession,
        strategy_id: UUID,
    ) -> Optional[DiscoveryStrategy]:
        """Activate a paused or deprecated strategy."""
        strategy = await db.get(DiscoveryStrategy, strategy_id)
        if strategy:
            strategy.status = StrategyStatus.ACTIVE
            await db.commit()
            await db.refresh(strategy)
        return strategy
    
    async def deprecate_strategy(
        self,
        db: AsyncSession,
        strategy_id: UUID,
        reason: Optional[str] = None,
    ) -> Optional[DiscoveryStrategy]:
        """Deprecate a strategy so it won't be used again."""
        strategy = await db.get(DiscoveryStrategy, strategy_id)
        if strategy:
            strategy.status = StrategyStatus.DEPRECATED
            if reason:
                strategy.agent_notes = f"{strategy.agent_notes or ''}\nDeprecated: {reason}"
            await db.commit()
            await db.refresh(strategy)
        return strategy
    
    # ==========================================================================
    # Insights
    # ==========================================================================
    
    async def get_insights(
        self,
        db: AsyncSession,
        insight_type: Optional[InsightType] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[AgentInsight], int]:
        """Get agent insights with optional filtering."""
        query = select(AgentInsight)
        count_query = select(func.count(AgentInsight.id))
        
        if insight_type:
            query = query.where(AgentInsight.insight_type == insight_type)
            count_query = count_query.where(AgentInsight.insight_type == insight_type)
        
        total = (await db.execute(count_query)).scalar() or 0
        
        query = query.order_by(
            AgentInsight.confidence.desc(),
            AgentInsight.created_at.desc(),
        ).offset(skip).limit(limit)
        
        result = await db.execute(query)
        insights = list(result.scalars().all())
        
        return insights, total
    
    async def validate_insight(
        self,
        db: AsyncSession,
        insight_id: UUID,
        is_validated: bool,
        validation_notes: Optional[str] = None,
    ) -> Optional[AgentInsight]:
        """User validates or invalidates an insight."""
        insight = await db.get(AgentInsight, insight_id)
        if insight:
            insight.validated = is_validated
            insight.validation_notes = validation_notes
            # Adjust confidence based on validation
            if is_validated:
                insight.confidence = min(1.0, insight.confidence + 0.1)
            else:
                insight.confidence = max(0.0, insight.confidence - 0.2)
            await db.commit()
            await db.refresh(insight)
        return insight
    
    # ==========================================================================
    # User Settings
    # ==========================================================================
    
    async def get_user_settings(
        self,
        db: AsyncSession,
        user_id: Optional[UUID] = None,
    ) -> Optional[UserScoutSettings]:
        """Get user's scout settings, or global defaults if no user specified."""
        query = select(UserScoutSettings)
        if user_id:
            query = query.where(UserScoutSettings.user_id == user_id)
        else:
            # Global defaults (user_id IS NULL)
            query = query.where(UserScoutSettings.user_id.is_(None))
        
        result = await db.execute(query)
        return result.scalar_one_or_none()
    
    async def count_unreviewed_opportunities(
        self,
        db: AsyncSession,
    ) -> int:
        """Count opportunities that haven't been reviewed by the user yet.
        
        Unreviewed = discovered, researching, or evaluated (not yet acted on by user).
        """
        unreviewed_statuses = [
            OpportunityStatus.DISCOVERED,
            OpportunityStatus.RESEARCHING,
            OpportunityStatus.EVALUATED,
            OpportunityStatus.PRESENTED,
        ]
        query = select(func.count(Opportunity.id)).where(
            Opportunity.status.in_(unreviewed_statuses)
        )
        result = await db.execute(query)
        return result.scalar() or 0
    
    async def find_duplicate_opportunity(
        self,
        db: AsyncSession,
        title: str,
        source_urls: Optional[list] = None,
        similarity_threshold: float = 0.6,
    ) -> Optional[Opportunity]:
        """Check if a similar opportunity already exists.

        Uses two checks (either match = duplicate):
          1. Exact URL match — any URL in source_urls already present in an
             existing opportunity's source_urls JSONB array.
          2. Trigram title similarity (pg_trgm) above the given threshold.

        Returns the first matching Opportunity, or None.
        """
        # --- 1. URL exact match ---
        if source_urls:
            for url in source_urls:
                if not url:
                    continue
                # source_urls is a JSONB array; use @> containment with safe params
                url_query = (
                    select(Opportunity)
                    .where(text("source_urls @> cast(:url_array as jsonb)"))
                    .params(url_array=json.dumps([url]))
                    .limit(1)
                )
                result = await db.execute(url_query)
                existing = result.scalar_one_or_none()
                if existing:
                    logger.debug(
                        "Duplicate URL match: '%s' already in opportunity '%s'",
                        url, existing.title,
                    )
                    return existing

        # --- 2. Trigram title similarity ---
        if title:
            trgm_query = (
                select(Opportunity)
                .where(
                    text("similarity(opportunities.title, :new_title) > :threshold")
                )
                .order_by(text("similarity(opportunities.title, :new_title) DESC"))
                .params(new_title=title, threshold=similarity_threshold)
                .limit(1)
            )
            result = await db.execute(trgm_query)
            existing = result.scalar_one_or_none()
            if existing:
                logger.debug(
                    "Duplicate title match (trigram): new='%s' ~ existing='%s'",
                    title, existing.title,
                )
                return existing

        return None

    async def get_any_user_settings(
        self,
        db: AsyncSession,
    ) -> Optional[UserScoutSettings]:
        """Get the first available scout settings (for use in scheduled tasks without user context)."""
        query = select(UserScoutSettings).limit(1)
        result = await db.execute(query)
        return result.scalar_one_or_none()
    
    async def update_user_settings(
        self,
        db: AsyncSession,
        user_id: Optional[UUID] = None,
        max_active_proposals: Optional[int] = None,
        hopper_warning_threshold: Optional[int] = None,
        auto_pause_discovery: Optional[bool] = None,
        max_backlog_size: Optional[int] = None,
        auto_dismiss_below_score: Optional[float] = None,
        auto_dismiss_types: Optional[List[str]] = None,
        default_sort: Optional[str] = None,
        show_unlikely_tier: Optional[bool] = None,
        preferred_types: Optional[List[str]] = None,
        preferred_domains: Optional[List[str]] = None,
        excluded_types: Optional[List[str]] = None,
        excluded_keywords: Optional[List[str]] = None,
        custom_rubric_weights: Optional[Dict[str, float]] = None,
    ) -> UserScoutSettings:
        """Update or create user scout settings."""
        settings = await self.get_user_settings(db, user_id)
        
        if not settings:
            settings = UserScoutSettings(user_id=user_id)
            db.add(settings)
        
        if max_active_proposals is not None:
            settings.max_active_proposals = max_active_proposals
        if hopper_warning_threshold is not None:
            settings.hopper_warning_threshold = hopper_warning_threshold
        if auto_pause_discovery is not None:
            settings.auto_pause_discovery = auto_pause_discovery
        if max_backlog_size is not None:
            settings.max_backlog_size = max_backlog_size
        if auto_dismiss_below_score is not None:
            settings.auto_dismiss_below_score = auto_dismiss_below_score
        if auto_dismiss_types is not None:
            settings.auto_dismiss_types = auto_dismiss_types
        if default_sort is not None:
            settings.default_sort = default_sort
        if show_unlikely_tier is not None:
            settings.show_unlikely_tier = show_unlikely_tier
        if preferred_types is not None:
            settings.preferred_types = preferred_types
        if preferred_domains is not None:
            settings.preferred_domains = preferred_domains
        if excluded_types is not None:
            settings.excluded_types = excluded_types
        if excluded_keywords is not None:
            settings.excluded_keywords = excluded_keywords
        if custom_rubric_weights is not None:
            settings.custom_rubric_weights = custom_rubric_weights
        
        await db.commit()
        await db.refresh(settings)
        
        return settings
    
    # ==========================================================================
    # Scoring Rubric
    # ==========================================================================
    
    async def get_active_rubric(
        self,
        db: AsyncSession,
    ) -> Optional[ScoringRubric]:
        """Get the currently active scoring rubric."""
        query = select(ScoringRubric).where(ScoringRubric.is_active == True)
        result = await db.execute(query)
        return result.scalar_one_or_none()
    
    async def create_rubric(
        self,
        db: AsyncSession,
        name: str,
        factors: Dict[str, Any],
        description: Optional[str] = None,
        activate: bool = True,
    ) -> ScoringRubric:
        """Create a new scoring rubric."""
        if activate:
            # Deactivate existing rubrics
            await db.execute(
                update(ScoringRubric).values(is_active=False)
            )
        
        rubric = ScoringRubric(
            name=name,
            description=description,
            factors=factors,
            is_active=activate,
        )
        db.add(rubric)
        await db.commit()
        await db.refresh(rubric)
        
        return rubric
    
    async def update_rubric_factor(
        self,
        db: AsyncSession,
        rubric_id: UUID,
        factor_name: str,
        weight: Optional[float] = None,
        description: Optional[str] = None,
    ) -> Optional[ScoringRubric]:
        """Update a single factor in the rubric."""
        rubric = await db.get(ScoringRubric, rubric_id)
        if not rubric:
            return None
        
        factors = rubric.factors.copy()
        if factor_name not in factors:
            factors[factor_name] = {}
        
        if weight is not None:
            factors[factor_name]["weight"] = weight
        if description is not None:
            factors[factor_name]["description"] = description
        
        rubric.factors = factors
        rubric.version += 1
        
        await db.commit()
        await db.refresh(rubric)
        
        return rubric
    
    # ==========================================================================
    # Statistics & Analytics
    # ==========================================================================
    
    async def get_pipeline_stats(
        self,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        """Get pipeline funnel statistics for the dashboard."""
        from app.models import Proposal, ProposalStatus, Campaign
        
        # Count opportunities by status
        opp_query = select(
            Opportunity.status,
            func.count(Opportunity.id)
        ).group_by(Opportunity.status)
        opp_result = await db.execute(opp_query)
        opp_counts = {row[0].value: row[1] for row in opp_result.all()}
        
        # Count proposals by status
        prop_query = select(
            Proposal.status,
            func.count(Proposal.id)
        ).group_by(Proposal.status)
        prop_result = await db.execute(prop_query)
        prop_counts = {row[0].value: row[1] for row in prop_result.all()}
        
        # Count campaigns (if table exists)
        try:
            campaign_query = select(func.count(Campaign.id))
            campaign_count = (await db.execute(campaign_query)).scalar() or 0
        except Exception:
            campaign_count = 0
        
        # Build funnel stages
        discovered = opp_counts.get("discovered", 0)
        evaluated = opp_counts.get("evaluated", 0)
        approved = opp_counts.get("approved", 0)
        
        # Proposals in review/pending
        proposals_pending = (
            prop_counts.get("pending", 0) + 
            prop_counts.get("under_review", 0) +
            prop_counts.get("draft_from_scout", 0)
        )
        proposals_approved = prop_counts.get("approved", 0)
        
        return {
            "stages": [
                {"name": "Discovered", "count": discovered, "color": "gray"},
                {"name": "Evaluated", "count": evaluated, "color": "blue"},
                {"name": "Approved", "count": approved, "color": "cyan"},
                {"name": "Proposals", "count": proposals_pending + proposals_approved, "color": "purple"},
                {"name": "Campaigns", "count": campaign_count, "color": "green"},
            ],
            "totals": {
                "opportunities": discovered + evaluated + approved,
                "proposals": sum(prop_counts.values()),
                "campaigns": campaign_count,
            }
        }
    
    async def get_scout_statistics(
        self,
        db: AsyncSession,
        days: int = 30,
    ) -> Dict[str, Any]:
        """Get overall statistics for the Opportunity Scout system."""
        since = utc_now() - timedelta(days=days)
        
        # Opportunities stats
        opp_stats_query = select(
            func.count(Opportunity.id).label("total"),
            func.count(Opportunity.id).filter(
                Opportunity.status == OpportunityStatus.APPROVED
            ).label("approved"),
            func.count(Opportunity.id).filter(
                Opportunity.status == OpportunityStatus.DISMISSED
            ).label("dismissed"),
            func.avg(Opportunity.overall_score).label("avg_score"),
        ).where(Opportunity.discovered_at >= since)
        
        opp_result = (await db.execute(opp_stats_query)).one()
        
        # Strategy stats
        strategy_stats_query = select(
            func.count(DiscoveryStrategy.id).label("total"),
            func.count(DiscoveryStrategy.id).filter(
                DiscoveryStrategy.status == StrategyStatus.ACTIVE
            ).label("active"),
            func.avg(DiscoveryStrategy.effectiveness_score).label("avg_effectiveness"),
        )
        
        strategy_result = (await db.execute(strategy_stats_query)).one()
        
        # Recent outcomes
        outcomes_query = select(func.count(StrategyOutcome.id)).where(
            StrategyOutcome.executed_at >= since
        )
        outcomes_count = (await db.execute(outcomes_query)).scalar() or 0
        
        # Insights count
        insights_query = select(func.count(AgentInsight.id))
        insights_count = (await db.execute(insights_query)).scalar() or 0
        
        return {
            "period_days": days,
            "opportunities": {
                "total": opp_result.total or 0,
                "approved": opp_result.approved or 0,
                "dismissed": opp_result.dismissed or 0,
                "approval_rate": (
                    (opp_result.approved or 0) / opp_result.total
                    if opp_result.total else 0
                ),
                "avg_score": float(opp_result.avg_score or 0),
            },
            "strategies": {
                "total": strategy_result.total or 0,
                "active": strategy_result.active or 0,
                "avg_effectiveness": float(strategy_result.avg_effectiveness or 0),
            },
            "discovery_runs": outcomes_count,
            "insights_count": insights_count,
        }


# Singleton instance
opportunity_service = OpportunityService()
