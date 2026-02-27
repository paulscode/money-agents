"""Celery tasks for LLM model pricing updates.

Periodically fetches the latest per-token pricing from OpenRouter and
updates the in-memory MODEL_PRICING dictionary used for cost tracking.
"""
import asyncio
import logging

from app.core.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.pricing_tasks.refresh_model_pricing")
def refresh_model_pricing() -> dict:
    """Celery task: refresh LLM model pricing from OpenRouter.

    Runs daily via Celery beat, and can also be triggered manually.
    Returns a summary dict for result inspection.
    """
    from app.services.pricing_update_service import refresh_model_pricing as _refresh

    logger.info("Celery task: refreshing LLM model pricing")

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_refresh())
        return result.to_dict()
    except Exception as e:
        logger.error("Pricing refresh task failed: %s", e)
        return {"success": False, "error": str(e)}
    finally:
        loop.close()
