"""
Unit tests for Bitcoin Budget Service.

Tests budget enforcement, transaction recording, approval workflow,
and campaign totals. Uses real SQLite DB via conftest fixtures.
"""
import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from uuid import uuid4, UUID
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.bitcoin_budget import (
    BitcoinTransaction,
    BitcoinSpendApproval,
    TransactionType,
    TransactionStatus,
    SpendApprovalStatus,
    SpendTrigger,
)
from app.services.bitcoin_budget_service import (
    BitcoinBudgetService,
    BudgetCheckResult,
    BUDGET_THRESHOLDS,
)


# ============================================================================
# BudgetCheckResult
# ============================================================================

class TestBudgetCheckResult:
    """Tests for BudgetCheckResult value object."""

    def test_allowed_result(self):
        """Allowed result has correct attributes."""
        result = BudgetCheckResult(allowed=True, budget_context={"amount_sats": 100})
        assert result.allowed is True
        assert result.trigger is None
        assert result.reason is None
        assert result.budget_context == {"amount_sats": 100}

    def test_denied_result(self):
        """Denied result captures trigger and reason."""
        result = BudgetCheckResult(
            allowed=False,
            trigger=SpendTrigger.OVER_BUDGET,
            reason="Exceeds budget",
            budget_context={"total_sats": 500},
        )
        assert result.allowed is False
        assert result.trigger == SpendTrigger.OVER_BUDGET
        assert "Exceeds budget" in result.reason

    def test_repr(self):
        """BudgetCheckResult has useful repr."""
        result = BudgetCheckResult(allowed=True)
        assert "allowed=True" in repr(result)


# ============================================================================
# BitcoinBudgetService — Budget checks (mocked DB)
# ============================================================================

class TestBudgetChecks:
    """Tests for spend authorization checks."""

    @pytest.mark.asyncio
    @patch("app.services.bitcoin_budget_service.settings")
    async def test_check_spend_global_limit_exceeded(self, mock_settings):
        """Spend denied when exceeding global safety limit."""
        mock_settings.lnd_max_payment_sats = 100000

        mock_db = AsyncMock()
        service = BitcoinBudgetService(mock_db)

        result = await service.check_spend(amount_sats=200000)
        assert result.allowed is False
        assert result.trigger == SpendTrigger.GLOBAL_LIMIT
        assert "200000" in result.reason
        assert "100000" in result.reason

    @pytest.mark.asyncio
    @patch("app.services.bitcoin_budget_service.settings")
    async def test_check_spend_global_limit_ok(self, mock_settings):
        """Spend allowed when under global safety limit (no campaign)."""
        mock_settings.lnd_max_payment_sats = 100000
        mock_settings.lnd_rate_limit_sats = 0
        mock_settings.lnd_rate_limit_window_seconds = 0
        mock_settings.lnd_velocity_max_txns = 0
        mock_settings.lnd_velocity_window_seconds = 0

        mock_db = AsyncMock()
        service = BitcoinBudgetService(mock_db)

        result = await service.check_spend(amount_sats=50000)
        assert result.allowed is True

    @pytest.mark.asyncio
    @patch("app.services.bitcoin_budget_service.settings")
    async def test_check_spend_global_limit_negative_one_means_no_limit(self, mock_settings):
        """No global limit when set to -1."""
        mock_settings.lnd_max_payment_sats = -1
        mock_settings.lnd_rate_limit_sats = 0
        mock_settings.lnd_rate_limit_window_seconds = 0
        mock_settings.lnd_velocity_max_txns = 0
        mock_settings.lnd_velocity_window_seconds = 0

        mock_db = AsyncMock()
        service = BitcoinBudgetService(mock_db)

        result = await service.check_spend(amount_sats=9999999)
        assert result.allowed is True

    @pytest.mark.asyncio
    @patch("app.services.bitcoin_budget_service.settings")
    async def test_check_spend_zero_requires_all_approval(self, mock_settings):
        """Setting 0 blocks all transactions (require approval)."""
        mock_settings.lnd_max_payment_sats = 0

        mock_db = AsyncMock()
        service = BitcoinBudgetService(mock_db)

        result = await service.check_spend(amount_sats=1)
        assert result.allowed is False
        assert result.trigger == SpendTrigger.GLOBAL_LIMIT
        assert "require approval" in result.reason.lower()

    @pytest.mark.asyncio
    @patch("app.services.bitcoin_budget_service.settings")
    async def test_check_spend_campaign_over_budget(self, mock_settings):
        """Spend denied when it would exceed campaign budget."""
        mock_settings.lnd_max_payment_sats = -1  # no global limit
        mock_settings.lnd_rate_limit_sats = 0
        mock_settings.lnd_rate_limit_window_seconds = 0
        mock_settings.lnd_velocity_max_txns = 0
        mock_settings.lnd_velocity_window_seconds = 0

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("FOR UPDATE not supported"))
        service = BitcoinBudgetService(mock_db)

        # Mock campaign with existing spend
        user_id = uuid4()
        mock_campaign = MagicMock()
        mock_campaign.bitcoin_budget_sats = 100000
        mock_campaign.bitcoin_spent_sats = 90000
        mock_campaign.bitcoin_received_sats = 0
        mock_campaign.user_id = user_id
        service._get_campaign = AsyncMock(return_value=mock_campaign)

        campaign_id = uuid4()
        result = await service.check_spend(
            amount_sats=15000, campaign_id=campaign_id, user_id=user_id
        )
        assert result.allowed is False
        assert result.trigger == SpendTrigger.OVER_BUDGET
        assert result.budget_context["campaign_remaining_sats"] == 10000

    @pytest.mark.asyncio
    @patch("app.services.bitcoin_budget_service.settings")
    async def test_check_spend_campaign_within_budget(self, mock_settings):
        """Spend allowed when within campaign budget."""
        mock_settings.lnd_max_payment_sats = -1
        mock_settings.lnd_rate_limit_sats = 0
        mock_settings.lnd_rate_limit_window_seconds = 0
        mock_settings.lnd_velocity_max_txns = 0
        mock_settings.lnd_velocity_window_seconds = 0

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("FOR UPDATE not supported"))
        service = BitcoinBudgetService(mock_db)

        user_id = uuid4()
        mock_campaign = MagicMock()
        mock_campaign.bitcoin_budget_sats = 100000
        mock_campaign.bitcoin_spent_sats = 50000
        mock_campaign.bitcoin_received_sats = 0
        mock_campaign.user_id = user_id
        service._get_campaign = AsyncMock(return_value=mock_campaign)

        campaign_id = uuid4()
        result = await service.check_spend(
            amount_sats=25000, campaign_id=campaign_id, user_id=user_id
        )
        assert result.allowed is True

    @pytest.mark.asyncio
    @patch("app.services.bitcoin_budget_service.settings")
    async def test_check_spend_no_budget_set(self, mock_settings):
        """Spend denied when campaign has no budget set."""
        mock_settings.lnd_max_payment_sats = -1
        mock_settings.lnd_rate_limit_sats = 0
        mock_settings.lnd_rate_limit_window_seconds = 0
        mock_settings.lnd_velocity_max_txns = 0
        mock_settings.lnd_velocity_window_seconds = 0

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("FOR UPDATE not supported"))
        service = BitcoinBudgetService(mock_db)

        user_id = uuid4()
        mock_campaign = MagicMock()
        mock_campaign.bitcoin_budget_sats = None
        mock_campaign.bitcoin_spent_sats = 0
        mock_campaign.bitcoin_received_sats = 0
        mock_campaign.user_id = user_id
        service._get_campaign = AsyncMock(return_value=mock_campaign)

        campaign_id = uuid4()
        result = await service.check_spend(
            amount_sats=5000, campaign_id=campaign_id, user_id=user_id
        )
        assert result.allowed is False
        assert result.trigger == SpendTrigger.NO_BUDGET

    @pytest.mark.asyncio
    @patch("app.services.bitcoin_budget_service.settings")
    async def test_check_spend_includes_fees(self, mock_settings):
        """Fee is added to spend amount for budget check."""
        mock_settings.lnd_max_payment_sats = -1
        mock_settings.lnd_rate_limit_sats = 0
        mock_settings.lnd_rate_limit_window_seconds = 0
        mock_settings.lnd_velocity_max_txns = 0
        mock_settings.lnd_velocity_window_seconds = 0

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("FOR UPDATE not supported"))
        service = BitcoinBudgetService(mock_db)

        user_id = uuid4()
        mock_campaign = MagicMock()
        mock_campaign.bitcoin_budget_sats = 10000
        mock_campaign.bitcoin_spent_sats = 5000
        mock_campaign.bitcoin_received_sats = 0
        mock_campaign.user_id = user_id
        service._get_campaign = AsyncMock(return_value=mock_campaign)

        campaign_id = uuid4()
        # Amount (4000) fits, but amount + fee (4000+2000=6000) exceeds remaining (5000)
        result = await service.check_spend(
            amount_sats=4000, campaign_id=campaign_id, fee_sats=2000, user_id=user_id
        )
        assert result.allowed is False
        assert result.trigger == SpendTrigger.OVER_BUDGET

    @pytest.mark.asyncio
    @patch("app.services.bitcoin_budget_service.settings")
    async def test_check_spend_campaign_not_found(self, mock_settings):
        """Spend allowed when campaign_id given but campaign not found."""
        mock_settings.lnd_max_payment_sats = -1
        mock_settings.lnd_rate_limit_sats = 0
        mock_settings.lnd_rate_limit_window_seconds = 0
        mock_settings.lnd_velocity_max_txns = 0
        mock_settings.lnd_velocity_window_seconds = 0

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("FOR UPDATE not supported"))
        service = BitcoinBudgetService(mock_db)
        service._get_campaign = AsyncMock(return_value=None)

        result = await service.check_spend(
            amount_sats=5000, campaign_id=uuid4()
        )
        assert result.allowed is True


# ============================================================================
# BitcoinBudgetService — Transaction recording (mocked DB)
# ============================================================================

class TestTransactionRecording:
    """Tests for transaction recording in the immutable ledger."""

    @pytest.mark.asyncio
    async def test_record_transaction_pending(self):
        """Pending transaction is recorded without updating campaign totals."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        service = BitcoinBudgetService(mock_db)

        user_id = uuid4()
        tx = await service.record_transaction(
            user_id=user_id,
            tx_type=TransactionType.LIGHTNING_SEND,
            amount_sats=10000,
            fee_sats=5,
            status=TransactionStatus.PENDING,
        )

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()

        added_tx = mock_db.add.call_args[0][0]
        assert added_tx.amount_sats == 10000
        assert added_tx.fee_sats == 5
        assert added_tx.status == TransactionStatus.PENDING
        assert added_tx.confirmed_at is None

    @pytest.mark.asyncio
    async def test_record_transaction_confirmed_updates_totals(self):
        """Confirmed transaction triggers campaign total update."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        service = BitcoinBudgetService(mock_db)
        service._update_campaign_totals = AsyncMock()

        campaign_id = uuid4()
        user_id = uuid4()
        tx = await service.record_transaction(
            user_id=user_id,
            tx_type=TransactionType.LIGHTNING_SEND,
            amount_sats=10000,
            campaign_id=campaign_id,
            status=TransactionStatus.CONFIRMED,
        )

        service._update_campaign_totals.assert_called_once_with(
            campaign_id, TransactionType.LIGHTNING_SEND, 10000, 0
        )
        added_tx = mock_db.add.call_args[0][0]
        assert added_tx.confirmed_at is not None

    @pytest.mark.asyncio
    async def test_record_transaction_with_notes(self):
        """Transaction records description/notes."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        service = BitcoinBudgetService(mock_db)

        user_id = uuid4()
        await service.record_transaction(
            user_id=user_id,
            tx_type=TransactionType.ONCHAIN_SEND,
            amount_sats=50000,
            description="Payment for hosting services",
            status=TransactionStatus.PENDING,
        )

        added_tx = mock_db.add.call_args[0][0]
        assert added_tx.description == "Payment for hosting services"

    @pytest.mark.asyncio
    async def test_record_transaction_with_payment_metadata(self):
        """Transaction records payment hash, request, txid."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        service = BitcoinBudgetService(mock_db)

        user_id = uuid4()
        await service.record_transaction(
            user_id=user_id,
            tx_type=TransactionType.LIGHTNING_SEND,
            amount_sats=10000,
            payment_hash="abc123",
            payment_request="lnbc10u1p...",
            status=TransactionStatus.CONFIRMED,
        )

        added_tx = mock_db.add.call_args[0][0]
        assert added_tx.payment_hash == "abc123"
        assert added_tx.payment_request == "lnbc10u1p..."


# ============================================================================
# BitcoinBudgetService — Transaction state transitions (mocked DB)
# ============================================================================

class TestTransactionStateTransitions:
    """Tests for confirm/fail transaction transitions."""

    @pytest.mark.asyncio
    async def test_confirm_transaction_updates_status(self):
        """Confirming a pending RECEIVE transaction sets status and updates totals."""
        mock_tx = MagicMock()
        mock_tx.status = TransactionStatus.PENDING
        mock_tx.campaign_id = uuid4()
        mock_tx.tx_type = TransactionType.LIGHTNING_RECEIVE
        mock_tx.amount_sats = 5000
        mock_tx.fee_sats = 10

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tx

        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        service = BitcoinBudgetService(mock_db)
        service._update_campaign_totals = AsyncMock()

        result = await service.confirm_transaction(uuid4())
        assert mock_tx.status == TransactionStatus.CONFIRMED
        assert mock_tx.confirmed_at is not None
        service._update_campaign_totals.assert_called_once()

    @pytest.mark.asyncio
    async def test_confirm_already_confirmed_noop(self):
        """Cannot confirm a non-pending transaction."""
        mock_tx = MagicMock()
        mock_tx.status = TransactionStatus.CONFIRMED

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tx

        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        service = BitcoinBudgetService(mock_db)

        result = await service.confirm_transaction(uuid4())
        assert result.status == TransactionStatus.CONFIRMED

    @pytest.mark.asyncio
    async def test_confirm_nonexistent_returns_none(self):
        """Confirming a missing transaction returns None."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        service = BitcoinBudgetService(mock_db)

        result = await service.confirm_transaction(uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_fail_transaction(self):
        """Failing a pending transaction sets status to FAILED."""
        mock_tx = MagicMock()
        mock_tx.status = TransactionStatus.PENDING

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_tx

        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        service = BitcoinBudgetService(mock_db)

        result = await service.fail_transaction(uuid4())
        assert mock_tx.status == TransactionStatus.FAILED


# ============================================================================
# BitcoinBudgetService — Approval workflow (mocked DB)
# ============================================================================

class TestApprovalWorkflow:
    """Tests for spend approval request lifecycle."""

    @pytest.mark.asyncio
    async def test_create_approval_request(self):
        """Creates an approval request with all fields."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        service = BitcoinBudgetService(mock_db)

        user_id = uuid4()
        campaign_id = uuid4()
        approval = await service.create_approval_request(
            requested_by_id=user_id,
            trigger=SpendTrigger.OVER_BUDGET,
            amount_sats=50000,
            description="Need to pay for API access",
            budget_context={"campaign_remaining_sats": 10000},
            campaign_id=campaign_id,
            fee_estimate_sats=500,
            payment_request="lnbc50u1p...",
        )

        mock_db.add.assert_called_once()
        added = mock_db.add.call_args[0][0]
        assert added.amount_sats == 50000
        assert added.trigger == SpendTrigger.OVER_BUDGET
        assert added.description == "Need to pay for API access"
        assert added.payment_request == "lnbc50u1p..."

    @pytest.mark.asyncio
    async def test_review_approval_approve(self):
        """Approving a pending approval sets status and reviewer."""
        mock_approval = MagicMock()
        mock_approval.status = SpendApprovalStatus.PENDING
        mock_approval.can_be_reviewed.return_value = True

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_approval

        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        service = BitcoinBudgetService(mock_db)

        reviewer_id = uuid4()
        result = await service.review_approval(
            approval_id=uuid4(),
            reviewed_by_id=reviewer_id,
            approved=True,
            review_notes="Looks good, proceed.",
        )
        assert mock_approval.status == SpendApprovalStatus.APPROVED
        assert mock_approval.reviewed_by_id == reviewer_id
        assert mock_approval.review_notes == "Looks good, proceed."
        assert mock_approval.reviewed_at is not None

    @pytest.mark.asyncio
    async def test_review_approval_reject(self):
        """Rejecting an approval sets REJECTED status."""
        mock_approval = MagicMock()
        mock_approval.status = SpendApprovalStatus.PENDING
        mock_approval.can_be_reviewed.return_value = True

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_approval

        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        service = BitcoinBudgetService(mock_db)

        result = await service.review_approval(
            approval_id=uuid4(),
            reviewed_by_id=uuid4(),
            approved=False,
            review_notes="Too expensive.",
        )
        assert mock_approval.status == SpendApprovalStatus.REJECTED

    @pytest.mark.asyncio
    async def test_review_expired_approval(self):
        """Cannot review an expired approval."""
        mock_approval = MagicMock()
        mock_approval.status = SpendApprovalStatus.PENDING
        mock_approval.can_be_reviewed.return_value = False
        mock_approval.is_expired.return_value = True

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_approval

        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        service = BitcoinBudgetService(mock_db)

        result = await service.review_approval(
            approval_id=uuid4(), reviewed_by_id=uuid4(), approved=True
        )
        # Status should remain unchanged
        assert mock_approval.status == SpendApprovalStatus.PENDING

    @pytest.mark.asyncio
    async def test_review_nonexistent_approval(self):
        """Reviewing missing approval returns None."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        mock_db = AsyncMock()
        mock_db.execute.return_value = mock_result

        service = BitcoinBudgetService(mock_db)

        result = await service.review_approval(
            approval_id=uuid4(), reviewed_by_id=uuid4(), approved=True
        )
        assert result is None


# ============================================================================
# BitcoinBudgetService — Campaign totals update
# ============================================================================

class TestCampaignTotalsUpdate:
    """Tests for campaign running total updates.
    
    SA2-06: _update_campaign_totals now uses atomic SQL UPDATE (not ORM
    read-modify-write), so we verify db.execute is called with the correct
    UPDATE statement rather than checking in-memory mock attributes.
    """

    @pytest.mark.asyncio
    async def test_update_totals_lightning_send(self):
        """Lightning send issues atomic UPDATE adding amount + fee to spent_sats."""
        campaign_id = uuid4()
        mock_campaign = MagicMock()
        mock_campaign.bitcoin_budget_sats = None  # no threshold check

        mock_db = AsyncMock()
        service = BitcoinBudgetService(mock_db)
        service._get_campaign = AsyncMock(return_value=mock_campaign)
        service._check_budget_thresholds = AsyncMock()

        await service._update_campaign_totals(
            campaign_id, TransactionType.LIGHTNING_SEND, 5000, 10
        )
        # Verify an atomic SQL UPDATE was issued (SA2-06)
        mock_db.execute.assert_called()
        mock_db.flush.assert_called()
        # Verify threshold check was called after re-fetch
        service._get_campaign.assert_called_once_with(campaign_id)
        service._check_budget_thresholds.assert_called_once_with(mock_campaign)

    @pytest.mark.asyncio
    async def test_update_totals_onchain_receive(self):
        """On-chain receive issues atomic UPDATE adding amount to received_sats."""
        campaign_id = uuid4()
        mock_db = AsyncMock()
        service = BitcoinBudgetService(mock_db)
        service._get_campaign = AsyncMock(return_value=None)

        await service._update_campaign_totals(
            campaign_id, TransactionType.ONCHAIN_RECEIVE, 20000, 0
        )
        # Verify an atomic SQL UPDATE was issued (SA2-06)
        mock_db.execute.assert_called()
        mock_db.flush.assert_called()

    @pytest.mark.asyncio
    async def test_update_totals_send_triggers_threshold_check(self):
        """Send types re-fetch campaign after atomic update for threshold check."""
        campaign_id = uuid4()
        mock_campaign = MagicMock()
        mock_campaign.bitcoin_budget_sats = 100000

        mock_db = AsyncMock()
        service = BitcoinBudgetService(mock_db)
        service._get_campaign = AsyncMock(return_value=mock_campaign)
        service._check_budget_thresholds = AsyncMock()

        await service._update_campaign_totals(
            campaign_id, TransactionType.LIGHTNING_SEND, 1000, 0
        )
        service._get_campaign.assert_called_once_with(campaign_id)
        service._check_budget_thresholds.assert_called_once_with(mock_campaign)


# ============================================================================
# BitcoinBudgetService — Threshold alerts
# ============================================================================

class TestBudgetThresholds:
    """Tests for budget threshold alert detection."""

    @pytest.mark.asyncio
    async def test_threshold_80_percent(self):
        """80% threshold crossed triggers notification."""
        mock_campaign = MagicMock()
        mock_campaign.id = uuid4()
        mock_campaign.user_id = uuid4()
        mock_campaign.title = "Test Campaign"
        mock_campaign.bitcoin_budget_sats = 100000
        mock_campaign.bitcoin_spent_sats = 82000  # 82%

        mock_db = AsyncMock()
        service = BitcoinBudgetService(mock_db)

        with patch("app.services.notification_service.NotificationService") as MockNotifSvc, \
             patch("app.services.campaign_progress_service.campaign_progress_service") as mock_ws:
            mock_notif_instance = MagicMock()
            mock_notif_instance.notify_budget_threshold = AsyncMock()
            MockNotifSvc.return_value = mock_notif_instance

            mock_ws.emit_budget_warning = AsyncMock()

            await service._check_budget_thresholds(mock_campaign)

            mock_notif_instance.notify_budget_threshold.assert_called_once()
            call_kwargs = mock_notif_instance.notify_budget_threshold.call_args.kwargs
            assert call_kwargs["threshold_label"] == "80%"
            assert call_kwargs["severity"] == "warning"

    @pytest.mark.asyncio
    async def test_threshold_95_percent_takes_precedence(self):
        """Highest crossed threshold (95%) is used when multiple are crossed."""
        mock_campaign = MagicMock()
        mock_campaign.id = uuid4()
        mock_campaign.user_id = uuid4()
        mock_campaign.title = "Test Campaign"
        mock_campaign.bitcoin_budget_sats = 100000
        mock_campaign.bitcoin_spent_sats = 96000  # 96% — crosses 80%, 90%, 95%

        mock_db = AsyncMock()
        service = BitcoinBudgetService(mock_db)

        with patch("app.services.notification_service.NotificationService") as MockNotifSvc, \
             patch("app.services.campaign_progress_service.campaign_progress_service") as mock_ws:
            mock_notif_instance = MagicMock()
            mock_notif_instance.notify_budget_threshold = AsyncMock()
            MockNotifSvc.return_value = mock_notif_instance
            mock_ws.emit_budget_warning = AsyncMock()

            await service._check_budget_thresholds(mock_campaign)

            call_kwargs = mock_notif_instance.notify_budget_threshold.call_args.kwargs
            assert call_kwargs["threshold_label"] == "95%"
            assert call_kwargs["severity"] == "danger"

    @pytest.mark.asyncio
    async def test_no_threshold_under_80_percent(self):
        """No notification when under 80%."""
        mock_campaign = MagicMock()
        mock_campaign.bitcoin_budget_sats = 100000
        mock_campaign.bitcoin_spent_sats = 50000  # 50%

        mock_db = AsyncMock()
        service = BitcoinBudgetService(mock_db)

        # This should be a no-op (no notification service imported)
        await service._check_budget_thresholds(mock_campaign)

    @pytest.mark.asyncio
    async def test_no_threshold_no_budget(self):
        """No notification when campaign has no budget."""
        mock_campaign = MagicMock()
        mock_campaign.bitcoin_budget_sats = None
        mock_campaign.bitcoin_spent_sats = 50000

        mock_db = AsyncMock()
        service = BitcoinBudgetService(mock_db)

        await service._check_budget_thresholds(mock_campaign)

    @pytest.mark.asyncio
    async def test_no_threshold_zero_budget(self):
        """No notification when budget is zero."""
        mock_campaign = MagicMock()
        mock_campaign.bitcoin_budget_sats = 0
        mock_campaign.bitcoin_spent_sats = 0

        mock_db = AsyncMock()
        service = BitcoinBudgetService(mock_db)

        await service._check_budget_thresholds(mock_campaign)


# ============================================================================
# BitcoinSpendApproval model — expiry logic
# ============================================================================

class TestSpendApprovalModel:
    """Tests for BitcoinSpendApproval model methods."""

    def test_is_expired_no_expiry(self):
        """Not expired when no expiry date set."""
        approval = MagicMock(spec=BitcoinSpendApproval)
        approval.expires_at = None
        approval.is_expired = BitcoinSpendApproval.is_expired.__get__(approval)
        assert approval.is_expired() is False

    def test_is_expired_future(self):
        """Not expired when expiry is in the future."""
        approval = MagicMock(spec=BitcoinSpendApproval)
        approval.expires_at = datetime.utcnow() + timedelta(hours=1)
        approval.is_expired = BitcoinSpendApproval.is_expired.__get__(approval)
        assert approval.is_expired() is False

    def test_is_expired_past(self):
        """Expired when expiry is in the past."""
        approval = MagicMock(spec=BitcoinSpendApproval)
        approval.expires_at = datetime.utcnow() - timedelta(hours=1)
        approval.is_expired = BitcoinSpendApproval.is_expired.__get__(approval)
        assert approval.is_expired() is True

    def test_can_be_reviewed_pending_not_expired(self):
        """Pending non-expired approval can be reviewed."""
        approval = MagicMock(spec=BitcoinSpendApproval)
        approval.status = SpendApprovalStatus.PENDING
        approval.expires_at = None
        approval.is_expired = BitcoinSpendApproval.is_expired.__get__(approval)
        approval.can_be_reviewed = BitcoinSpendApproval.can_be_reviewed.__get__(approval)
        assert approval.can_be_reviewed() is True

    def test_can_be_reviewed_expired(self):
        """Expired approval cannot be reviewed."""
        approval = MagicMock(spec=BitcoinSpendApproval)
        approval.status = SpendApprovalStatus.PENDING
        approval.expires_at = datetime.utcnow() - timedelta(hours=1)
        approval.is_expired = BitcoinSpendApproval.is_expired.__get__(approval)
        approval.can_be_reviewed = BitcoinSpendApproval.can_be_reviewed.__get__(approval)
        assert approval.can_be_reviewed() is False

    def test_can_be_reviewed_already_approved(self):
        """Already approved approval cannot be reviewed again."""
        approval = MagicMock(spec=BitcoinSpendApproval)
        approval.status = SpendApprovalStatus.APPROVED
        approval.expires_at = None
        approval.is_expired = BitcoinSpendApproval.is_expired.__get__(approval)
        approval.can_be_reviewed = BitcoinSpendApproval.can_be_reviewed.__get__(approval)
        assert approval.can_be_reviewed() is False

    def test_can_be_reviewed_already_rejected(self):
        """Already rejected approval cannot be reviewed again."""
        approval = MagicMock(spec=BitcoinSpendApproval)
        approval.status = SpendApprovalStatus.REJECTED
        approval.expires_at = None
        approval.is_expired = BitcoinSpendApproval.is_expired.__get__(approval)
        approval.can_be_reviewed = BitcoinSpendApproval.can_be_reviewed.__get__(approval)
        assert approval.can_be_reviewed() is False


# ============================================================================
# Bitcoin enums
# ============================================================================

class TestBitcoinEnums:
    """Tests for Bitcoin-related enum values."""

    def test_transaction_type_values(self):
        """All expected transaction types exist."""
        assert TransactionType.LIGHTNING_SEND.value == "lightning_send"
        assert TransactionType.LIGHTNING_RECEIVE.value == "lightning_receive"
        assert TransactionType.ONCHAIN_SEND.value == "onchain_send"
        assert TransactionType.ONCHAIN_RECEIVE.value == "onchain_receive"

    def test_transaction_status_values(self):
        """All expected statuses exist."""
        assert TransactionStatus.PENDING.value == "pending"
        assert TransactionStatus.CONFIRMED.value == "confirmed"
        assert TransactionStatus.FAILED.value == "failed"
        assert TransactionStatus.EXPIRED.value == "expired"

    def test_spend_approval_status_values(self):
        """All expected approval statuses exist."""
        assert SpendApprovalStatus.PENDING.value == "pending"
        assert SpendApprovalStatus.APPROVED.value == "approved"
        assert SpendApprovalStatus.REJECTED.value == "rejected"
        assert SpendApprovalStatus.EXPIRED.value == "expired"
        assert SpendApprovalStatus.CANCELLED.value == "cancelled"

    def test_spend_trigger_values(self):
        """All expected triggers exist."""
        assert SpendTrigger.NO_BUDGET.value == "no_budget"
        assert SpendTrigger.OVER_BUDGET.value == "over_budget"
        assert SpendTrigger.GLOBAL_LIMIT.value == "global_limit"
        assert SpendTrigger.MANUAL_REVIEW.value == "manual_review"
