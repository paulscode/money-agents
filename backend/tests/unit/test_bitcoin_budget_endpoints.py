"""
Unit tests for Bitcoin Budget API endpoints.

Tests the FastAPI budget/approval routes using the async client fixture.
Covers: approval listing, review, cancel, budget summaries, transaction listing.
"""
import pytest
import pytest_asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.models.bitcoin_budget import SpendApprovalStatus, SpendTrigger


# Helper to build mock approval objects
def _mock_approval(**overrides):
    """Create a mock BitcoinSpendApproval with from_attributes support."""
    defaults = {
        "id": uuid4(),
        "campaign_id": uuid4(),
        "requested_by_id": uuid4(),
        "trigger": SpendTrigger.OVER_BUDGET,
        "status": SpendApprovalStatus.PENDING,
        "amount_sats": 50000,
        "fee_estimate_sats": 500,
        "payment_request": "lnbc50u1p...",
        "destination_address": None,
        "description": "Test approval",
        "budget_context": {"campaign_remaining_sats": 10000},
        "reviewed_by_id": None,
        "reviewed_at": None,
        "review_notes": None,
        "advisor_conversation_id": None,
        "created_at": datetime.utcnow(),
        "expires_at": None,
    }
    defaults.update(overrides)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


def _mock_transaction(**overrides):
    """Create a mock BitcoinTransaction."""
    defaults = {
        "id": uuid4(),
        "campaign_id": uuid4(),
        "user_id": uuid4(),
        "tx_type": "lightning_send",
        "status": "confirmed",
        "amount_sats": 10000,
        "fee_sats": 5,
        "payment_hash": "abc123",
        "payment_request": "lnbc10u1p...",
        "txid": None,
        "address": None,
        "description": "Test payment",
        "agent_tool_execution_id": None,
        "approval_id": None,
        "created_at": datetime.utcnow(),
        "confirmed_at": datetime.utcnow(),
    }
    defaults.update(overrides)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


# ============================================================================
# Approval listing
# ============================================================================

class TestApprovalEndpoints:
    """Tests for spend approval endpoints."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.bitcoin_budget.settings")
    @patch("app.api.endpoints.bitcoin_budget.BitcoinBudgetService")
    async def test_list_pending_approvals(self, MockService, mock_settings, async_client, test_user):
        """Lists pending approvals."""
        mock_settings.use_lnd = True

        approvals = [_mock_approval(), _mock_approval()]
        instance = MockService.return_value
        instance.get_pending_approvals = AsyncMock(return_value=approvals)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/bitcoin/approvals",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["approvals"]) == 2

    @pytest.mark.asyncio
    @patch("app.api.endpoints.bitcoin_budget.settings")
    @patch("app.api.endpoints.bitcoin_budget.BitcoinBudgetService")
    async def test_count_pending_approvals(self, MockService, mock_settings, async_client, test_user):
        """Returns pending approval count."""
        mock_settings.use_lnd = True

        instance = MockService.return_value
        instance.count_pending_approvals = AsyncMock(return_value=3)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/bitcoin/approvals/count",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json()["pending_count"] == 3

    @pytest.mark.asyncio
    @patch("app.api.endpoints.bitcoin_budget.settings")
    @patch("app.api.endpoints.bitcoin_budget.BitcoinBudgetService")
    async def test_get_approval_by_id(self, MockService, mock_settings, async_client, test_user):
        """Fetches a specific approval by ID."""
        mock_settings.use_lnd = True

        approval = _mock_approval()
        instance = MockService.return_value
        instance.get_approval = AsyncMock(return_value=approval)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            f"/api/v1/bitcoin/approvals/{approval.id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["amount_sats"] == 50000

    @pytest.mark.asyncio
    @patch("app.api.endpoints.bitcoin_budget.settings")
    @patch("app.api.endpoints.bitcoin_budget.BitcoinBudgetService")
    async def test_get_approval_not_found(self, MockService, mock_settings, async_client, test_user):
        """404 for non-existent approval."""
        mock_settings.use_lnd = True

        instance = MockService.return_value
        instance.get_approval = AsyncMock(return_value=None)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            f"/api/v1/bitcoin/approvals/{uuid4()}",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 404


# ============================================================================
# Approval review
# ============================================================================

class TestApprovalReview:
    """Tests for approval review endpoint."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.bitcoin_budget.settings")
    @patch("app.api.endpoints.bitcoin_budget.BitcoinBudgetService")
    async def test_approve_approval(self, MockService, mock_settings, async_client, test_admin_user):
        """Approving sets status to approved (admin-only per SA-01)."""
        mock_settings.use_lnd = True

        approved = _mock_approval(
            status=SpendApprovalStatus.APPROVED,
            reviewed_by_id=test_admin_user.id,
            reviewed_at=datetime.utcnow(),
        )
        instance = MockService.return_value
        instance.review_approval = AsyncMock(return_value=approved)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            f"/api/v1/bitcoin/approvals/{approved.id}/review",
            json={"action": "approved", "review_notes": "LGTM"},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "approved"

    @pytest.mark.asyncio
    @patch("app.api.endpoints.bitcoin_budget.settings")
    @patch("app.api.endpoints.bitcoin_budget.BitcoinBudgetService")
    async def test_reject_approval(self, MockService, mock_settings, async_client, test_admin_user):
        """Rejecting sets status to rejected (admin-only per SA-01)."""
        mock_settings.use_lnd = True

        rejected = _mock_approval(status=SpendApprovalStatus.REJECTED)
        instance = MockService.return_value
        instance.review_approval = AsyncMock(return_value=rejected)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            f"/api/v1/bitcoin/approvals/{rejected.id}/review",
            json={"action": "rejected", "review_notes": "Too expensive"},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "rejected"

    @pytest.mark.asyncio
    @patch("app.api.endpoints.bitcoin_budget.settings")
    @patch("app.api.endpoints.bitcoin_budget.BitcoinBudgetService")
    async def test_review_expired_returns_409(self, MockService, mock_settings, async_client, test_admin_user):
        """Reviewing an expired/already-resolved approval returns 409 (admin-only per SA-01)."""
        mock_settings.use_lnd = True

        # review_approval returns approval with PENDING status (couldn't transition)
        expired = _mock_approval(status=SpendApprovalStatus.PENDING)
        instance = MockService.return_value
        instance.review_approval = AsyncMock(return_value=expired)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            f"/api/v1/bitcoin/approvals/{expired.id}/review",
            json={"action": "approved"},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 409

    @pytest.mark.asyncio
    @patch("app.api.endpoints.bitcoin_budget.settings")
    @patch("app.api.endpoints.bitcoin_budget.BitcoinBudgetService")
    async def test_review_invalid_action(self, MockService, mock_settings, async_client, test_admin_user):
        """Invalid action value returns 422 validation error (admin-only per SA-01)."""
        mock_settings.use_lnd = True

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            f"/api/v1/bitcoin/approvals/{uuid4()}/review",
            json={"action": "maybe"},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 422


# ============================================================================
# Approval cancel
# ============================================================================

class TestApprovalCancel:
    """Tests for approval cancellation."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.bitcoin_budget.settings")
    @patch("app.api.endpoints.bitcoin_budget.BitcoinBudgetService")
    async def test_cancel_own_approval(self, MockService, mock_settings, async_client, test_user):
        """Requestor can cancel their own pending approval."""
        mock_settings.use_lnd = True

        approval = _mock_approval(
            requested_by_id=test_user.id,
            status=SpendApprovalStatus.PENDING,
        )
        instance = MockService.return_value
        instance.get_approval = AsyncMock(return_value=approval)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.post(
            f"/api/v1/bitcoin/approvals/{approval.id}/cancel",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    @patch("app.api.endpoints.bitcoin_budget.settings")
    @patch("app.api.endpoints.bitcoin_budget.BitcoinBudgetService")
    async def test_cancel_others_approval_forbidden(self, MockService, mock_settings, async_client, test_user):
        """Cannot cancel another user's approval."""
        mock_settings.use_lnd = True

        other_user_id = uuid4()
        approval = _mock_approval(
            requested_by_id=other_user_id,
            status=SpendApprovalStatus.PENDING,
        )
        instance = MockService.return_value
        instance.get_approval = AsyncMock(return_value=approval)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.post(
            f"/api/v1/bitcoin/approvals/{approval.id}/cancel",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 403


# ============================================================================
# Budget summaries
# ============================================================================

class TestBudgetSummaries:
    """Tests for budget summary endpoints."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.bitcoin_budget.settings")
    @patch("app.api.endpoints.bitcoin_budget.BitcoinBudgetService")
    async def test_global_budget(self, MockService, mock_settings, async_client, test_user):
        """Global budget returns aggregated data."""
        mock_settings.use_lnd = True

        instance = MockService.return_value
        instance.get_global_budget = AsyncMock(return_value={
            "total_budget_sats": 1000000,
            "total_spent_sats": 500000,
            "total_received_sats": 100000,
            "total_remaining_sats": 500000,
            "total_pending_sats": 25000,
            "global_limit_sats": 500000,
            "campaigns_with_budget": 3,
            "campaigns_over_budget": 0,
            "campaigns_near_budget": 1,
            "pending_approvals": 2,
        })

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        # lnd_service is imported lazily inside the endpoint function
        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            mock_lnd.get_wallet_balance = AsyncMock(return_value={"confirmed_balance": 200000})
            mock_lnd.get_channel_balance = AsyncMock(return_value={"local_balance_sat": 300000})

            response = await async_client.get(
                "/api/v1/bitcoin/budget/global",
                headers={"Authorization": f"Bearer {token}"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["total_budget_sats"] == 1000000
        assert data["total_spent_sats"] == 500000

    @pytest.mark.asyncio
    @patch("app.api.endpoints.bitcoin_budget.settings")
    @patch("app.api.endpoints.bitcoin_budget.BitcoinBudgetService")
    async def test_campaign_budget(self, MockService, mock_settings, async_client, test_user):
        """Campaign budget returns per-campaign data."""
        mock_settings.use_lnd = True

        campaign_id = uuid4()
        instance = MockService.return_value
        instance.get_campaign_budget = AsyncMock(return_value={
            "campaign_id": str(campaign_id),
            "bitcoin_budget_sats": 200000,
            "bitcoin_spent_sats": 50000,
            "bitcoin_received_sats": 10000,
            "bitcoin_remaining_sats": 150000,
            "pending_approvals": 1,
            "recent_transactions": [],
        })

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            f"/api/v1/bitcoin/budget/campaign/{campaign_id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["bitcoin_budget_sats"] == 200000
        assert data["bitcoin_remaining_sats"] == 150000


# ============================================================================
# Transaction listing
# ============================================================================

class TestTransactionEndpoints:
    """Tests for transaction listing endpoints."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.bitcoin_budget.settings")
    @patch("app.api.endpoints.bitcoin_budget.BitcoinBudgetService")
    async def test_list_all_transactions(self, MockService, mock_settings, async_client, test_user):
        """Lists all transactions across campaigns."""
        mock_settings.use_lnd = True

        txs = [_mock_transaction(), _mock_transaction()]
        instance = MockService.return_value
        instance.get_all_transactions = AsyncMock(return_value=txs)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/bitcoin/transactions",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2

    @pytest.mark.asyncio
    @patch("app.api.endpoints.bitcoin_budget.settings")
    @patch("app.api.endpoints.bitcoin_budget.BitcoinBudgetService")
    async def test_list_campaign_transactions(self, MockService, mock_settings, async_client, test_user):
        """Lists transactions filtered by campaign."""
        mock_settings.use_lnd = True

        campaign_id = uuid4()
        txs = [_mock_transaction(campaign_id=campaign_id)]
        instance = MockService.return_value
        instance.get_campaign_transactions = AsyncMock(return_value=txs)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            f"/api/v1/bitcoin/transactions?campaign_id={campaign_id}",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1


# ============================================================================
# LND disabled guard for budget endpoints
# ============================================================================

class TestBudgetLndDisabled:
    """Tests that budget endpoints return 404 when LND disabled."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.bitcoin_budget.settings")
    async def test_approvals_lnd_disabled(self, mock_settings, async_client, test_user):
        """Approvals endpoint returns 404 when LND disabled."""
        mock_settings.use_lnd = False

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/bitcoin/approvals",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("app.api.endpoints.bitcoin_budget.settings")
    async def test_budget_global_lnd_disabled(self, mock_settings, async_client, test_user):
        """Global budget endpoint returns 404 when LND disabled."""
        mock_settings.use_lnd = False

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/bitcoin/budget/global",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 404
