from fastapi import APIRouter

from app.api.endpoints import auth, users, proposals, campaigns, conversations, admin, tools, resources, test_resources, agents, usage, opportunities, brainstorm, ideas, agent_scheduler, broker, tasks, notifications, campaign_learning, rate_limits, approvals, tool_health, analytics, wallet, bitcoin_budget, cold_storage, disclaimer, media_library


from app.core.config import settings


api_router = APIRouter()

# Include all endpoint routers
api_router.include_router(auth.router, prefix="/auth", tags=["Authentication"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(admin.router, prefix="/admin", tags=["Admin"])
api_router.include_router(proposals.router, prefix="/proposals", tags=["Proposals"])
api_router.include_router(campaigns.router, prefix="/campaigns", tags=["Campaigns"])
api_router.include_router(campaign_learning.router, prefix="/learning", tags=["Campaign Learning"])
api_router.include_router(conversations.router, prefix="/conversations", tags=["Conversations"])
api_router.include_router(tools.router, prefix="/tools", tags=["Tools"])
api_router.include_router(tool_health.router, tags=["Tool Health"])
api_router.include_router(resources.router, prefix="/resources", tags=["Resources"])
# Test endpoints only available in non-production environments
if settings.environment != "production":
    api_router.include_router(test_resources.router, prefix="/test/resources", tags=["Testing"])
api_router.include_router(agents.router, prefix="/agents", tags=["Agents"])
api_router.include_router(agent_scheduler.router, tags=["Agent Scheduler"])
api_router.include_router(usage.router, prefix="/usage", tags=["Usage & Costs"])
api_router.include_router(rate_limits.router, prefix="/rate-limits", tags=["Rate Limits"])
api_router.include_router(approvals.router, prefix="/approvals", tags=["Tool Approvals"])
api_router.include_router(analytics.router, prefix="/analytics", tags=["Analytics"])
api_router.include_router(opportunities.router, tags=["Opportunity Scout"])
api_router.include_router(brainstorm.router, tags=["Brainstorm"])
api_router.include_router(ideas.router, prefix="/ideas", tags=["Ideas"])
api_router.include_router(broker.router, tags=["Resource Broker"])
api_router.include_router(tasks.router, tags=["Tasks"])
api_router.include_router(notifications.router, tags=["Notifications"])
api_router.include_router(wallet.router, tags=["Wallet"])
api_router.include_router(bitcoin_budget.router, prefix="/bitcoin", tags=["Bitcoin Budget"])
api_router.include_router(cold_storage.router, tags=["Cold Storage"])
api_router.include_router(disclaimer.router, prefix="/disclaimer", tags=["Disclaimer"])
api_router.include_router(media_library.router, prefix="/media", tags=["Media Library"])
