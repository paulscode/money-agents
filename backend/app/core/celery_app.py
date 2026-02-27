"""Celery application configuration for background task processing."""
from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

# Create Celery app
celery_app = Celery(
    "money_agents",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.agent_tasks",
        "app.tasks.tool_tasks",
        "app.tasks.boltz_tasks",
        "app.tasks.pricing_tasks",
    ]
)

# Celery configuration
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    
    # Task execution settings
    task_acks_late=True,  # Acknowledge task after completion (for reliability)
    task_reject_on_worker_lost=True,  # Re-queue task if worker dies
    worker_prefetch_multiplier=1,  # One task at a time for long-running agent tasks
    
    # Result settings
    result_expires=3600,  # Results expire after 1 hour
    
    # Beat scheduler settings (for periodic tasks)
    beat_schedule={
        # Dynamic scheduler dispatcher - checks database for due agents
        # This replaces individual agent schedules with database-driven scheduling
        "agent-scheduler-dispatcher": {
            "task": "app.tasks.agent_tasks.agent_scheduler_dispatcher",
            "schedule": 30,  # Check every 30 seconds
            "options": {"queue": "agents"},
        },
        # Agent health check - runs every minute
        "agent-health-check": {
            "task": "app.tasks.agent_tasks.agent_health_check",
            "schedule": 60,  # 1 minute
            "options": {"queue": "agents"},
        },
        # Campaign worker heartbeat - renews leases for held campaigns
        "campaign-worker-heartbeat": {
            "task": "app.tasks.agent_tasks.campaign_worker_heartbeat",
            "schedule": 60,  # 1 minute (lease TTL is 5 min, so plenty of margin)
            "options": {"queue": "agents"},
        },
        # Campaign lease cleanup - releases expired leases from dead workers
        "release-expired-campaign-leases": {
            "task": "app.tasks.agent_tasks.release_expired_campaign_leases",
            "schedule": 120,  # 2 minutes
            "options": {"queue": "agents"},
        },
        # Idea review - runs every 15 minutes to process new user ideas
        "review-user-ideas": {
            "task": "app.tasks.agent_tasks.review_user_ideas",
            "schedule": 900,  # 15 minutes
            "options": {"queue": "agents"},
        },
        # Tool idea processing - runs every 30 minutes
        "process-tool-ideas": {
            "task": "app.tasks.agent_tasks.process_tool_ideas",
            "schedule": 1800,  # 30 minutes
            "options": {"queue": "agents"},
        },
        # Tool health check scheduler - runs every 5 minutes
        "tool-health-check": {
            "task": "app.tasks.tool_tasks.tool_health_check_scheduler",
            "schedule": 300,  # 5 minutes
            "options": {"queue": "default"},
        },
        # LLM model pricing refresh - daily at 03:00 UTC
        "refresh-model-pricing": {
            "task": "app.tasks.pricing_tasks.refresh_model_pricing",
            "schedule": crontab(hour=3, minute=0),  # Daily at 03:00 UTC
            "options": {"queue": "default"},
        },
    },
    
    # Queue routing
    task_routes={
        "app.tasks.agent_tasks.*": {"queue": "agents"},
    },
    
    # Default queue
    task_default_queue="default",
    
    # Logging
    worker_hijack_root_logger=False,
)

# Optional: Configure task queues with different priorities
celery_app.conf.task_queues = {
    "default": {},
    "agents": {
        "exchange": "agents",
        "routing_key": "agents",
    },
}


# =============================================================================
# Worker Startup Signal - Trigger System Recovery
# =============================================================================

from celery.signals import worker_ready
import logging

logger = logging.getLogger(__name__)

@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    """
    Trigger system recovery when Celery worker starts.
    
    This ensures that after a restart/crash, stale jobs and 
    stuck states are cleaned up before processing new work.
    """
    logger.info("=" * 60)
    logger.info("CELERY WORKER READY - Triggering system startup recovery")
    logger.info("=" * 60)
    
    # Import here to avoid circular imports
    from app.tasks.agent_tasks import system_startup_recovery
    from app.tasks.boltz_tasks import recover_boltz_swaps
    
    # Trigger recovery tasks (runs async in the worker)
    system_startup_recovery.delay()
    recover_boltz_swaps.delay()

