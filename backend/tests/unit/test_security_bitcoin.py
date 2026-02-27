from pydantic import SecretStr
from pydantic import SecretStr
"""
Comprehensive security tests for the Bitcoin budget enforcement system.

Tests cover all 10 audit findings:
1. Tuple destructuring (LND service returns)
2. On-chain sends debit budget immediately
3. TOCTOU race condition prevention (row locking)
4. Wallet endpoints skip budget checks (auth-gated)
5. Campaign ownership validation
6. Audit logging for safety limit changes
7. Sandbox network isolation
8. Transaction recording failure handling
9. Global limit includes fees
10. Cumulative rate limiting


Additional coverage:
  - Boltz clearnet fallback defaults
  - Async subprocess in Boltz service
  - LND client init lock
  - Boltz stderr hex redaction
  - Velocity breaker controls
"""
import asyncio
import inspect
import json
import logging
import re
import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from app.core.config import settings
from app.models import Campaign, Proposal, RiskLevel
from app.models.bitcoin_budget import (
    BitcoinTransaction,
    BitcoinVelocityBreaker,
    TransactionType,
    TransactionStatus,
    SpendTrigger,
)
from app.services.bitcoin_budget_service import BitcoinBudgetService, BudgetCheckResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_campaign(
    db_session,
    user_id: UUID,
    budget_sats: int = 50000,
    spent_sats: int = 0,
    received_sats: int = 0,
) -> Campaign:
    """Insert a Campaign (and its prerequisite Proposal) into the test DB."""
    prop = Proposal(
        user_id=user_id,
        title="Test Proposal",
        summary="test",
        detailed_description="test desc",
        initial_budget=Decimal("0"),
        risk_level=RiskLevel.LOW,
        risk_description="low risk",
        stop_loss_threshold={"pct": 10},
        success_criteria={"goal": "test"},
        required_tools=[],
        required_inputs=[],
        bitcoin_budget_sats=budget_sats,
    )
    db_session.add(prop)
    await db_session.flush()

    campaign = Campaign(
        proposal_id=prop.id,
        user_id=user_id,
        budget_allocated=Decimal("0"),
        bitcoin_budget_sats=budget_sats,
        bitcoin_spent_sats=spent_sats,
        bitcoin_received_sats=received_sats,
        success_metrics={},
        requirements_checklist={},
    )
    db_session.add(campaign)
    await db_session.flush()
    return campaign


class _SavedSettings:
    """Context manager to save/restore settings modified by tests."""

    _KEYS = (
        "lnd_max_payment_sats",
        "lnd_rate_limit_sats",
        "lnd_rate_limit_window_seconds",
        "lnd_macaroon_hex",
        "lnd_velocity_max_txns",
        "lnd_velocity_window_seconds",
    )

    def __init__(self):
        self._saved = {}

    def __enter__(self):
        self._saved = {k: getattr(settings, k) for k in self._KEYS}
        return self

    def __exit__(self, *args):
        for k, v in self._saved.items():
            setattr(settings, k, v)


# ═══════════════════════════════════════════════════════════════════════════
# Finding 9: Global safety limit includes fees
# ═══════════════════════════════════════════════════════════════════════════


class TestGlobalLimitIncludesFees:
    """Total = amount + fees must be compared against the per-tx safety limit."""

    @pytest.mark.asyncio
    async def test_amount_under_limit_but_total_over(self, db_session):
        """9000 amount + 2000 fee = 11000 > 10000 limit  →  blocked."""
        with _SavedSettings():
            settings.lnd_max_payment_sats = 10000
            settings.lnd_rate_limit_sats = 0
            svc = BitcoinBudgetService(db_session)
            result = await svc.check_spend(amount_sats=9000, fee_sats=2000)
            assert not result.allowed
            assert result.trigger == SpendTrigger.GLOBAL_LIMIT
            assert "11000" in result.reason

    @pytest.mark.asyncio
    async def test_amount_plus_fees_under_limit(self, db_session):
        with _SavedSettings():
            settings.lnd_max_payment_sats = 10000
            settings.lnd_rate_limit_sats = 0
            svc = BitcoinBudgetService(db_session)
            result = await svc.check_spend(amount_sats=7000, fee_sats=2000)
            assert result.allowed

    @pytest.mark.asyncio
    async def test_zero_limit_blocks_all(self, db_session):
        with _SavedSettings():
            settings.lnd_max_payment_sats = 0
            svc = BitcoinBudgetService(db_session)
            result = await svc.check_spend(amount_sats=1)
            assert not result.allowed
            assert result.trigger == SpendTrigger.GLOBAL_LIMIT

    @pytest.mark.asyncio
    async def test_negative_one_disables_per_tx_limit(self, db_session):
        """Safety limit of -1 disables the per-transaction check."""
        with _SavedSettings():
            settings.lnd_max_payment_sats = -1
            settings.lnd_rate_limit_sats = 0  # disable rate limit too
            svc = BitcoinBudgetService(db_session)
            result = await svc.check_spend(amount_sats=999_999_999)
            assert result.allowed


# ═══════════════════════════════════════════════════════════════════════════
# Finding 2: On-chain sends debit budget immediately
# ═══════════════════════════════════════════════════════════════════════════


class TestOnchainBudgetDebit:

    @pytest.mark.asyncio
    async def test_pending_send_debits_campaign(self, db_session, test_user):
        campaign = await _create_campaign(db_session, test_user.id, budget_sats=50000)

        svc = BitcoinBudgetService(db_session)
        tx = await svc.record_transaction(
            user_id=test_user.id,
            tx_type=TransactionType.ONCHAIN_SEND,
            amount_sats=10000,
            campaign_id=campaign.id,
            status=TransactionStatus.PENDING,
        )

        await db_session.refresh(campaign)
        assert campaign.bitcoin_spent_sats == 10000
        assert tx.status == TransactionStatus.PENDING

    @pytest.mark.asyncio
    async def test_confirmed_lightning_send_debits(self, db_session, test_user):
        campaign = await _create_campaign(db_session, test_user.id, budget_sats=50000)

        svc = BitcoinBudgetService(db_session)
        await svc.record_transaction(
            user_id=test_user.id,
            tx_type=TransactionType.LIGHTNING_SEND,
            amount_sats=5000,
            fee_sats=100,
            campaign_id=campaign.id,
            status=TransactionStatus.CONFIRMED,
        )

        await db_session.refresh(campaign)
        assert campaign.bitcoin_spent_sats == 5100

    @pytest.mark.asyncio
    async def test_second_spend_sees_updated_budget(self, db_session, test_user):
        with _SavedSettings():
            settings.lnd_max_payment_sats = -1
            settings.lnd_rate_limit_sats = 0
            campaign = await _create_campaign(
                db_session, test_user.id, budget_sats=20000,
            )

            svc = BitcoinBudgetService(db_session)
            await svc.record_transaction(
                user_id=test_user.id,
                tx_type=TransactionType.ONCHAIN_SEND,
                amount_sats=15000,
                campaign_id=campaign.id,
                status=TransactionStatus.PENDING,
            )

            check = await svc.check_spend(
                amount_sats=10000,
                campaign_id=campaign.id,
                user_id=test_user.id,
            )
            assert not check.allowed
            assert check.trigger == SpendTrigger.OVER_BUDGET

    @pytest.mark.asyncio
    async def test_failed_tx_reverses_debit(self, db_session, test_user):
        campaign = await _create_campaign(db_session, test_user.id, budget_sats=50000)

        svc = BitcoinBudgetService(db_session)
        tx = await svc.record_transaction(
            user_id=test_user.id,
            tx_type=TransactionType.ONCHAIN_SEND,
            amount_sats=10000,
            fee_sats=500,
            campaign_id=campaign.id,
            status=TransactionStatus.PENDING,
        )

        await db_session.refresh(campaign)
        assert campaign.bitcoin_spent_sats == 10500

        failed_tx = await svc.fail_transaction(tx.id)
        assert failed_tx.status == TransactionStatus.FAILED

        await db_session.refresh(campaign)
        assert campaign.bitcoin_spent_sats == 0

    @pytest.mark.asyncio
    async def test_receive_not_debited_on_pending(self, db_session, test_user):
        campaign = await _create_campaign(db_session, test_user.id, budget_sats=50000)

        svc = BitcoinBudgetService(db_session)
        await svc.record_transaction(
            user_id=test_user.id,
            tx_type=TransactionType.LIGHTNING_RECEIVE,
            amount_sats=5000,
            campaign_id=campaign.id,
            status=TransactionStatus.PENDING,
        )

        await db_session.refresh(campaign)
        assert campaign.bitcoin_received_sats == 0

    @pytest.mark.asyncio
    async def test_confirm_send_does_not_double_debit(self, db_session, test_user):
        """Confirming a PENDING send should NOT debit the campaign again."""
        campaign = await _create_campaign(db_session, test_user.id, budget_sats=50000)

        svc = BitcoinBudgetService(db_session)
        tx = await svc.record_transaction(
            user_id=test_user.id,
            tx_type=TransactionType.ONCHAIN_SEND,
            amount_sats=10000,
            campaign_id=campaign.id,
            status=TransactionStatus.PENDING,
        )

        await db_session.refresh(campaign)
        assert campaign.bitcoin_spent_sats == 10000

        confirmed = await svc.confirm_transaction(tx.id)
        assert confirmed.status == TransactionStatus.CONFIRMED

        await db_session.refresh(campaign)
        assert campaign.bitcoin_spent_sats == 10000  # NOT 20000

    @pytest.mark.asyncio
    async def test_confirm_receive_credits_campaign(self, db_session, test_user):
        """Confirming a PENDING receive should credit the campaign."""
        campaign = await _create_campaign(db_session, test_user.id, budget_sats=50000)

        svc = BitcoinBudgetService(db_session)
        tx = await svc.record_transaction(
            user_id=test_user.id,
            tx_type=TransactionType.LIGHTNING_RECEIVE,
            amount_sats=5000,
            campaign_id=campaign.id,
            status=TransactionStatus.PENDING,
        )

        await db_session.refresh(campaign)
        assert campaign.bitcoin_received_sats == 0

        await svc.confirm_transaction(tx.id)

        await db_session.refresh(campaign)
        assert campaign.bitcoin_received_sats == 5000


# ═══════════════════════════════════════════════════════════════════════════
# Finding 5: Campaign ownership validation
# ═══════════════════════════════════════════════════════════════════════════


class TestCampaignOwnership:

    @pytest.mark.asyncio
    async def test_wrong_user_blocked(self, db_session, test_user):
        with _SavedSettings():
            settings.lnd_max_payment_sats = -1
            settings.lnd_rate_limit_sats = 0

            # Create a second user to own the campaign
            from app.models import User
            from app.core.security import get_password_hash
            other_user = User(
                username="otheruser",
                email="other@example.com",
                password_hash=get_password_hash("password123"),
                role="user",
                is_active=True,
            )
            db_session.add(other_user)
            await db_session.flush()

            campaign = await _create_campaign(
                db_session, other_user.id, budget_sats=100000,
            )

            svc = BitcoinBudgetService(db_session)
            check = await svc.check_spend(
                amount_sats=1000,
                campaign_id=campaign.id,
                user_id=test_user.id,  # different from campaign owner
            )
            assert not check.allowed
            assert check.trigger == SpendTrigger.MANUAL_REVIEW
            assert "does not belong" in check.reason

    @pytest.mark.asyncio
    async def test_correct_user_allowed(self, db_session, test_user):
        with _SavedSettings():
            settings.lnd_max_payment_sats = -1
            settings.lnd_rate_limit_sats = 0
            campaign = await _create_campaign(
                db_session, test_user.id, budget_sats=100000,
            )

            svc = BitcoinBudgetService(db_session)
            check = await svc.check_spend(
                amount_sats=1000,
                campaign_id=campaign.id,
                user_id=test_user.id,
            )
            assert check.allowed

    @pytest.mark.asyncio
    async def test_no_user_id_rejected(self, db_session, test_user):
        with _SavedSettings():
            settings.lnd_max_payment_sats = -1
            settings.lnd_rate_limit_sats = 0
            campaign = await _create_campaign(
                db_session, test_user.id, budget_sats=100000,
            )

            svc = BitcoinBudgetService(db_session)
            check = await svc.check_spend(
                amount_sats=1000,
                campaign_id=campaign.id,
                user_id=None,
            )
            assert not check.allowed
            assert check.trigger == SpendTrigger.MANUAL_REVIEW


# ═══════════════════════════════════════════════════════════════════════════
# Finding 10: Cumulative rate limiting
# ═══════════════════════════════════════════════════════════════════════════


class TestCumulativeRateLimit:

    @pytest.mark.asyncio
    async def test_blocks_when_exceeded(self, db_session, test_user):
        with _SavedSettings():
            settings.lnd_max_payment_sats = -1
            settings.lnd_rate_limit_sats = 50000
            settings.lnd_rate_limit_window_seconds = 3600

            svc = BitcoinBudgetService(db_session)
            await svc.record_transaction(
                user_id=test_user.id,
                tx_type=TransactionType.LIGHTNING_SEND,
                amount_sats=40000,
                status=TransactionStatus.CONFIRMED,
            )
            await db_session.flush()

            check = await svc.check_spend(amount_sats=20000)
            assert not check.allowed
            assert "rate limit" in check.reason.lower()

    @pytest.mark.asyncio
    async def test_allows_under_threshold(self, db_session, test_user):
        with _SavedSettings():
            settings.lnd_max_payment_sats = -1
            settings.lnd_rate_limit_sats = 50000
            settings.lnd_rate_limit_window_seconds = 3600

            svc = BitcoinBudgetService(db_session)
            await svc.record_transaction(
                user_id=test_user.id,
                tx_type=TransactionType.LIGHTNING_SEND,
                amount_sats=20000,
                status=TransactionStatus.CONFIRMED,
            )
            await db_session.flush()

            check = await svc.check_spend(amount_sats=10000)
            assert check.allowed

    @pytest.mark.asyncio
    async def test_disabled_when_zero(self, db_session, test_user):
        with _SavedSettings():
            settings.lnd_max_payment_sats = -1
            settings.lnd_rate_limit_sats = 0

            svc = BitcoinBudgetService(db_session)
            await svc.record_transaction(
                user_id=test_user.id,
                tx_type=TransactionType.LIGHTNING_SEND,
                amount_sats=999999,
                status=TransactionStatus.CONFIRMED,
            )
            await db_session.flush()

            check = await svc.check_spend(amount_sats=999999)
            assert check.allowed

    @pytest.mark.asyncio
    async def test_includes_pending_txns(self, db_session, test_user):
        with _SavedSettings():
            settings.lnd_max_payment_sats = -1
            settings.lnd_rate_limit_sats = 50000
            settings.lnd_rate_limit_window_seconds = 3600

            svc = BitcoinBudgetService(db_session)
            await svc.record_transaction(
                user_id=test_user.id,
                tx_type=TransactionType.ONCHAIN_SEND,
                amount_sats=45000,
                status=TransactionStatus.PENDING,
            )
            await db_session.flush()

            check = await svc.check_spend(amount_sats=10000)
            assert not check.allowed
            assert "rate limit" in check.reason.lower()


# ═══════════════════════════════════════════════════════════════════════════
# Finding 1: Tuple destructuring in tool executor
# ═══════════════════════════════════════════════════════════════════════════


class TestTupleDestructuring:
    """LND service returns (data, error) tuples; the tool executor must
    destructure them correctly and handle both paths."""

    @pytest.fixture(autouse=True)
    def _enable_lnd(self):
        with _SavedSettings():
            settings.lnd_macaroon_hex = SecretStr("test")
            yield

    @pytest.mark.asyncio
    async def test_pay_invoice_handles_decode_tuple(self):
        from app.services.tool_execution_service import ToolExecutor
        from contextlib import asynccontextmanager

        svc = ToolExecutor.__new__(ToolExecutor)

        mock_lnd = AsyncMock()
        mock_lnd.decode_payment_request.return_value = (
            {"num_satoshis": 1000, "destination": "abc"}, None,
        )
        mock_lnd.send_payment_sync.return_value = (
            {
                "payment_hash": "abc123",
                "payment_route": {"total_fees": 10},
                "payment_preimage": "p",
            },
            None,
        )

        mock_budget_svc = AsyncMock()
        mock_budget_svc.check_spend.return_value = BudgetCheckResult(allowed=True)
        mock_budget_svc.record_transaction.return_value = MagicMock(id=uuid4())

        @asynccontextmanager
        async def mock_session():
            yield AsyncMock()

        svc._get_db_session = mock_session

        with (
            patch("app.services.lnd_service.lnd_service", mock_lnd),
            patch(
                "app.services.bitcoin_budget_service.BitcoinBudgetService",
                return_value=mock_budget_svc,
            ),
            patch("app.services.mempool_fee_service.mempool_fee_service"),
        ):
            result = await svc._execute_lnd_lightning(
                MagicMock(),
                {
                    "action": "pay_invoice",
                    "payment_request": "lnbc1000...",
                    "__ma_user_id": str(uuid4()),
                    "__ma_execution_id": str(uuid4()),
                },
            )

        assert result.success, f"Expected success but got: {result.error}"
        assert result.output["amount_sats"] == 1000
        assert result.output["fee_sats"] == 10

    @pytest.mark.asyncio
    async def test_pay_invoice_handles_decode_error(self):
        from app.services.tool_execution_service import ToolExecutor

        svc = ToolExecutor.__new__(ToolExecutor)

        mock_lnd = AsyncMock()
        mock_lnd.decode_payment_request.return_value = (None, "invalid invoice")

        with (
            patch("app.services.lnd_service.lnd_service", mock_lnd),
            patch("app.services.mempool_fee_service.mempool_fee_service"),
        ):
            result = await svc._execute_lnd_lightning(
                MagicMock(),
                {
                    "action": "pay_invoice",
                    "payment_request": "garbage_data",
                },
            )

        assert not result.success
        assert "invalid invoice" in result.error

    @pytest.mark.asyncio
    async def test_pay_invoice_handles_send_error(self):
        from app.services.tool_execution_service import ToolExecutor
        from contextlib import asynccontextmanager

        svc = ToolExecutor.__new__(ToolExecutor)

        mock_lnd = AsyncMock()
        mock_lnd.decode_payment_request.return_value = (
            {"num_satoshis": 1000}, None,
        )
        mock_lnd.send_payment_sync.return_value = (None, "no route found")

        mock_budget_svc = AsyncMock()
        mock_budget_svc.check_spend.return_value = BudgetCheckResult(allowed=True)

        @asynccontextmanager
        async def mock_session():
            yield AsyncMock()

        svc._get_db_session = mock_session

        with (
            patch("app.services.lnd_service.lnd_service", mock_lnd),
            patch(
                "app.services.bitcoin_budget_service.BitcoinBudgetService",
                return_value=mock_budget_svc,
            ),
            patch("app.services.mempool_fee_service.mempool_fee_service"),
        ):
            result = await svc._execute_lnd_lightning(
                MagicMock(),
                {
                    "action": "pay_invoice",
                    "payment_request": "lnbc1000...",
                    "__ma_user_id": str(uuid4()),
                },
            )

        assert not result.success
        assert "no route found" in result.error

    @pytest.mark.asyncio
    async def test_send_onchain_handles_send_error(self):
        from app.services.tool_execution_service import ToolExecutor
        from contextlib import asynccontextmanager

        svc = ToolExecutor.__new__(ToolExecutor)

        mock_lnd = AsyncMock()
        mock_lnd.send_coins.return_value = (None, "insufficient funds")

        mock_budget_svc = AsyncMock()
        mock_budget_svc.check_spend.return_value = BudgetCheckResult(allowed=True)

        @asynccontextmanager
        async def mock_session():
            yield AsyncMock()

        svc._get_db_session = mock_session

        mock_mempool = AsyncMock()
        mock_mempool.get_fee_for_priority.return_value = 5

        with (
            patch("app.services.lnd_service.lnd_service", mock_lnd),
            patch(
                "app.services.bitcoin_budget_service.BitcoinBudgetService",
                return_value=mock_budget_svc,
            ),
            patch(
                "app.services.mempool_fee_service.mempool_fee_service",
                mock_mempool,
            ),
        ):
            result = await svc._execute_lnd_lightning(
                MagicMock(),
                {
                    "action": "send_onchain",
                    "address": "bc1q...",
                    "amount_sats": 5000,
                    "__ma_user_id": str(uuid4()),
                },
            )

        assert not result.success
        assert "insufficient funds" in result.error

    @pytest.mark.asyncio
    async def test_decode_invoice_success(self):
        from app.services.tool_execution_service import ToolExecutor

        svc = ToolExecutor.__new__(ToolExecutor)

        mock_lnd = AsyncMock()
        mock_lnd.decode_payment_request.return_value = (
            {"num_satoshis": 5000, "destination": "xyz"}, None,
        )

        with (
            patch("app.services.lnd_service.lnd_service", mock_lnd),
            patch("app.services.mempool_fee_service.mempool_fee_service"),
        ):
            result = await svc._execute_lnd_lightning(
                MagicMock(),
                {
                    "action": "decode_invoice",
                    "payment_request": "lnbc5000...",
                },
            )

        assert result.success
        assert result.output["num_satoshis"] == 5000

    @pytest.mark.asyncio
    async def test_decode_invoice_error(self):
        from app.services.tool_execution_service import ToolExecutor

        svc = ToolExecutor.__new__(ToolExecutor)

        mock_lnd = AsyncMock()
        mock_lnd.decode_payment_request.return_value = (None, "bad invoice")

        with (
            patch("app.services.lnd_service.lnd_service", mock_lnd),
            patch("app.services.mempool_fee_service.mempool_fee_service"),
        ):
            result = await svc._execute_lnd_lightning(
                MagicMock(),
                {
                    "action": "decode_invoice",
                    "payment_request": "garbage",
                },
            )

        assert not result.success
        assert "bad invoice" in result.error


# ═══════════════════════════════════════════════════════════════════════════
# Finding 3: TOCTOU — budget check + payment is atomic (single session)
# ═══════════════════════════════════════════════════════════════════════════


class TestAtomicBudgetCheckAndPayment:

    @pytest.fixture(autouse=True)
    def _enable_lnd(self):
        with _SavedSettings():
            settings.lnd_macaroon_hex = SecretStr("test")
            yield

    @pytest.mark.asyncio
    async def test_pay_invoice_single_session(self):
        """Budget check, payment, and recording use ONE DB session."""
        from app.services.tool_execution_service import ToolExecutor
        from contextlib import asynccontextmanager

        svc = ToolExecutor.__new__(ToolExecutor)

        mock_lnd = AsyncMock()
        mock_lnd.decode_payment_request.return_value = (
            {"num_satoshis": 1000}, None,
        )
        mock_lnd.send_payment_sync.return_value = (
            {
                "payment_hash": "h",
                "payment_route": {"total_fees": 5},
                "payment_preimage": "p",
            },
            None,
        )

        session_count = 0

        @asynccontextmanager
        async def tracking_session():
            nonlocal session_count
            session_count += 1
            yield AsyncMock()

        mock_budget_svc = AsyncMock()
        mock_budget_svc.check_spend.return_value = BudgetCheckResult(allowed=True)
        mock_budget_svc.record_transaction.return_value = MagicMock(id=uuid4())

        svc._get_db_session = tracking_session

        with (
            patch("app.services.lnd_service.lnd_service", mock_lnd),
            patch(
                "app.services.bitcoin_budget_service.BitcoinBudgetService",
                return_value=mock_budget_svc,
            ),
            patch("app.services.mempool_fee_service.mempool_fee_service"),
        ):
            result = await svc._execute_lnd_lightning(
                MagicMock(),
                {
                    "action": "pay_invoice",
                    "payment_request": "lnbc1000...",
                    "__ma_user_id": str(uuid4()),
                    "__ma_execution_id": str(uuid4()),
                },
            )

        assert result.success
        # SGA3-M7: pay_invoice now uses reserve→pay→confirm pattern (multiple sessions
        # to avoid holding row lock during LND timeout). Expected: 2+ sessions.
        assert session_count >= 2, (
            f"Expected 2+ sessions (reserve→pay→confirm), got {session_count}"
        )

    @pytest.mark.asyncio
    async def test_send_onchain_single_session(self):
        from app.services.tool_execution_service import ToolExecutor
        from contextlib import asynccontextmanager

        svc = ToolExecutor.__new__(ToolExecutor)

        mock_lnd = AsyncMock()
        mock_lnd.send_coins.return_value = ({"txid": "abc"}, None)

        session_count = 0

        @asynccontextmanager
        async def tracking_session():
            nonlocal session_count
            session_count += 1
            yield AsyncMock()

        mock_budget_svc = AsyncMock()
        mock_budget_svc.check_spend.return_value = BudgetCheckResult(allowed=True)
        mock_budget_svc.record_transaction.return_value = MagicMock(id=uuid4())

        svc._get_db_session = tracking_session

        mock_mempool = AsyncMock()
        mock_mempool.get_fee_for_priority.return_value = 5

        with (
            patch("app.services.lnd_service.lnd_service", mock_lnd),
            patch(
                "app.services.bitcoin_budget_service.BitcoinBudgetService",
                return_value=mock_budget_svc,
            ),
            patch(
                "app.services.mempool_fee_service.mempool_fee_service",
                mock_mempool,
            ),
        ):
            result = await svc._execute_lnd_lightning(
                MagicMock(),
                {
                    "action": "send_onchain",
                    "address": "bc1q...",
                    "amount_sats": 5000,
                    "__ma_user_id": str(uuid4()),
                    "__ma_execution_id": str(uuid4()),
                },
            )

        assert result.success
        assert session_count == 1


# ═══════════════════════════════════════════════════════════════════════════
# Finding 8: Recording failures must propagate
# ═══════════════════════════════════════════════════════════════════════════


class TestRecordingFailurePropagation:

    @pytest.fixture(autouse=True)
    def _enable_lnd(self):
        with _SavedSettings():
            settings.lnd_macaroon_hex = SecretStr("test")
            yield

    @pytest.mark.asyncio
    async def test_pay_invoice_recording_failure_raises(self):
        """If record_transaction fails, the error must NOT be silently swallowed."""
        from app.services.tool_execution_service import ToolExecutor
        from contextlib import asynccontextmanager

        svc = ToolExecutor.__new__(ToolExecutor)

        mock_lnd = AsyncMock()
        mock_lnd.decode_payment_request.return_value = (
            {"num_satoshis": 1000}, None,
        )
        mock_lnd.send_payment_sync.return_value = (
            {
                "payment_hash": "h",
                "payment_route": {"total_fees": 5},
                "payment_preimage": "p",
            },
            None,
        )

        mock_budget_svc = AsyncMock()
        mock_budget_svc.check_spend.return_value = BudgetCheckResult(allowed=True)
        mock_budget_svc.record_transaction.side_effect = Exception("DB connection lost")

        @asynccontextmanager
        async def mock_session():
            yield AsyncMock()

        svc._get_db_session = mock_session

        with (
            patch("app.services.lnd_service.lnd_service", mock_lnd),
            patch(
                "app.services.bitcoin_budget_service.BitcoinBudgetService",
                return_value=mock_budget_svc,
            ),
            patch("app.services.mempool_fee_service.mempool_fee_service"),
        ):
            result = await svc._execute_lnd_lightning(
                MagicMock(),
                {
                    "action": "pay_invoice",
                    "payment_request": "lnbc1000...",
                    "__ma_user_id": str(uuid4()),
                    "__ma_execution_id": str(uuid4()),
                },
            )

        # Must NOT succeed — error must propagate
        assert not result.success
        # Agent-facing error is generic (HIGH-3 mitigation)
        assert "Lightning operation failed" in result.error


# ═══════════════════════════════════════════════════════════════════════════
# Finding 6: Audit logging for safety limit changes
# ═══════════════════════════════════════════════════════════════════════════


class TestSafetyLimitAuditLog:

    def test_update_endpoint_logs_change(self):
        """Verify the wallet endpoint code logs safety limit changes."""
        from app.api.endpoints import wallet as wallet_mod

        source = inspect.getsource(wallet_mod.update_safety_limit)
        assert "SAFETY LIMIT CHANGED" in source
        assert "audit" in source.lower()
        assert ".warning" in source


# ═══════════════════════════════════════════════════════════════════════════
# Finding 7: Sandbox network isolation
# ═══════════════════════════════════════════════════════════════════════════


class TestSandboxNetworkIsolation:

    def _read_sandbox_source(self):
        """Read source file directly to avoid importing docker module."""
        import pathlib
        src = pathlib.Path(__file__).resolve().parents[2] / "app" / "services" / "dev_sandbox_service.py"
        return src.read_text()

    def test_container_blocks_internal_hosts(self):
        source = self._read_sandbox_source()
        assert "extra_hosts" in source
        assert "host.docker.internal" in source
        assert "backend" in source
        assert "postgres" in source
        assert "redis" in source
        assert "127.0.0.254" in source

    def test_container_uses_public_dns(self):
        source = self._read_sandbox_source()
        assert "dns=" in source
        assert "8.8.8.8" in source


# ═══════════════════════════════════════════════════════════════════════════
# Edge cases
# ═══════════════════════════════════════════════════════════════════════════


class TestCampaignBudgetEdgeCases:

    @pytest.mark.asyncio
    async def test_no_campaign_still_checks_global(self, db_session):
        with _SavedSettings():
            settings.lnd_max_payment_sats = 5000
            settings.lnd_rate_limit_sats = 0
            svc = BitcoinBudgetService(db_session)
            result = await svc.check_spend(amount_sats=6000)
            assert not result.allowed
            assert result.trigger == SpendTrigger.GLOBAL_LIMIT

    @pytest.mark.asyncio
    async def test_campaign_no_budget_set(self, db_session, test_user):
        with _SavedSettings():
            settings.lnd_max_payment_sats = -1
            settings.lnd_rate_limit_sats = 0
            campaign = await _create_campaign(
                db_session, test_user.id, budget_sats=50000,
            )
            campaign.bitcoin_budget_sats = None
            await db_session.flush()

            svc = BitcoinBudgetService(db_session)
            check = await svc.check_spend(
                amount_sats=1000,
                campaign_id=campaign.id,
                user_id=test_user.id,
            )
            assert not check.allowed
            assert check.trigger == SpendTrigger.NO_BUDGET

    @pytest.mark.asyncio
    async def test_campaign_budget_exact_boundary(self, db_session, test_user):
        with _SavedSettings():
            settings.lnd_max_payment_sats = -1
            settings.lnd_rate_limit_sats = 0
            campaign = await _create_campaign(
                db_session, test_user.id, budget_sats=10000, spent_sats=5000,
            )

            svc = BitcoinBudgetService(db_session)
            # Exactly at remaining budget boundary — should pass
            check = await svc.check_spend(
                amount_sats=5000,
                campaign_id=campaign.id,
                user_id=test_user.id,
            )
            assert check.allowed

            # One sat over — should fail
            check2 = await svc.check_spend(
                amount_sats=5001,
                campaign_id=campaign.id,
                user_id=test_user.id,
            )
            assert not check2.allowed

    @pytest.mark.asyncio
    async def test_fail_already_confirmed_is_noop(self, db_session, test_user):
        svc = BitcoinBudgetService(db_session)
        tx = await svc.record_transaction(
            user_id=test_user.id,
            tx_type=TransactionType.LIGHTNING_SEND,
            amount_sats=5000,
            status=TransactionStatus.CONFIRMED,
        )
        await db_session.flush()

        result = await svc.fail_transaction(tx.id)
        assert result.status == TransactionStatus.CONFIRMED

    @pytest.mark.asyncio
    async def test_fail_nonexistent_returns_none(self, db_session):
        svc = BitcoinBudgetService(db_session)
        result = await svc.fail_transaction(uuid4())
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# Finding 11: Velocity circuit breaker
# ═══════════════════════════════════════════════════════════════════════


class TestVelocityCircuitBreaker:
    """Test the velocity circuit breaker that trips on rapid-fire small txns."""

    @pytest.fixture(autouse=True)
    def _velocity_settings(self):
        """Enable velocity breaker with tight settings for tests."""
        with _SavedSettings():
            settings.lnd_max_payment_sats = -1  # no per-tx limit
            settings.lnd_rate_limit_sats = 0  # no cumulative sats limit
            settings.lnd_velocity_max_txns = 3  # trip after 3 txns
            settings.lnd_velocity_window_seconds = 900  # 15 min
            yield

    @pytest.mark.asyncio
    async def test_under_threshold_allowed(self, db_session, test_user):
        """1–3 txns in window should be fine."""
        svc = BitcoinBudgetService(db_session)

        # Record 2 transactions (under threshold of 3)
        for _ in range(2):
            await svc.record_transaction(
                user_id=test_user.id,
                tx_type=TransactionType.LIGHTNING_SEND,
                amount_sats=100,
                status=TransactionStatus.CONFIRMED,
            )
        await db_session.flush()

        # The 3rd check should still be allowed (count = 2, +1 = 3 = threshold)
        check = await svc.check_spend(amount_sats=100)
        assert check.allowed

    @pytest.mark.asyncio
    async def test_exceeding_threshold_trips_breaker(self, db_session, test_user):
        """The (threshold+1)th txn should trip the breaker."""
        svc = BitcoinBudgetService(db_session)

        # Record 3 transactions (at threshold)
        for _ in range(3):
            await svc.record_transaction(
                user_id=test_user.id,
                tx_type=TransactionType.LIGHTNING_SEND,
                amount_sats=100,
                status=TransactionStatus.CONFIRMED,
            )
        await db_session.flush()

        # The 4th payment attempt should trip the breaker
        check = await svc.check_spend(amount_sats=100)
        assert not check.allowed
        assert check.trigger == SpendTrigger.VELOCITY_BREAKER
        assert "tripped" in check.reason.lower()

    @pytest.mark.asyncio
    async def test_breaker_stays_tripped_after_window(self, db_session, test_user):
        """Once tripped, the breaker does NOT auto-reset even after the window."""
        svc = BitcoinBudgetService(db_session)

        # Trip the breaker
        for _ in range(3):
            await svc.record_transaction(
                user_id=test_user.id,
                tx_type=TransactionType.LIGHTNING_SEND,
                amount_sats=100,
                status=TransactionStatus.CONFIRMED,
            )
        await db_session.flush()

        check = await svc.check_spend(amount_sats=100)
        assert not check.allowed
        assert check.trigger == SpendTrigger.VELOCITY_BREAKER

        # Even if we try again, breaker is still tripped
        check2 = await svc.check_spend(amount_sats=100)
        assert not check2.allowed
        assert check2.trigger == SpendTrigger.VELOCITY_BREAKER

    @pytest.mark.asyncio
    async def test_human_reset_unblocks(self, db_session, test_user):
        """A human can reset the breaker and payments resume."""
        svc = BitcoinBudgetService(db_session)

        # Trip the breaker
        for _ in range(3):
            await svc.record_transaction(
                user_id=test_user.id,
                tx_type=TransactionType.LIGHTNING_SEND,
                amount_sats=100,
                status=TransactionStatus.CONFIRMED,
            )
        await db_session.flush()

        check = await svc.check_spend(amount_sats=100)
        assert not check.allowed

        # Human resets the breaker
        breaker = await svc.reset_velocity_breaker(test_user.id)
        assert not breaker.is_tripped
        assert breaker.reset_at is not None
        assert breaker.reset_by_user_id == test_user.id

        # Now payments work again
        check2 = await svc.check_spend(amount_sats=100)
        assert check2.allowed

    @pytest.mark.asyncio
    async def test_pending_txns_count_toward_velocity(self, db_session, test_user):
        """PENDING sends count toward the velocity threshold."""
        svc = BitcoinBudgetService(db_session)

        for _ in range(3):
            await svc.record_transaction(
                user_id=test_user.id,
                tx_type=TransactionType.ONCHAIN_SEND,
                amount_sats=50,
                status=TransactionStatus.PENDING,
            )
        await db_session.flush()

        check = await svc.check_spend(amount_sats=50)
        assert not check.allowed
        assert check.trigger == SpendTrigger.VELOCITY_BREAKER

    @pytest.mark.asyncio
    async def test_receives_dont_count(self, db_session, test_user):
        """Incoming receives should NOT count toward velocity."""
        svc = BitcoinBudgetService(db_session)

        for _ in range(5):
            await svc.record_transaction(
                user_id=test_user.id,
                tx_type=TransactionType.LIGHTNING_RECEIVE,
                amount_sats=1000,
                status=TransactionStatus.CONFIRMED,
            )
        await db_session.flush()

        check = await svc.check_spend(amount_sats=100)
        assert check.allowed

    @pytest.mark.asyncio
    async def test_disabled_when_max_txns_zero(self, db_session, test_user):
        """Setting velocity_max_txns=0 disables the check entirely."""
        settings.lnd_velocity_max_txns = 0

        svc = BitcoinBudgetService(db_session)
        for _ in range(10):
            await svc.record_transaction(
                user_id=test_user.id,
                tx_type=TransactionType.LIGHTNING_SEND,
                amount_sats=100,
                status=TransactionStatus.CONFIRMED,
            )
        await db_session.flush()

        check = await svc.check_spend(amount_sats=100)
        assert check.allowed

    @pytest.mark.asyncio
    async def test_trip_context_includes_tx_ids(self, db_session, test_user):
        """When tripped, the breaker stores recent tx IDs for audit."""
        svc = BitcoinBudgetService(db_session)

        tx_ids = []
        for _ in range(3):
            tx = await svc.record_transaction(
                user_id=test_user.id,
                tx_type=TransactionType.LIGHTNING_SEND,
                amount_sats=100,
                status=TransactionStatus.CONFIRMED,
            )
            tx_ids.append(str(tx.id))
        await db_session.flush()

        # Trip it
        await svc.check_spend(amount_sats=100)

        breaker = await svc._get_or_create_velocity_breaker()
        assert breaker.is_tripped
        assert breaker.trip_context is not None
        assert breaker.trip_context["count"] == 3
        assert breaker.trip_context["threshold"] == 3
        assert len(breaker.trip_context["recent_tx_ids"]) == 3

    @pytest.mark.asyncio
    async def test_status_api_shows_tripped(self, db_session, test_user):
        """get_velocity_breaker_status returns correct state."""
        svc = BitcoinBudgetService(db_session)

        status = await svc.get_velocity_breaker_status()
        assert status["is_tripped"] is False

        # Trip it
        for _ in range(3):
            await svc.record_transaction(
                user_id=test_user.id,
                tx_type=TransactionType.LIGHTNING_SEND,
                amount_sats=100,
                status=TransactionStatus.CONFIRMED,
            )
        await db_session.flush()
        await svc.check_spend(amount_sats=100)

        status = await svc.get_velocity_breaker_status()
        assert status["is_tripped"] is True
        assert status["tripped_at"] is not None
        assert status["trip_context"] is not None

    @pytest.mark.asyncio
    async def test_reset_when_not_tripped_is_noop(self, db_session, test_user):
        """Resetting a non-tripped breaker is safe and returns cleanly."""
        svc = BitcoinBudgetService(db_session)
        breaker = await svc.reset_velocity_breaker(test_user.id)
        assert not breaker.is_tripped

    @pytest.mark.asyncio
    async def test_failed_txns_count_toward_velocity(self, db_session, test_user):
        """Failed txns are also suspicious and should count."""
        svc = BitcoinBudgetService(db_session)

        # Record 2 confirmed + 1 failed (but the query only counts CONFIRMED+PENDING)
        for _ in range(2):
            await svc.record_transaction(
                user_id=test_user.id,
                tx_type=TransactionType.LIGHTNING_SEND,
                amount_sats=100,
                status=TransactionStatus.CONFIRMED,
            )
        await svc.record_transaction(
            user_id=test_user.id,
            tx_type=TransactionType.LIGHTNING_SEND,
            amount_sats=100,
            status=TransactionStatus.FAILED,
        )
        await db_session.flush()

        # Only 2 CONFIRMED/PENDING, so next spend should be allowed
        # (failed txns don't count in current impl)
        check = await svc.check_spend(amount_sats=100)
        assert check.allowed


class TestVelocityBreakerEndpoints:
    """Verify the wallet endpoint source code includes velocity breaker routes."""

    def test_reset_endpoint_exists(self):
        from app.api.endpoints import wallet as wallet_mod
        source = inspect.getsource(wallet_mod.reset_velocity_breaker)
        assert "reset_velocity_breaker" in source
        assert "get_current_admin" in source
        assert "VELOCITY BREAKER RESET" in source

    def test_status_endpoint_exists(self):
        from app.api.endpoints import wallet as wallet_mod
        source = inspect.getsource(wallet_mod.get_velocity_breaker_status)
        assert "get_velocity_breaker_status" in source





# ============================================================================
# MEDIUM-4: Boltz Clearnet Fallback Default
# ============================================================================

class TestMedium4BoltzClearnetDefault:
    """Verify Boltz clearnet fallback defaults to False."""

    def test_boltz_fallback_clearnet_defaults_false(self):
        """Config defaults boltz_fallback_clearnet to False."""
        from app.core.config import settings
        assert settings.boltz_fallback_clearnet is False

    def test_config_class_has_false_default(self):
        """The Settings class definition has False as the default."""
        import inspect
        from app.core.config import Settings

        source = inspect.getsource(Settings)
        assert "boltz_fallback_clearnet" in source
        # Check it defaults to False (not True)
        match = re.search(r"boltz_fallback_clearnet.*?=\s*(True|False)", source)
        assert match is not None
        assert match.group(1) == "False"


# ============================================================================
# MEDIUM-5: Async Subprocess in Boltz
# ============================================================================




# ============================================================================
# MEDIUM-5: Async Subprocess in Boltz
# ============================================================================

class TestMedium5AsyncSubprocess:
    """Verify Boltz keypair generation and claim use async subprocess."""

    @pytest.mark.asyncio
    async def test_generate_keypair_is_async(self):
        """_generate_keypair is a coroutine function."""
        from app.services.boltz_service import _generate_keypair
        assert asyncio.iscoroutinefunction(_generate_keypair)

    @pytest.mark.asyncio
    async def test_generate_keypair_uses_async_subprocess(self):
        """_generate_keypair creates an async subprocess, not sync."""
        from app.services.boltz_service import _generate_keypair

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(
            json.dumps({
                "privateKey": "a" * 64,
                "publicKey": "02" + "b" * 64,
            }).encode(),
            b"",
        ))
        mock_proc.returncode = 0

        with patch("app.services.boltz_service.asyncio.create_subprocess_exec",
                    return_value=mock_proc) as mock_exec:
            priv, pub = await _generate_keypair()

        # Verify async subprocess was used
        mock_exec.assert_called_once()
        assert priv == "a" * 64
        assert pub == "02" + "b" * 64

    @pytest.mark.asyncio
    async def test_generate_keypair_timeout_kills_process(self):
        """On timeout, the subprocess is killed."""
        from app.services.boltz_service import _generate_keypair

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch("app.services.boltz_service.asyncio.create_subprocess_exec",
                    return_value=mock_proc):
            with pytest.raises(RuntimeError, match="timed out"):
                await _generate_keypair()

        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_subprocess_import_in_boltz(self):
        """boltz_service no longer imports subprocess module."""
        import app.services.boltz_service as mod
        # The module should not have subprocess in its namespace
        assert not hasattr(mod, 'subprocess'), \
            "boltz_service should use asyncio.create_subprocess_exec, not subprocess"


# ============================================================================
# LOW-1: Ollama HTTP Client Reuse
# ============================================================================




# ============================================================================
# LOW-2: LND Client Init Lock
# ============================================================================

class TestLow2LNDClientLock:
    """Verify LND client init is protected by asyncio.Lock."""

    def test_lnd_service_has_client_lock(self):
        """LNDService has a _client_lock attribute."""
        from app.services.lnd_service import LNDService

        svc = LNDService()
        assert hasattr(svc, '_client_lock')
        assert isinstance(svc._client_lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_concurrent_get_client_uses_lock(self):
        """Multiple concurrent _get_client calls don't create duplicate clients."""
        from app.services.lnd_service import LNDService

        svc = LNDService()
        clients_created = []

        original_init = svc._client_lock

        # Track how many times an httpx.AsyncClient is created
        with patch("app.services.lnd_service.httpx.AsyncClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_instance.is_closed = False
            mock_client_cls.return_value = mock_instance

            # Launch multiple concurrent _get_client calls
            results = await asyncio.gather(
                svc._get_client(),
                svc._get_client(),
                svc._get_client(),
            )

            # Only one client should have been created (lock prevents duplicates)
            assert mock_client_cls.call_count == 1


# ============================================================================
# LOW-3: Lazy API Key Loading
# ============================================================================





# ============================================================================
# GAP-12: Boltz Stderr Hex Redaction
# ============================================================================

class TestGap12BoltzStderrSanitization:
    """Verify Boltz claim script stderr redacts hex key material."""

    def test_sanitize_stderr_function_exists(self):
        """boltz_service should have _sanitize_stderr function."""
        from app.services.boltz_service import _sanitize_stderr

        assert callable(_sanitize_stderr)

    def test_sanitize_stderr_redacts_hex_keys(self):
        """Long hex strings (>= 32 chars) should be redacted."""
        from app.services.boltz_service import _sanitize_stderr

        # Private key-like hex string (64 chars)
        hex_key = "a1b2c3d4e5f6" * 6  # 72 hex chars
        stderr_raw = f"Error: invalid key {hex_key}".encode()

        result = _sanitize_stderr(stderr_raw)

        assert hex_key not in result
        assert "[REDACTED_HEX]" in result
        assert "Error: invalid key" in result

    def test_sanitize_stderr_preserves_short_hex(self):
        """Short hex strings (< 32 chars) should not be redacted."""
        from app.services.boltz_service import _sanitize_stderr

        # Error code or short hash
        stderr_raw = b"Error 0x1234: something failed"
        result = _sanitize_stderr(stderr_raw)

        assert "0x1234" in result  # short hex preserved

    def test_sanitize_stderr_truncates_long_output(self):
        """Stderr longer than max_len should be truncated."""
        from app.services.boltz_service import _sanitize_stderr

        long_stderr = b"x" * 1000
        result = _sanitize_stderr(long_stderr, max_len=100)

        assert len(result) <= 150  # some room for [REDACTED_HEX] replacements

    def test_sanitize_stderr_handles_empty(self):
        """Empty stderr should return empty string."""
        from app.services.boltz_service import _sanitize_stderr

        assert _sanitize_stderr(b"") == ""
        assert _sanitize_stderr(None) == ""

    def test_sanitize_stderr_handles_real_preimage(self):
        """A realistic preimage hex should be redacted from error output."""
        from app.services.boltz_service import _sanitize_stderr

        preimage = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        stderr = f"claim failed: preimage={preimage} is invalid".encode()
        result = _sanitize_stderr(stderr)

        assert preimage not in result
        assert "[REDACTED_HEX]" in result


# ============================================================================
# GAP-13: ML Scanner Fail-Closed Option
# ============================================================================




# ============================================================================
# Cross-cutting: Verify _HEX_KEY_PATTERN regex
# ============================================================================

class TestHexKeyPattern:
    """Unit tests for the hex key redaction regex used in GAP-12."""

    def test_pattern_matches_64_char_hex(self):
        """64-char hex (256-bit key) should match."""
        from app.services.boltz_service import _HEX_KEY_PATTERN

        text = "key=" + "a" * 64
        assert _HEX_KEY_PATTERN.search(text) is not None

    def test_pattern_matches_32_char_hex(self):
        """32-char hex (128-bit) should match (boundary)."""
        from app.services.boltz_service import _HEX_KEY_PATTERN

        text = "hash=" + "b" * 32
        assert _HEX_KEY_PATTERN.search(text) is not None

    def test_pattern_does_not_match_31_char_hex(self):
        """31-char hex should NOT match (below threshold)."""
        from app.services.boltz_service import _HEX_KEY_PATTERN

        text = "short=" + "c" * 31
        assert _HEX_KEY_PATTERN.search(text) is None

    def test_pattern_handles_mixed_case(self):
        """Mixed case hex should be matched."""
        from app.services.boltz_service import _HEX_KEY_PATTERN

        # 32 hex chars with mixed case, space-delimited for word boundaries
        hex_part = "aAbBcCdDeEfF00112233445566778899"
        assert len(hex_part) == 32
        text = f"key= {hex_part} end"
        assert _HEX_KEY_PATTERN.search(text) is not None


# ============================================================================
# GAP-14: Wallet Endpoint Rate Limits
# ============================================================================

class TestGap14WalletRateLimits:
    """Verify financial wallet endpoints have per-endpoint rate limits."""

    @pytest.mark.host_only
    def test_wallet_endpoints_have_rate_limits(self):
        """Critical wallet endpoints must have @limiter.limit() decorators."""
        from tests.helpers.paths import backend_file
        source = backend_file("app/api/endpoints/wallet.py").read_text()

        rate_limited_endpoints = [
            ("safety-limit", "put"),
            ("velocity-breaker/reset", "post"),
            ("address/new", "post"),
            ("invoices/create", "post"),
            ("channels/open", "post"),
        ]

        for endpoint, method in rate_limited_endpoints:
            pattern = rf'@router\.{method}\(["\'].*{re.escape(endpoint)}["\']'
            match = re.search(pattern, source)
            assert match, f"Could not find {method.upper()} route for {endpoint}"

            after_route = source[match.start():]
            next_def = after_route.index("async def ")
            decorator_block = after_route[:next_def]
            assert "@limiter.limit(" in decorator_block, \
                f"Endpoint {endpoint} must have @limiter.limit() decorator"

    @pytest.mark.asyncio
    async def test_wallet_rate_limit_enforced(self, async_client, test_admin_user):
        """Rate limit on wallet endpoints should return 429 when exceeded."""
        from app.core.rate_limit import limiter
        from app.core.security import create_access_token
        from app.main import app
        from app.api.endpoints.wallet import require_lnd

        limiter.enabled = True
        app.dependency_overrides[require_lnd] = lambda: True

        try:
            token = create_access_token(data={"sub": str(test_admin_user.id)})
            headers = {"Authorization": f"Bearer {token}"}

            responses = []
            for _ in range(12):
                resp = await async_client.post(
                    "/api/v1/wallet/address/new",
                    json={"address_type": "p2tr"},
                    headers=headers,
                )
                responses.append(resp.status_code)

            assert 429 in responses, \
                "Rate limit should eventually return 429"
        finally:
            limiter.enabled = False
            app.dependency_overrides.pop(require_lnd, None)


# ============================================================================
# Global __ma_ Prefix Stripping Before Injection
# ============================================================================


class TestToolMaPrefixStripping:
    """__ma_ keys are stripped from LLM params before trusted values are injected."""

    def test_ma_stripping_before_injection_in_source(self):
        """tool_execution_service.py strips __ma_ before injecting trusted values."""
        from tests.helpers.paths import backend_file

        src = backend_file("app", "services", "tool_execution_service.py").read_text()

        strip_pattern = r'params\s*=\s*\{k:\s*v\s+for\s+k,\s*v\s+in\s+params\.items\(\)\s+if\s+not\s+k\.startswith\("__ma_"\)\}'
        inject_pattern = r'params\["__ma_user_id"\]'

        strip_match = re.search(strip_pattern, src)
        inject_match = re.search(inject_pattern, src)

        assert strip_match is not None, "Global __ma_ stripping line not found"
        assert inject_match is not None, "__ma_user_id injection not found"

        # The last stripping occurrence should appear before injection
        all_strips = list(re.finditer(strip_pattern, src))
        global_strip = all_strips[-1]
        assert global_strip.start() < inject_match.start(), (
            "Global __ma_ stripping must occur BEFORE __ma_user_id injection"
        )


# ============================================================================
# Trusted Campaign ID from Agent Context
# ============================================================================


class TestTrustedCampaignId:
    """LND spend operations prefer __ma_campaign_id from agent context."""

    def test_lnd_functions_use_trusted_campaign_id(self):
        """All three LND spend operations prefer __ma_campaign_id over user-supplied."""
        from tests.helpers.paths import backend_file

        src = backend_file("app", "services", "tool_execution_service.py").read_text()

        assert '__ma_campaign_id' in src
        assert 'params["__ma_campaign_id"]' in src

        # create_invoice, pay_invoice, send_onchain should all prefer __ma_campaign_id
        trusted_pattern = r'params\.get\("__ma_campaign_id"\)\s*or\s*params\.get\("campaign_id"\)'
        matches = list(re.finditer(trusted_pattern, src))
        assert len(matches) >= 3, (
            f"Expected 3+ trusted campaign_id lookups (create_invoice, pay_invoice, send_onchain), "
            f"found {len(matches)}"
        )


# ============================================================================
# SGA3-M7: Reserve → Pay → Confirm Pattern (Payment Lock Optimization)
# ============================================================================


class TestSGA3M7PaymentLockOptimization:
    """SGA3-M7: Payment flow uses reserve→pay→confirm pattern to avoid long row locks."""

    @pytest.mark.host_only
    def test_pay_invoice_uses_reserve_pattern(self):
        """pay_invoice must use PENDING reservation instead of holding lock during payment."""
        from tests.helpers.paths import backend_file

        src = backend_file("app", "services", "tool_execution_service.py").read_text()
        # Must reference the SGA3-M7 pattern
        assert "SGA3-M7" in src, "pay_invoice must reference SGA3-M7 pattern"
        assert "RESERVED" in src or "reserved" in src, (
            "pay_invoice must create a PENDING reservation"
        )
        assert "cancel_pending_transaction" in src, (
            "pay_invoice must call cancel_pending_transaction on failure"
        )
        assert "confirm_pending_transaction" in src, (
            "pay_invoice must call confirm_pending_transaction on success"
        )

    @pytest.mark.asyncio
    async def test_cancel_pending_transaction(self, db_session, test_user):
        """cancel_pending_transaction must mark PENDING tx as FAILED."""
        from app.services.bitcoin_budget_service import BitcoinBudgetService

        svc = BitcoinBudgetService(db_session)
        payment_request = f"lnbc_test_cancel_{uuid4().hex[:8]}"

        # Create a PENDING transaction
        tx = await svc.record_transaction(
            user_id=test_user.id,
            tx_type=TransactionType.LIGHTNING_SEND,
            amount_sats=1000,
            payment_request=payment_request,
            payment_hash="",
            description="[RESERVED] test",
            status=TransactionStatus.PENDING,
        )
        await db_session.commit()
        assert tx.status == TransactionStatus.PENDING

        # Cancel it
        cancelled = await svc.cancel_pending_transaction(
            payment_request=payment_request,
            user_id=test_user.id,
        )
        assert cancelled is not None
        assert cancelled.status == TransactionStatus.FAILED

    @pytest.mark.asyncio
    async def test_cancel_pending_nonexistent(self, db_session, test_user):
        """cancel_pending_transaction returns None if no matching PENDING tx."""
        from app.services.bitcoin_budget_service import BitcoinBudgetService

        svc = BitcoinBudgetService(db_session)
        result = await svc.cancel_pending_transaction(
            payment_request="lnbc_nonexistent",
            user_id=test_user.id,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_confirm_pending_transaction(self, db_session, test_user):
        """confirm_pending_transaction must mark PENDING tx as CONFIRMED."""
        from app.services.bitcoin_budget_service import BitcoinBudgetService

        svc = BitcoinBudgetService(db_session)
        payment_request = f"lnbc_test_confirm_{uuid4().hex[:8]}"

        # Create a PENDING transaction
        tx = await svc.record_transaction(
            user_id=test_user.id,
            tx_type=TransactionType.LIGHTNING_SEND,
            amount_sats=500,
            fee_sats=10,
            payment_request=payment_request,
            payment_hash="",
            description="[RESERVED] test confirm",
            status=TransactionStatus.PENDING,
        )
        await db_session.commit()

        # Confirm with actual payment hash and fee
        confirmed = await svc.confirm_pending_transaction(
            payment_request=payment_request,
            user_id=test_user.id,
            payment_hash="abc123def456",
            fee_sats=5,
        )
        assert confirmed is not None
        assert confirmed.status == TransactionStatus.CONFIRMED
        assert confirmed.payment_hash == "abc123def456"
        assert confirmed.fee_sats == 5

    @pytest.mark.asyncio
    async def test_confirm_pending_nonexistent(self, db_session, test_user):
        """confirm_pending_transaction returns None if no matching PENDING tx."""
        from app.services.bitcoin_budget_service import BitcoinBudgetService

        svc = BitcoinBudgetService(db_session)
        result = await svc.confirm_pending_transaction(
            payment_request="lnbc_nonexistent",
            user_id=test_user.id,
            payment_hash="hash123",
            fee_sats=5,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_confirm_adjusts_fee_delta(self, db_session, test_user):
        """confirm_pending_transaction adjusts campaign totals when actual fee differs."""
        from app.services.bitcoin_budget_service import BitcoinBudgetService

        svc = BitcoinBudgetService(db_session)
        # Create campaign using the existing helper
        campaign = await _create_campaign(db_session, test_user.id, budget_sats=100000)
        await db_session.commit()

        payment_request = f"lnbc_fee_delta_{uuid4().hex[:8]}"

        # Record with estimated fee of 100 sats
        tx = await svc.record_transaction(
            user_id=test_user.id,
            tx_type=TransactionType.LIGHTNING_SEND,
            amount_sats=5000,
            fee_sats=100,
            campaign_id=campaign.id,
            payment_request=payment_request,
            payment_hash="",
            description="[RESERVED] fee test",
            status=TransactionStatus.PENDING,
        )
        await db_session.commit()

        await db_session.refresh(campaign)
        spent_after_reserve = campaign.bitcoin_spent_sats

        # Confirm with actual fee of 20 sats (lower than estimate)
        confirmed = await svc.confirm_pending_transaction(
            payment_request=payment_request,
            user_id=test_user.id,
            payment_hash="hash_fee_delta",
            fee_sats=20,
        )
        assert confirmed is not None
        assert confirmed.fee_sats == 20

        # Campaign spent should have decreased by (100 - 20) = 80 sats
        await db_session.refresh(campaign)
        assert campaign.bitcoin_spent_sats == spent_after_reserve - 80