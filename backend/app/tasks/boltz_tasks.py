"""
Celery tasks for Boltz swap processing and monitoring.

Handles:
- process_boltz_swap: Full lifecycle orchestration (pay invoice → monitor → claim)
- monitor_boltz_swap: Periodic status check for active swaps
- recover_boltz_swaps: Startup recovery for interrupted swaps
"""
import asyncio
import logging
from datetime import datetime, timezone

from app.core.celery_app import celery_app
from app.core.database import get_db_context
from app.core.datetime_utils import utc_now as _utc_now, ensure_utc

logger = logging.getLogger(__name__)


async def _cleanup_db_pool():
    """Clean up the database pool for the current event loop."""
    from app.core.database import _engines, _session_makers, _get_loop_id
    loop_id = _get_loop_id()
    if loop_id in _engines:
        engine = _engines.pop(loop_id)
        await engine.dispose()
    if loop_id in _session_makers:
        del _session_makers[loop_id]


async def _cleanup_async_clients():
    """Close async HTTP clients before the event loop closes.

    Prevents 'Event loop is closed' errors on the next task invocation,
    since singleton services (LND, Boltz) cache httpx.AsyncClient instances
    that become invalid once their event loop is destroyed.
    """
    from app.services.lnd_service import lnd_service
    from app.services.boltz_service import boltz_service
    try:
        await lnd_service.close()
    except Exception:
        pass
    try:
        await boltz_service.close()
    except Exception:
        pass


def run_async(coro):
    """Run an async coroutine in Celery (which is sync)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.run_until_complete(_cleanup_async_clients())
        loop.run_until_complete(_cleanup_db_pool())
        loop.close()


# =============================================================================
# Main swap processing task
# =============================================================================

@celery_app.task(
    bind=True,
    name="app.tasks.boltz_tasks.process_boltz_swap",
    max_retries=200,  # ~16hrs at 300s intervals; prevents infinite retry on permanently broken swaps
    default_retry_delay=15,
)
def process_boltz_swap(self, swap_id: str, routing_fee_limit_percent: float = 3.0):
    """Process a Boltz reverse swap through its full lifecycle.

    Lifecycle:
    1. Pay the Boltz hold invoice via LND
    2. Monitor for Boltz lockup transaction
    3. Construct + broadcast claim transaction
    4. Wait for confirmation

    Args:
        swap_id: UUID of the BoltzSwap record
        routing_fee_limit_percent: Max routing fee as % of invoice amount (default 3%)

    Retries every 15 seconds for the first 10 minutes,
    then every 60s for 2 hours, then every 5 minutes.
    """
    return run_async(_process_boltz_swap_async(self, swap_id, routing_fee_limit_percent))


async def _process_boltz_swap_async(task, swap_id: str, routing_fee_limit_percent: float = 3.0):
    """Async implementation of swap processing."""
    from uuid import UUID
    from app.services.boltz_service import boltz_service
    from app.services.lnd_service import lnd_service
    from app.models.boltz_swap import SwapStatus

    async with get_db_context() as db:
        swap = await boltz_service.get_swap_by_id(db, UUID(swap_id))
        if not swap:
            logger.error(f"Boltz swap {swap_id} not found in database")
            return {"error": "Swap not found", "swap_id": swap_id}

        # Skip if already terminal
        if swap.status in (
            SwapStatus.COMPLETED,
            SwapStatus.FAILED,
            SwapStatus.CANCELLED,
            SwapStatus.REFUNDED,
        ):
            logger.info(f"Swap {swap.boltz_swap_id} already in terminal state: {swap.status.value}")
            return {"status": swap.status.value, "swap_id": swap_id}

        try:
            # Step 1: Pay the invoice if we haven't yet
            if swap.status == SwapStatus.CREATED:
                logger.info(f"Paying Boltz invoice for swap {swap.boltz_swap_id}")

                # Decode invoice to get payment hash before paying
                # (so we can look up status if the HTTP request times out)
                decoded, decode_err = await lnd_service.decode_payment_request(swap.boltz_invoice)
                payment_hash_hex = decoded.get("payment_hash", "") if decoded else ""

                swap.status = SwapStatus.PAYING_INVOICE
                swap.lnd_payment_hash = payment_hash_hex
                swap.updated_at = _utc_now()
                history = swap.status_history or []
                history.append({
                    "status": "paying_invoice",
                    "timestamp": _utc_now().isoformat(),
                })
                swap.status_history = history
                await db.commit()

                # Pay the Boltz hold invoice
                # Use a long timeout — Boltz hold invoices settle only after
                # the on-chain lockup is confirmed, which can take 10+ minutes.
                # Fee limit: user-configurable % of invoice amount (min 1000 sats)
                fee_limit = max(1000, int(swap.invoice_amount_sats * routing_fee_limit_percent / 100))
                logger.info(
                    f"Paying {swap.invoice_amount_sats} sats with routing fee limit "
                    f"{fee_limit} sats ({routing_fee_limit_percent}%)"
                )
                payment_result, pay_error = await lnd_service.send_payment_sync(
                    payment_request=swap.boltz_invoice,
                    fee_limit_sats=fee_limit,
                    timeout_seconds=600,
                )

                if pay_error:
                    # HTTP timeout or connection error — the payment may still be
                    # in-flight in LND. Check actual payment status before failing.
                    if payment_hash_hex:
                        logger.warning(
                            f"Payment request error for {swap.boltz_swap_id}, "
                            f"checking LND for actual status: {pay_error}"
                        )
                        pay_status, _ = await lnd_service.lookup_payment(payment_hash_hex)
                        if pay_status and pay_status.get("status") in ("SUCCEEDED", "IN_FLIGHT"):
                            logger.info(
                                f"Payment actually {pay_status['status']} for {swap.boltz_swap_id} "
                                f"despite HTTP error — continuing swap"
                            )
                            if pay_status["status"] == "SUCCEEDED":
                                swap.status = SwapStatus.INVOICE_PAID
                                swap.lnd_payment_status = "succeeded"
                            # else IN_FLIGHT — keep PAYING_INVOICE, retry will check again
                            swap.updated_at = _utc_now()
                            await db.commit()
                            # Don't fail — fall through to advance_swap or retry
                            pay_error = None
                        else:
                            actual_status = pay_status.get("status", "UNKNOWN") if pay_status else "lookup_failed"
                            logger.error(
                                f"Payment confirmed failed for {swap.boltz_swap_id}: "
                                f"LND status={actual_status}, original error: {pay_error}"
                            )

                    if pay_error:
                        swap.status = SwapStatus.FAILED
                        swap.error_message = f"Lightning payment failed: {pay_error}"
                        swap.lnd_payment_status = "failed"
                        swap.completed_at = _utc_now()
                        await db.commit()
                        return {"status": "failed", "error": pay_error}

                if swap.status == SwapStatus.PAYING_INVOICE and payment_result:
                    # Payment completed synchronously
                    swap.status = SwapStatus.INVOICE_PAID
                    swap.lnd_payment_hash = payment_result.get("payment_hash", "") or payment_hash_hex
                    swap.lnd_payment_status = "succeeded"

                if swap.status == SwapStatus.INVOICE_PAID:
                    swap.updated_at = _utc_now()
                    history = swap.status_history or []
                    history.append({
                        "status": "invoice_paid",
                        "timestamp": _utc_now().isoformat(),
                        "payment_hash": swap.lnd_payment_hash,
                    })
                    swap.status_history = history
                    await db.commit()
                    logger.info(f"Invoice paid for swap {swap.boltz_swap_id}")

            # Step 1b: If we're in PAYING_INVOICE (from a previous attempt), check status
            if swap.status == SwapStatus.PAYING_INVOICE and swap.lnd_payment_hash:
                pay_status, _ = await lnd_service.lookup_payment(swap.lnd_payment_hash)
                if pay_status:
                    if pay_status["status"] == "SUCCEEDED":
                        swap.status = SwapStatus.INVOICE_PAID
                        swap.lnd_payment_status = "succeeded"
                        swap.updated_at = _utc_now()
                        await db.commit()
                        logger.info(f"Payment confirmed for {swap.boltz_swap_id}")
                    elif pay_status["status"] == "FAILED":
                        swap.status = SwapStatus.FAILED
                        swap.error_message = "Lightning payment failed after retry check"
                        swap.lnd_payment_status = "failed"
                        swap.completed_at = _utc_now()
                        await db.commit()
                        return {"status": "failed", "error": swap.error_message}

            # Step 2: Advance via status check + claim
            updated_swap, advance_err = await boltz_service.advance_swap(db, swap)

            if updated_swap.status in (SwapStatus.COMPLETED, SwapStatus.CLAIMED):
                logger.info(f"Swap {swap.boltz_swap_id} reached {updated_swap.status.value}")
                return {
                    "status": updated_swap.status.value,
                    "claim_txid": updated_swap.claim_txid,
                }

            # Not yet complete — schedule retry
            if updated_swap.status not in (
                SwapStatus.FAILED, SwapStatus.REFUNDED, SwapStatus.CANCELLED
            ):
                retry_delay = _get_retry_delay(updated_swap)
                logger.info(
                    f"Swap {swap.boltz_swap_id} status={updated_swap.status.value}, "
                    f"retrying in {retry_delay}s"
                )
                task.retry(countdown=retry_delay)

            return {
                "status": updated_swap.status.value,
                "error": advance_err,
            }

        except Exception as e:
            logger.error(f"Error processing swap {swap.boltz_swap_id}: {e}", exc_info=True)
            # Retry on unexpected errors (the swap state is persisted)
            retry_delay = _get_retry_delay(swap)
            try:
                task.retry(countdown=retry_delay, exc=e)
            except task.MaxRetriesExceededError:
                swap.status = SwapStatus.FAILED
                swap.error_message = f"Max retries exceeded: {e}"
                swap.completed_at = _utc_now()
                await db.commit()
                return {"status": "failed", "error": str(e)}


def _get_retry_delay(swap) -> int:
    """Calculate retry delay based on swap age (tiered backoff).

    First 10 min: 15s  |  10 min – 2 hr: 60s  |  2+ hr: 300s
    """
    if not swap.created_at:
        return 15

    age_seconds = (_utc_now() - ensure_utc(swap.created_at)).total_seconds()

    if age_seconds < 600:      # < 10 minutes
        return 15
    elif age_seconds < 7200:   # < 2 hours
        return 60
    else:
        return 300


# =============================================================================
# Startup recovery task
# =============================================================================

@celery_app.task(bind=True, name="app.tasks.boltz_tasks.recover_boltz_swaps")
def recover_boltz_swaps(self):
    """Recover any Boltz swaps interrupted by crash/restart.

    Called on Celery worker startup via the worker_ready signal.
    """
    return run_async(_recover_boltz_swaps_async())


async def _recover_boltz_swaps_async():
    """Async implementation of swap recovery."""
    from app.services.boltz_service import boltz_service

    async with get_db_context() as db:
        results = await boltz_service.recover_pending_swaps(db)

    if results:
        logger.info(f"Boltz swap recovery complete: {len(results)} swap(s) processed")
        for r in results:
            logger.info(
                f"  Swap {r['boltz_swap_id']}: status={r['status']}, error={r.get('error')}"
            )
    else:
        logger.info("No pending Boltz swaps to recover")

    return {"recovered": len(results), "results": results}
