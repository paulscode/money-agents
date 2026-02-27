"""
Tests for Boltz Cold Storage — BoltzSwapService and API endpoints.

Covers:
- Preimage/keypair generation
- Pair info fetching and caching
- Swap creation lifecycle
- Status monitoring and lifecycle advancement
- Claim transaction execution (subprocess paths)
- Failure modes (expired, refunded, Boltz errors, Tor failures)
- Cancel logic
- API endpoint validation (auth, input, responses)
- Fee estimation endpoint
- Recovery (multi-swap startup recovery)
- Celery task orchestration
- URL/proxy routing (Tor vs clearnet)
- List swaps endpoint
"""
import asyncio
import hashlib
import json
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4, UUID


# ---------------------------------------------------------------------------
# Auto-use fixture: make encrypt_field / decrypt_field no-ops for all Boltz
# tests so we test swap logic, not encryption.  Encryption is covered by
# TestEncryptionUtilities in test_security_auth_hardening.py.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _passthrough_encryption():
    """Patch boltz_service encrypt/decrypt to identity functions."""
    with patch("app.services.boltz_service.encrypt_field", side_effect=lambda x: x), \
         patch("app.services.boltz_service.decrypt_field", side_effect=lambda x: x):
        yield


@pytest.fixture(autouse=True)
def _disable_rate_limiter():
    """Disable slowapi rate limiter during tests to prevent 429 responses."""
    from app.core.rate_limit import limiter
    limiter.enabled = False
    yield
    limiter.enabled = True


# ============================================================================
# Unit Tests — Crypto Helpers
# ============================================================================

class TestPreimageGeneration:
    """Tests for preimage and hash generation."""

    def test_generate_preimage_format(self):
        from app.services.boltz_service import _generate_preimage
        preimage_hex, hash_hex = _generate_preimage()

        assert len(preimage_hex) == 64  # 32 bytes hex-encoded
        assert len(hash_hex) == 64

    def test_generate_preimage_hash_matches(self):
        from app.services.boltz_service import _generate_preimage
        preimage_hex, hash_hex = _generate_preimage()

        expected_hash = hashlib.sha256(bytes.fromhex(preimage_hex)).hexdigest()
        assert hash_hex == expected_hash

    def test_generate_preimage_unique(self):
        from app.services.boltz_service import _generate_preimage
        results = set()
        for _ in range(10):
            preimage_hex, _ = _generate_preimage()
            results.add(preimage_hex)
        assert len(results) == 10  # All unique


# ============================================================================
# Unit Tests — BoltzSwapService
# ============================================================================

class TestBoltzServicePairInfo:
    """Tests for fetching and parsing Boltz reverse pair info."""

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService._request")
    async def test_get_pair_info_success(self, mock_request):
        """Parses BTC/BTC pair info correctly."""
        from app.services.boltz_service import BoltzSwapService

        mock_request.return_value = (
            {
                "BTC": {
                    "BTC": {
                        "hash": "abc123",
                        "limits": {"minimal": 25000, "maximal": 25000000},
                        "fees": {
                            "percentage": 0.5,
                            "minerFees": {"lockup": 462, "claim": 333},
                        },
                    }
                }
            },
            None,
        )

        svc = BoltzSwapService()
        info, err = await svc.get_reverse_pair_info()

        assert err is None
        assert info["min"] == 25000
        assert info["max"] == 25000000
        assert info["fees_percentage"] == 0.5
        assert info["fees_miner_lockup"] == 462
        assert info["fees_miner_claim"] == 333
        assert info["hash"] == "abc123"

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService._request")
    async def test_get_pair_info_cached(self, mock_request):
        """Pair info is cached for 60 seconds."""
        from app.services.boltz_service import BoltzSwapService

        mock_request.return_value = (
            {
                "BTC": {
                    "BTC": {
                        "hash": "abc",
                        "limits": {"minimal": 25000, "maximal": 25000000},
                        "fees": {
                            "percentage": 0.5,
                            "minerFees": {"lockup": 462, "claim": 333},
                        },
                    }
                }
            },
            None,
        )

        svc = BoltzSwapService()
        await svc.get_reverse_pair_info()
        await svc.get_reverse_pair_info()

        # Should only call the API once (cached)
        assert mock_request.call_count == 1

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService._request")
    async def test_get_pair_info_error(self, mock_request):
        """Handles Boltz API errors gracefully."""
        from app.services.boltz_service import BoltzSwapService

        mock_request.return_value = (None, "Connection failed")

        svc = BoltzSwapService()
        info, err = await svc.get_reverse_pair_info()

        assert info is None
        assert "Connection failed" in err

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService._request")
    async def test_get_pair_info_missing_btc_pair(self, mock_request):
        """Handles missing BTC/BTC pair data."""
        from app.services.boltz_service import BoltzSwapService

        mock_request.return_value = ({"ETH": {}}, None)

        svc = BoltzSwapService()
        info, err = await svc.get_reverse_pair_info()

        assert info is None
        assert "not found" in err


class TestBoltzServiceSwapCreation:
    """Tests for creating reverse swaps."""

    @pytest.mark.asyncio
    @patch("app.services.boltz_service._generate_keypair")
    @patch("app.services.boltz_service.BoltzSwapService._request")
    async def test_create_swap_success(self, mock_request, mock_keypair, db_session, test_user):
        """Creates a swap and persists it to the database."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        mock_keypair.return_value = ("a" * 64, "02" + "b" * 64)

        # First call: get pair info; second call: create swap
        mock_request.side_effect = [
            (
                {
                    "BTC": {
                        "BTC": {
                            "hash": "abc",
                            "limits": {"minimal": 25000, "maximal": 25000000},
                            "fees": {
                                "percentage": 0.5,
                                "minerFees": {"lockup": 462, "claim": 333},
                            },
                        }
                    }
                },
                None,
            ),
            (
                {
                    "id": "boltz123",
                    "invoice": "lnbc250u1p...",
                    "lockupAddress": "bc1q...",
                    "refundPublicKey": "03" + "c" * 64,
                    "swapTree": {
                        "claimLeaf": {"version": 192, "output": "aa"},
                        "refundLeaf": {"version": 192, "output": "bb"},
                    },
                    "timeoutBlockHeight": 800000,
                    "onchainAmount": 24500,
                },
                None,
            ),
        ]

        svc = BoltzSwapService()
        swap, err = await svc.create_reverse_swap(
            db=db_session,
            user_id=test_user.id,
            invoice_amount_sats=25000,
            destination_address="bc1qtest123",
        )

        assert err is None
        assert swap is not None
        assert swap.boltz_swap_id == "boltz123"
        assert swap.invoice_amount_sats == 25000
        assert swap.onchain_amount_sats == 24500
        assert swap.status == SwapStatus.CREATED
        assert swap.boltz_invoice == "lnbc250u1p..."
        assert swap.destination_address == "bc1qtest123"
        assert swap.preimage_hex is not None
        assert len(swap.preimage_hex) == 64

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService._request")
    async def test_create_swap_amount_too_low(self, mock_request, db_session, test_user):
        """Rejects amounts below Boltz minimum."""
        from app.services.boltz_service import BoltzSwapService

        mock_request.return_value = (
            {
                "BTC": {
                    "BTC": {
                        "hash": "abc",
                        "limits": {"minimal": 25000, "maximal": 25000000},
                        "fees": {
                            "percentage": 0.5,
                            "minerFees": {"lockup": 462, "claim": 333},
                        },
                    }
                }
            },
            None,
        )

        svc = BoltzSwapService()
        swap, err = await svc.create_reverse_swap(
            db=db_session,
            user_id=test_user.id,
            invoice_amount_sats=1000,  # Below 25k minimum
            destination_address="bc1qtest",
        )

        assert swap is None
        assert "25,000" in err

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService._request")
    async def test_create_swap_amount_too_high(self, mock_request, db_session, test_user):
        """Rejects amounts above Boltz maximum."""
        from app.services.boltz_service import BoltzSwapService

        mock_request.return_value = (
            {
                "BTC": {
                    "BTC": {
                        "hash": "abc",
                        "limits": {"minimal": 25000, "maximal": 25000000},
                        "fees": {
                            "percentage": 0.5,
                            "minerFees": {"lockup": 462, "claim": 333},
                        },
                    }
                }
            },
            None,
        )

        svc = BoltzSwapService()
        swap, err = await svc.create_reverse_swap(
            db=db_session,
            user_id=test_user.id,
            invoice_amount_sats=30_000_000,  # Above 25M max
            destination_address="bc1qtest",
        )

        assert swap is None
        assert "25,000,000" in err

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService._request")
    async def test_create_swap_boltz_api_error(self, mock_request, db_session, test_user):
        """Handles Boltz API error on swap creation."""
        from app.services.boltz_service import BoltzSwapService

        mock_request.side_effect = [
            (
                {
                    "BTC": {
                        "BTC": {
                            "hash": "abc",
                            "limits": {"minimal": 25000, "maximal": 25000000},
                            "fees": {
                                "percentage": 0.5,
                                "minerFees": {"lockup": 462, "claim": 333},
                            },
                        }
                    }
                },
                None,
            ),
            (None, "Boltz API error 400: invalid address"),
        ]

        svc = BoltzSwapService()
        # Need to mock keypair gen to avoid Node.js dependency in tests
        with patch("app.services.boltz_service._generate_keypair", return_value=("a" * 64, "02" + "b" * 64)):
            swap, err = await svc.create_reverse_swap(
                db=db_session,
                user_id=test_user.id,
                invoice_amount_sats=25000,
                destination_address="bc1qtest",
            )

        assert swap is None
        assert "invalid address" in err


class TestBoltzServiceStatusAdvancement:
    """Tests for swap lifecycle state transitions."""

    def _make_swap(self, user_id, status="created", boltz_status="swap.created", **kwargs):
        """Helper to create a BoltzSwap instance."""
        from app.models.boltz_swap import BoltzSwap, SwapStatus
        return BoltzSwap(
            boltz_swap_id=f"test_{uuid4().hex[:8]}",
            user_id=user_id,
            invoice_amount_sats=25000,
            destination_address="bc1qtest",
            preimage_hex="a" * 64,
            preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64,
            claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus(status),
            boltz_status=boltz_status,
            boltz_invoice="lnbc...",
            boltz_swap_tree_json={"claimLeaf": {"version": 192, "output": "aa"}, "refundLeaf": {"version": 192, "output": "bb"}},
            status_history=[],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            **kwargs,
        )

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService.get_swap_status_from_boltz")
    async def test_advance_swap_expired(self, mock_status, db_session, test_user):
        """Swap moves to FAILED when Boltz reports invoice.expired."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import SwapStatus

        swap = self._make_swap(test_user.id)
        db_session.add(swap)
        await db_session.commit()

        mock_status.return_value = ("invoice.expired", {}, None)

        svc = BoltzSwapService()
        updated, err = await svc.advance_swap(db_session, swap)

        assert updated.status == SwapStatus.FAILED
        assert "invoice.expired" in updated.error_message
        assert updated.completed_at is not None

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService.get_swap_status_from_boltz")
    async def test_advance_swap_refunded(self, mock_status, db_session, test_user):
        """Swap moves to REFUNDED when Boltz reclaims after timeout."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import SwapStatus

        swap = self._make_swap(test_user.id, status="invoice_paid")
        db_session.add(swap)
        await db_session.commit()

        mock_status.return_value = ("transaction.refunded", {}, None)

        svc = BoltzSwapService()
        updated, err = await svc.advance_swap(db_session, swap)

        assert updated.status == SwapStatus.REFUNDED
        assert "refund" in updated.error_message.lower()

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService.get_swap_status_from_boltz")
    async def test_advance_swap_completed(self, mock_status, db_session, test_user):
        """Swap moves to COMPLETED when Boltz reports invoice.settled."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import SwapStatus

        swap = self._make_swap(test_user.id, status="claimed")
        db_session.add(swap)
        await db_session.commit()

        mock_status.return_value = ("invoice.settled", {}, None)

        svc = BoltzSwapService()
        updated, err = await svc.advance_swap(db_session, swap)

        assert updated.status == SwapStatus.COMPLETED
        assert updated.completed_at is not None

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService.cooperative_claim")
    @patch("app.services.boltz_service.BoltzSwapService.get_lockup_transaction")
    @patch("app.services.boltz_service.BoltzSwapService.get_swap_status_from_boltz")
    async def test_advance_swap_lockup_triggers_claim(
        self, mock_status, mock_lockup, mock_claim, db_session, test_user,
    ):
        """When Boltz lockup tx appears, claim is attempted."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import SwapStatus

        swap = self._make_swap(test_user.id, status="invoice_paid")
        db_session.add(swap)
        await db_session.commit()

        mock_status.return_value = ("transaction.mempool", {}, None)
        mock_lockup.return_value = ("0200000001...", None)
        mock_claim.return_value = ("abc123txid", None)

        svc = BoltzSwapService()
        updated, err = await svc.advance_swap(db_session, swap)

        assert updated.status == SwapStatus.CLAIMED
        assert updated.claim_txid == "abc123txid"
        mock_claim.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService.cooperative_claim")
    @patch("app.services.boltz_service.BoltzSwapService.get_lockup_transaction")
    @patch("app.services.boltz_service.BoltzSwapService.get_swap_status_from_boltz")
    async def test_advance_swap_claim_failure_retries(
        self, mock_status, mock_lockup, mock_claim, db_session, test_user,
    ):
        """Claim failure increments recovery count but doesn't mark as failed."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import SwapStatus

        swap = self._make_swap(test_user.id, status="invoice_paid")
        db_session.add(swap)
        await db_session.commit()

        mock_status.return_value = ("transaction.mempool", {}, None)
        mock_lockup.return_value = ("0200000001...", None)
        mock_claim.return_value = (None, "Musig2 signing failed")

        svc = BoltzSwapService()
        updated, err = await svc.advance_swap(db_session, swap)

        assert updated.status == SwapStatus.CLAIMING  # Still claiming, not failed
        assert updated.recovery_count == 1
        assert err == "Musig2 signing failed"

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService.get_swap_status_from_boltz")
    async def test_advance_swap_boltz_unreachable(self, mock_status, db_session, test_user):
        """Handles Boltz API being unreachable."""
        from app.services.boltz_service import BoltzSwapService

        swap = self._make_swap(test_user.id, status="invoice_paid")
        db_session.add(swap)
        await db_session.commit()

        mock_status.return_value = (None, None, "Connection failed (Tor)")

        svc = BoltzSwapService()
        updated, err = await svc.advance_swap(db_session, swap)

        assert err == "Connection failed (Tor)"
        assert updated.status.value == "invoice_paid"  # No state change on transient error

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService.get_swap_status_from_boltz")
    async def test_advance_swap_status_history_appended(self, mock_status, db_session, test_user):
        """Status changes are tracked in status_history."""
        from app.services.boltz_service import BoltzSwapService

        swap = self._make_swap(test_user.id, status="created", boltz_status="swap.created")
        db_session.add(swap)
        await db_session.commit()

        mock_status.return_value = ("invoice.expired", {}, None)

        svc = BoltzSwapService()
        updated, _ = await svc.advance_swap(db_session, swap)

        assert len(updated.status_history) == 1
        assert updated.status_history[0]["boltz_status"] == "invoice.expired"


class TestBoltzServiceCancel:
    """Tests for swap cancellation."""

    @pytest.mark.asyncio
    async def test_cancel_created_swap(self, db_session, test_user):
        """Can cancel a swap in 'created' status."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        swap = BoltzSwap(
            boltz_swap_id="cancel_test",
            user_id=test_user.id,
            invoice_amount_sats=25000,
            destination_address="bc1qtest",
            preimage_hex="a" * 64,
            preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64,
            claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.CREATED,
            status_history=[],
        )
        db_session.add(swap)
        await db_session.commit()

        svc = BoltzSwapService()
        success, err = await svc.cancel_swap(db_session, swap)

        assert success is True
        assert err is None
        assert swap.status == SwapStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_paid_swap_rejected(self, db_session, test_user):
        """Cannot cancel after invoice has been paid."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        swap = BoltzSwap(
            boltz_swap_id="cancel_test2",
            user_id=test_user.id,
            invoice_amount_sats=25000,
            destination_address="bc1qtest",
            preimage_hex="a" * 64,
            preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64,
            claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.INVOICE_PAID,
            status_history=[],
        )
        db_session.add(swap)
        await db_session.commit()

        svc = BoltzSwapService()
        success, err = await svc.cancel_swap(db_session, swap)

        assert success is False
        assert "Cannot cancel" in err


class TestBoltzServiceFeeCalculation:
    """Tests for fee estimation logic."""

    def test_fee_calculation(self):
        """Verify fee math: amount * percentage + miner fees."""
        import math
        amount = 100_000
        percentage = 0.5
        miner_lockup = 462
        miner_claim = 333

        boltz_fee = math.ceil(amount * (percentage / 100))  # 500
        total_fee = boltz_fee + miner_lockup + miner_claim  # 500 + 462 + 333 = 1295
        receive = amount - total_fee  # 98,705

        assert boltz_fee == 500
        assert total_fee == 1295
        assert receive == 98_705

    def test_fee_minimum_amount(self):
        """Fee calculation at minimum amount (25k sats)."""
        import math
        amount = 25_000
        boltz_fee = math.ceil(amount * 0.005)  # 125
        miner = 795  # 462 + 333
        receive = amount - boltz_fee - miner  # 24,080

        assert boltz_fee == 125
        assert receive == 24_080

    def test_fee_maximum_amount(self):
        """Fee calculation at maximum amount (25M sats)."""
        import math
        amount = 25_000_000
        boltz_fee = math.ceil(amount * 0.005)  # 125,000
        miner = 795
        receive = amount - boltz_fee - miner  # 24,874,205

        assert boltz_fee == 125_000
        assert receive == 24_874_205


# ============================================================================
# Unit Tests — Swap State Machine
# ============================================================================

class TestSwapStateMachine:
    """Tests for valid/invalid state transitions."""

    def test_valid_states(self):
        from app.models.boltz_swap import SwapStatus
        assert SwapStatus.CREATED.value == "created"
        assert SwapStatus.PAYING_INVOICE.value == "paying_invoice"
        assert SwapStatus.INVOICE_PAID.value == "invoice_paid"
        assert SwapStatus.CLAIMING.value == "claiming"
        assert SwapStatus.CLAIMED.value == "claimed"
        assert SwapStatus.COMPLETED.value == "completed"
        assert SwapStatus.FAILED.value == "failed"
        assert SwapStatus.CANCELLED.value == "cancelled"
        assert SwapStatus.REFUNDED.value == "refunded"

    def test_terminal_states(self):
        """Terminal states: completed, failed, cancelled, refunded."""
        from app.models.boltz_swap import SwapStatus
        terminal = {SwapStatus.COMPLETED, SwapStatus.FAILED, SwapStatus.CANCELLED, SwapStatus.REFUNDED}
        assert len(terminal) == 4


class TestRetryDelay:
    """Tests for the tiered retry delay calculation."""

    def test_retry_delay_first_10_min(self):
        from app.tasks.boltz_tasks import _get_retry_delay
        from app.models.boltz_swap import BoltzSwap

        swap = MagicMock()
        swap.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        assert _get_retry_delay(swap) == 15

    def test_retry_delay_10_min_to_2_hours(self):
        from app.tasks.boltz_tasks import _get_retry_delay

        swap = MagicMock()
        swap.created_at = datetime.now(timezone.utc) - timedelta(minutes=30)
        assert _get_retry_delay(swap) == 60

    def test_retry_delay_after_2_hours(self):
        from app.tasks.boltz_tasks import _get_retry_delay

        swap = MagicMock()
        swap.created_at = datetime.now(timezone.utc) - timedelta(hours=3)
        assert _get_retry_delay(swap) == 300

    def test_retry_delay_no_created_at(self):
        from app.tasks.boltz_tasks import _get_retry_delay

        swap = MagicMock()
        swap.created_at = None
        assert _get_retry_delay(swap) == 15


# ============================================================================
# API Endpoint Tests
# ============================================================================

class TestColdStorageFeeEndpoint:
    """Tests for GET /wallet/cold-storage/lightning/fees."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    @patch("app.api.endpoints.cold_storage.boltz_service")
    async def test_fees_success(self, mock_boltz, mock_settings, async_client, test_user):
        """Returns fee info when Boltz is reachable."""
        mock_settings.use_lnd = True
        mock_settings.boltz_use_tor = True
        mock_settings.lnd_tor_proxy = "socks5://tor:9050"

        mock_boltz.get_reverse_pair_info = AsyncMock(return_value=(
            {
                "min": 25000,
                "max": 25000000,
                "fees_percentage": 0.5,
                "fees_miner_lockup": 462,
                "fees_miner_claim": 333,
            },
            None,
        ))

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/cold-storage/lightning/fees",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["min_amount_sats"] == 25000
        assert data["max_amount_sats"] == 25000000
        assert data["fee_percentage"] == 0.5
        assert data["total_miner_fee_sats"] == 795

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    @patch("app.api.endpoints.cold_storage.boltz_service")
    async def test_fees_boltz_unavailable(self, mock_boltz, mock_settings, async_client, test_user):
        """Returns 503 when Boltz is unreachable."""
        mock_settings.use_lnd = True
        mock_boltz.get_reverse_pair_info = AsyncMock(return_value=(None, "Tor connection failed"))

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/cold-storage/lightning/fees",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 503

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_fees_lnd_disabled(self, mock_settings, async_client, test_user):
        """Returns 404 when LND is disabled."""
        mock_settings.use_lnd = False

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/cold-storage/lightning/fees",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404


class TestColdStorageInitiateEndpoint:
    """Tests for POST /wallet/cold-storage/lightning."""

    @pytest.mark.asyncio
    @patch("app.tasks.boltz_tasks.process_boltz_swap")
    @patch("app.services.lnd_service.lnd_service")
    @patch("app.api.endpoints.cold_storage.boltz_service")
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_initiate_swap_success(
        self, mock_settings, mock_boltz, mock_lnd, mock_task, async_client, test_admin_user,
    ):
        """Initiates swap and returns swap status."""
        mock_settings.use_lnd = True

        from app.models.boltz_swap import BoltzSwap, SwapStatus

        mock_swap = MagicMock(spec=BoltzSwap)
        mock_swap.id = uuid4()
        mock_swap.boltz_swap_id = "boltz_test_123"
        mock_swap.status = SwapStatus.CREATED
        mock_swap.boltz_status = "swap.created"
        mock_swap.invoice_amount_sats = 50000
        mock_swap.onchain_amount_sats = 49500
        mock_swap.destination_address = "bc1qtestaddr"
        mock_swap.fee_percentage = "0.5"
        mock_swap.miner_fee_sats = 795
        mock_swap.boltz_invoice = "lnbc500u1p..."
        mock_swap.claim_txid = None
        mock_swap.error_message = None
        mock_swap.created_at = datetime.now(timezone.utc)
        mock_swap.updated_at = datetime.now(timezone.utc)
        mock_swap.completed_at = None

        mock_boltz.create_reverse_swap = AsyncMock(return_value=(mock_swap, None))
        mock_lnd.get_channel_balance = AsyncMock(return_value={"local_balance_sat": "100000"})
        mock_task.delay = MagicMock()

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/cold-storage/lightning",
            json={
                "amount_sats": 50000,
                "destination_address": "bc1qtestaddress1234567890123",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["boltz_swap_id"] == "boltz_test_123"
        assert data["status"] == "created"

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_initiate_swap_amount_too_low(self, mock_settings, async_client, test_admin_user):
        """Rejects amount below Boltz minimum (422 validation)."""
        mock_settings.use_lnd = True

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/cold-storage/lightning",
            json={
                "amount_sats": 1000,
                "destination_address": "bc1qtestaddress1234567890123",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_initiate_swap_requires_admin(self, mock_settings, async_client, test_user):
        """Non-admin users cannot initiate swaps."""
        mock_settings.use_lnd = True

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/cold-storage/lightning",
            json={
                "amount_sats": 50000,
                "destination_address": "bc1qtestaddress1234567890123",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403


class TestColdStorageStatusEndpoint:
    """Tests for GET /wallet/cold-storage/lightning/{swap_id}."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    @patch("app.api.endpoints.cold_storage.boltz_service")
    async def test_status_not_found(self, mock_boltz, mock_settings, async_client, test_user):
        """Returns 404 for non-existent swap."""
        mock_settings.use_lnd = True
        mock_boltz.get_swap_by_id = AsyncMock(return_value=None)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        swap_id = str(uuid4())
        response = await async_client.get(
            f"/api/v1/wallet/cold-storage/lightning/{swap_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_status_invalid_uuid(self, mock_settings, async_client, test_user):
        """Returns 400 for invalid UUID format."""
        mock_settings.use_lnd = True

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/cold-storage/lightning/not-a-uuid",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400


class TestColdStorageCancelEndpoint:
    """Tests for POST /wallet/cold-storage/lightning/{swap_id}/cancel."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    @patch("app.api.endpoints.cold_storage.boltz_service")
    async def test_cancel_success(self, mock_boltz, mock_settings, async_client, test_admin_user):
        """Successfully cancels a swap in 'created' state."""
        mock_settings.use_lnd = True

        from app.models.boltz_swap import SwapStatus
        mock_swap = MagicMock()
        mock_swap.id = uuid4()
        mock_swap.user_id = test_admin_user.id  # Must match requesting user
        mock_swap.boltz_swap_id = "cancel_test"
        mock_swap.status = SwapStatus.CANCELLED
        mock_swap.boltz_status = None
        mock_swap.invoice_amount_sats = 25000
        mock_swap.onchain_amount_sats = None
        mock_swap.destination_address = "bc1q..."
        mock_swap.fee_percentage = None
        mock_swap.miner_fee_sats = None
        mock_swap.boltz_invoice = None
        mock_swap.claim_txid = None
        mock_swap.error_message = "Cancelled by user"
        mock_swap.created_at = datetime.now(timezone.utc)
        mock_swap.updated_at = datetime.now(timezone.utc)
        mock_swap.completed_at = datetime.now(timezone.utc)

        mock_boltz.get_swap_by_id = AsyncMock(return_value=mock_swap)
        mock_boltz.cancel_swap = AsyncMock(return_value=(True, None))

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        swap_id = str(uuid4())
        response = await async_client.post(
            f"/api/v1/wallet/cold-storage/lightning/{swap_id}/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    @patch("app.api.endpoints.cold_storage.boltz_service")
    async def test_cancel_after_payment_rejected(self, mock_boltz, mock_settings, async_client, test_admin_user):
        """Cannot cancel after invoice payment."""
        mock_settings.use_lnd = True

        mock_swap = MagicMock()
        mock_swap.user_id = test_admin_user.id  # Must match requesting user
        mock_boltz.get_swap_by_id = AsyncMock(return_value=mock_swap)
        mock_boltz.cancel_swap = AsyncMock(return_value=(False, "Cannot cancel swap in status 'invoice_paid'"))

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        swap_id = str(uuid4())
        response = await async_client.post(
            f"/api/v1/wallet/cold-storage/lightning/{swap_id}/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
        assert "Cannot cancel" in response.json()["detail"]


# ============================================================================
# Model Tests
# ============================================================================

class TestBoltzSwapModel:
    """Tests for the BoltzSwap SQLAlchemy model."""

    @pytest.mark.asyncio
    async def test_create_swap_record(self, db_session, test_user):
        """Can create and read a swap record."""
        from app.models.boltz_swap import BoltzSwap, SwapStatus
        from sqlalchemy import select

        swap = BoltzSwap(
            boltz_swap_id="model_test_1",
            user_id=test_user.id,
            invoice_amount_sats=100000,
            destination_address="bc1qmodeltest",
            preimage_hex="a" * 64,
            preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64,
            claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.CREATED,
            status_history=[],
        )
        db_session.add(swap)
        await db_session.commit()

        result = await db_session.execute(
            select(BoltzSwap).where(BoltzSwap.boltz_swap_id == "model_test_1")
        )
        loaded = result.scalar_one()

        assert loaded.invoice_amount_sats == 100000
        assert loaded.status == SwapStatus.CREATED
        assert loaded.destination_address == "bc1qmodeltest"

    @pytest.mark.asyncio
    async def test_update_swap_status(self, db_session, test_user):
        """Can update swap status and persist."""
        from app.models.boltz_swap import BoltzSwap, SwapStatus
        from sqlalchemy import select

        swap = BoltzSwap(
            boltz_swap_id="model_test_2",
            user_id=test_user.id,
            invoice_amount_sats=50000,
            destination_address="bc1qtest2",
            preimage_hex="a" * 64,
            preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64,
            claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.CREATED,
            status_history=[],
        )
        db_session.add(swap)
        await db_session.commit()

        swap.status = SwapStatus.PAYING_INVOICE
        swap.boltz_status = "swap.created"
        await db_session.commit()

        result = await db_session.execute(
            select(BoltzSwap).where(BoltzSwap.boltz_swap_id == "model_test_2")
        )
        loaded = result.scalar_one()
        assert loaded.status == SwapStatus.PAYING_INVOICE

    @pytest.mark.asyncio
    async def test_swap_repr(self, db_session, test_user):
        """Model __repr__ includes swap ID and status."""
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        swap = BoltzSwap(
            boltz_swap_id="repr_test",
            user_id=test_user.id,
            invoice_amount_sats=25000,
            destination_address="bc1qrepr",
            preimage_hex="a" * 64,
            preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64,
            claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.CREATED,
            status_history=[],
        )
        assert "repr_test" in repr(swap)
        assert "created" in repr(swap)


# ============================================================================
# Config Tests
# ============================================================================

class TestBoltzConfig:
    """Tests for Boltz configuration settings."""

    def test_default_boltz_settings(self):
        """Default Boltz settings are sensible."""
        from app.core.config import Settings

        # Access class-level defaults
        assert Settings.model_fields["boltz_api_url"].default == "https://api.boltz.exchange/v2"
        assert Settings.model_fields["boltz_use_tor"].default is True
        assert Settings.model_fields["boltz_fallback_clearnet"].default is False


# ============================================================================
# Service — cooperative_claim subprocess tests
# ============================================================================

class TestCooperativeClaim:
    """Tests for claim transaction construction via Node.js subprocess."""

    def _make_swap(self, user_id):
        from app.models.boltz_swap import BoltzSwap, SwapStatus
        return BoltzSwap(
            boltz_swap_id="claim_test_1",
            user_id=user_id,
            invoice_amount_sats=50000,
            destination_address="bc1qclaim",
            preimage_hex="a" * 64,
            preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64,
            claim_public_key_hex="02" + "d" * 64,
            boltz_refund_public_key_hex="03" + "e" * 64,
            boltz_swap_tree_json={"claimLeaf": {"version": 192, "output": "aa"}},
            timeout_block_height=800000,
            status=SwapStatus.CLAIMING,
            status_history=[],
        )

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.asyncio.create_subprocess_exec")
    async def test_claim_success(self, mock_exec, db_session, test_user):
        """Successful cooperative claim returns txid."""
        from app.services.boltz_service import BoltzSwapService

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(
            json.dumps({"txid": "abc123def456", "txHex": "0200..."}).encode(),
            b"",
        ))
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        svc = BoltzSwapService()
        swap = self._make_swap(test_user.id)
        txid, err = await svc.cooperative_claim(swap, "0200000001...")

        assert txid == "abc123def456"
        assert err is None
        mock_exec.assert_called_once()
        # Verify the claim input was passed via stdin (communicate input=)
        comm_call = mock_proc.communicate.call_args
        assert comm_call.kwargs.get("input") is not None or (
            comm_call.args and comm_call.args[0] is not None
        )

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.asyncio.create_subprocess_exec")
    async def test_claim_script_nonzero_exit(self, mock_exec, db_session, test_user):
        """Non-zero exit from claim script returns error."""
        from app.services.boltz_service import BoltzSwapService

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(
            b"", b"Error: Musig2 nonce exchange failed",
        ))
        mock_proc.returncode = 1
        mock_exec.return_value = mock_proc

        svc = BoltzSwapService()
        swap = self._make_swap(test_user.id)
        txid, err = await svc.cooperative_claim(swap, "0200000001...")

        assert txid is None
        assert "Claim script failed (non-zero exit code)" in err

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.asyncio.create_subprocess_exec")
    async def test_claim_script_timeout(self, mock_exec, db_session, test_user):
        """Claim script timeout returns appropriate error."""
        from app.services.boltz_service import BoltzSwapService

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()
        mock_exec.return_value = mock_proc

        svc = BoltzSwapService()
        swap = self._make_swap(test_user.id)
        txid, err = await svc.cooperative_claim(swap, "0200000001...")

        assert txid is None
        assert "timed out" in err

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.asyncio.create_subprocess_exec")
    async def test_claim_script_no_txid_in_output(self, mock_exec, db_session, test_user):
        """Claim script returns JSON but without txid."""
        from app.services.boltz_service import BoltzSwapService

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(
            json.dumps({"error": "something went wrong"}).encode(),
            b"",
        ))
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        svc = BoltzSwapService()
        swap = self._make_swap(test_user.id)
        txid, err = await svc.cooperative_claim(swap, "0200000001...")

        assert txid is None
        assert "no txid" in err.lower()

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.asyncio.create_subprocess_exec")
    async def test_claim_script_invalid_json(self, mock_exec, db_session, test_user):
        """Claim script returns non-JSON output."""
        from app.services.boltz_service import BoltzSwapService

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(
            b"not valid json at all",
            b"",
        ))
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        svc = BoltzSwapService()
        swap = self._make_swap(test_user.id)
        txid, err = await svc.cooperative_claim(swap, "0200000001...")

        assert txid is None
        assert "Claim script returned invalid output" in err

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.asyncio.create_subprocess_exec")
    async def test_claim_node_not_found(self, mock_exec, db_session, test_user):
        """Node.js not installed returns appropriate error."""
        from app.services.boltz_service import BoltzSwapService

        mock_exec.side_effect = FileNotFoundError("node")

        svc = BoltzSwapService()
        swap = self._make_swap(test_user.id)
        txid, err = await svc.cooperative_claim(swap, "0200000001...")

        assert txid is None
        assert "Node.js not found" in err

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.CLAIM_SCRIPT_PATH")
    async def test_claim_script_missing(self, mock_path, db_session, test_user):
        """Missing claim script file returns error."""
        from app.services.boltz_service import BoltzSwapService

        mock_path.exists.return_value = False

        svc = BoltzSwapService()
        swap = self._make_swap(test_user.id)
        txid, err = await svc.cooperative_claim(swap, "0200000001...")

        assert txid is None
        assert "not found" in err


# ============================================================================
# Service — URL routing and proxy selection
# ============================================================================

class TestBoltzUrlRouting:
    """Tests for Tor/clearnet URL and proxy selection."""

    @patch("app.services.boltz_service.settings")
    def test_tor_url_when_enabled(self, mock_settings):
        """Uses onion URL when Tor is enabled and proxy is set."""
        from app.services.boltz_service import BoltzSwapService
        mock_settings.boltz_use_tor = True
        mock_settings.lnd_tor_proxy = "socks5://tor-proxy:9050"
        mock_settings.boltz_onion_url = "http://boltz.onion/api/v2"
        mock_settings.boltz_api_url = "https://api.boltz.exchange/v2"

        svc = BoltzSwapService()
        assert svc._boltz_url == "http://boltz.onion/api/v2"
        assert svc._proxy == "socks5://tor-proxy:9050"

    @patch("app.services.boltz_service.settings")
    def test_clearnet_url_when_tor_disabled(self, mock_settings):
        """Uses clearnet URL when Tor is disabled."""
        from app.services.boltz_service import BoltzSwapService
        mock_settings.boltz_use_tor = False
        mock_settings.lnd_tor_proxy = "socks5://tor-proxy:9050"
        mock_settings.boltz_api_url = "https://api.boltz.exchange/v2"

        svc = BoltzSwapService()
        assert svc._boltz_url == "https://api.boltz.exchange/v2"
        assert svc._proxy is None

    @patch("app.services.boltz_service.settings")
    def test_clearnet_url_when_no_proxy(self, mock_settings):
        """Uses clearnet URL when Tor enabled but no proxy configured."""
        from app.services.boltz_service import BoltzSwapService
        mock_settings.boltz_use_tor = True
        mock_settings.lnd_tor_proxy = ""
        mock_settings.boltz_api_url = "https://api.boltz.exchange/v2"

        svc = BoltzSwapService()
        assert svc._boltz_url == "https://api.boltz.exchange/v2"
        assert svc._proxy is None


# ============================================================================
# Service — get_swap_status_from_boltz / get_lockup_transaction
# ============================================================================

class TestBoltzStatusAndLockup:
    """Tests for querying Boltz swap status and lockup transaction."""

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService._request")
    async def test_get_swap_status_success(self, mock_request):
        """Parses status from Boltz response."""
        from app.services.boltz_service import BoltzSwapService

        mock_request.return_value = (
            {"status": "transaction.mempool", "transaction": {"hex": "0200..."}},
            None,
        )
        svc = BoltzSwapService()
        status, data, err = await svc.get_swap_status_from_boltz("test123")

        assert status == "transaction.mempool"
        assert data["transaction"]["hex"] == "0200..."
        assert err is None

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService._request")
    async def test_get_swap_status_error(self, mock_request):
        """Handles Boltz API error on status check."""
        from app.services.boltz_service import BoltzSwapService

        mock_request.return_value = (None, "404 Not Found")
        svc = BoltzSwapService()
        status, data, err = await svc.get_swap_status_from_boltz("badid123")

        assert status is None
        assert data is None
        assert err == "404 Not Found"

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService._request")
    async def test_get_lockup_tx_success(self, mock_request):
        """Fetches lockup transaction hex."""
        from app.services.boltz_service import BoltzSwapService

        mock_request.return_value = ({"hex": "02000000abcdef"}, None)
        svc = BoltzSwapService()
        tx_hex, err = await svc.get_lockup_transaction("test123")

        assert tx_hex == "02000000abcdef"
        assert err is None

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService._request")
    async def test_get_lockup_tx_uses_transactionHex_key(self, mock_request):
        """Falls back to 'transactionHex' key if 'hex' is missing."""
        from app.services.boltz_service import BoltzSwapService

        mock_request.return_value = ({"transactionHex": "020000001111"}, None)
        svc = BoltzSwapService()
        tx_hex, err = await svc.get_lockup_transaction("test123")

        assert tx_hex == "020000001111"
        assert err is None

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService._request")
    async def test_get_lockup_tx_error(self, mock_request):
        """Handles error fetching lockup transaction."""
        from app.services.boltz_service import BoltzSwapService

        mock_request.return_value = (None, "lockup not yet available")
        svc = BoltzSwapService()
        tx_hex, err = await svc.get_lockup_transaction("test123")

        assert tx_hex is None
        assert "not yet available" in err


# ============================================================================
# Service — Additional advance_swap states
# ============================================================================

class TestAdvanceSwapAdditionalStates:
    """Tests for advance_swap edge cases not covered in the main class."""

    def _make_swap(self, user_id, status="created", boltz_status="swap.created", **kwargs):
        from app.models.boltz_swap import BoltzSwap, SwapStatus
        return BoltzSwap(
            boltz_swap_id=f"test_{uuid4().hex[:8]}",
            user_id=user_id,
            invoice_amount_sats=25000,
            destination_address="bc1qtest",
            preimage_hex="a" * 64,
            preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64,
            claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus(status),
            boltz_status=boltz_status,
            boltz_invoice="lnbc...",
            boltz_swap_tree_json={"claimLeaf": {"version": 192, "output": "aa"}},
            status_history=[],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            **kwargs,
        )

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService.get_swap_status_from_boltz")
    async def test_advance_swap_swap_expired(self, mock_status, db_session, test_user):
        """swap.expired is a terminal failure state."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import SwapStatus

        swap = self._make_swap(test_user.id)
        db_session.add(swap)
        await db_session.commit()

        mock_status.return_value = ("swap.expired", {}, None)

        svc = BoltzSwapService()
        updated, err = await svc.advance_swap(db_session, swap)

        assert updated.status == SwapStatus.FAILED
        assert "swap.expired" in updated.error_message

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService.get_swap_status_from_boltz")
    async def test_advance_swap_transaction_failed(self, mock_status, db_session, test_user):
        """transaction.failed is a terminal failure state."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import SwapStatus

        swap = self._make_swap(test_user.id, status="invoice_paid")
        db_session.add(swap)
        await db_session.commit()

        mock_status.return_value = ("transaction.failed", {}, None)

        svc = BoltzSwapService()
        updated, err = await svc.advance_swap(db_session, swap)

        assert updated.status == SwapStatus.FAILED
        assert "transaction.failed" in updated.error_message

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService.cooperative_claim")
    @patch("app.services.boltz_service.BoltzSwapService.get_lockup_transaction")
    @patch("app.services.boltz_service.BoltzSwapService.get_swap_status_from_boltz")
    async def test_advance_swap_confirmed_lockup_triggers_claim(
        self, mock_status, mock_lockup, mock_claim, db_session, test_user,
    ):
        """transaction.confirmed also triggers claim (not just mempool)."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import SwapStatus

        swap = self._make_swap(test_user.id, status="invoice_paid")
        db_session.add(swap)
        await db_session.commit()

        mock_status.return_value = ("transaction.confirmed", {}, None)
        mock_lockup.return_value = ("0200000001...", None)
        mock_claim.return_value = ("confirmed_claim_txid", None)

        svc = BoltzSwapService()
        updated, err = await svc.advance_swap(db_session, swap)

        assert updated.status == SwapStatus.CLAIMED
        assert updated.claim_txid == "confirmed_claim_txid"

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService.get_swap_status_from_boltz")
    async def test_advance_swap_skips_claim_when_txid_set(self, mock_status, db_session, test_user):
        """If claim_txid already set, don't attempt claim again."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import SwapStatus

        swap = self._make_swap(test_user.id, status="claiming", claim_txid="already_claimed")
        db_session.add(swap)
        await db_session.commit()

        mock_status.return_value = ("transaction.mempool", {}, None)

        svc = BoltzSwapService()
        updated, err = await svc.advance_swap(db_session, swap)

        # Should not error or overwrite existing claim_txid
        assert updated.claim_txid == "already_claimed"
        assert err is None

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService.get_swap_status_from_boltz")
    async def test_advance_swap_no_change_on_same_status(self, mock_status, db_session, test_user):
        """Status history not appended when boltz_status hasn't changed."""
        from app.services.boltz_service import BoltzSwapService

        swap = self._make_swap(test_user.id, status="invoice_paid", boltz_status="invoice.set")
        db_session.add(swap)
        await db_session.commit()

        mock_status.return_value = ("invoice.set", {}, None)

        svc = BoltzSwapService()
        updated, err = await svc.advance_swap(db_session, swap)

        assert len(updated.status_history) == 0  # No history entry added

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService.get_lockup_transaction")
    @patch("app.services.boltz_service.BoltzSwapService.get_swap_status_from_boltz")
    async def test_advance_swap_lockup_fetch_error(
        self, mock_status, mock_lockup, db_session, test_user,
    ):
        """Lockup transaction fetch failure returns error but doesn't fail swap."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import SwapStatus

        swap = self._make_swap(test_user.id, status="invoice_paid")
        db_session.add(swap)
        await db_session.commit()

        mock_status.return_value = ("transaction.mempool", {}, None)
        mock_lockup.return_value = (None, "transaction not yet broadcast")

        svc = BoltzSwapService()
        updated, err = await svc.advance_swap(db_session, swap)

        assert updated.status == SwapStatus.CLAIMING
        assert err == "transaction not yet broadcast"


# ============================================================================
# Service — recover_pending_swaps
# ============================================================================

class TestRecoverPendingSwaps:
    """Tests for startup swap recovery."""

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService.get_swap_status_from_boltz")
    async def test_recover_no_pending(self, mock_status, db_session, test_user):
        """No pending swaps returns empty list."""
        from app.services.boltz_service import BoltzSwapService

        svc = BoltzSwapService()
        results = await svc.recover_pending_swaps(db_session)

        assert results == []

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService.get_swap_status_from_boltz")
    async def test_recover_multiple_pending(self, mock_status, db_session, test_user):
        """Recovers multiple pending swaps with mixed outcomes."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        # Swap 1: will expire
        swap1 = BoltzSwap(
            boltz_swap_id="recover_1", user_id=test_user.id,
            invoice_amount_sats=25000, destination_address="bc1qr1",
            preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.CREATED, status_history=[], boltz_status="swap.created",
        )
        # Swap 2: will complete
        swap2 = BoltzSwap(
            boltz_swap_id="recover_2", user_id=test_user.id,
            invoice_amount_sats=50000, destination_address="bc1qr2",
            preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.CLAIMED, status_history=[], boltz_status="transaction.mempool",
        )
        # Swap 3: already completed (should NOT be recovered)
        swap3 = BoltzSwap(
            boltz_swap_id="recover_3", user_id=test_user.id,
            invoice_amount_sats=30000, destination_address="bc1qr3",
            preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.COMPLETED, status_history=[],
        )
        db_session.add_all([swap1, swap2, swap3])
        await db_session.commit()

        async def _status_by_id(swap_id):
            if swap_id == "recover_1":
                return ("invoice.expired", {}, None)
            elif swap_id == "recover_2":
                return ("invoice.settled", {}, None)
            return ("unknown", {}, None)
        mock_status.side_effect = _status_by_id

        svc = BoltzSwapService()
        results = await svc.recover_pending_swaps(db_session)

        assert len(results) == 2
        by_id = {r["boltz_swap_id"]: r for r in results}
        assert by_id["recover_1"]["status"] == "failed"
        assert by_id["recover_2"]["status"] == "completed"

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService.get_swap_status_from_boltz")
    async def test_recover_exception_in_one_swap(self, mock_status, db_session, test_user):
        """Exception recovering one swap doesn't block others."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        swap1 = BoltzSwap(
            boltz_swap_id="recover_err_1", user_id=test_user.id,
            invoice_amount_sats=25000, destination_address="bc1qe1",
            preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.INVOICE_PAID, status_history=[], boltz_status="invoice.set",
        )
        swap2 = BoltzSwap(
            boltz_swap_id="recover_err_2", user_id=test_user.id,
            invoice_amount_sats=25000, destination_address="bc1qe2",
            preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.CLAIMING, status_history=[], boltz_status="transaction.mempool",
        )
        db_session.add_all([swap1, swap2])
        await db_session.commit()

        async def _status_by_id(swap_id):
            if swap_id == "recover_err_1":
                raise Exception("Unexpected error")
            elif swap_id == "recover_err_2":
                return ("invoice.settled", {}, None)
            return ("unknown", {}, None)
        mock_status.side_effect = _status_by_id

        svc = BoltzSwapService()
        results = await svc.recover_pending_swaps(db_session)

        assert len(results) == 2
        by_id = {r["boltz_swap_id"]: r for r in results}
        assert "Unexpected error" in by_id["recover_err_1"]["error"]
        assert by_id["recover_err_2"]["status"] == "completed"


# ============================================================================
# Service — get_swaps_for_user
# ============================================================================

class TestGetSwapsForUser:
    """Tests for listing user swaps."""

    @pytest.mark.asyncio
    async def test_returns_user_swaps(self, db_session, test_user):
        """Returns swaps for the given user."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        for i in range(3):
            db_session.add(BoltzSwap(
                boltz_swap_id=f"list_{i}", user_id=test_user.id,
                invoice_amount_sats=25000 + i, destination_address=f"bc1q{i}",
                preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
                claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
                status=SwapStatus.CREATED, status_history=[],
            ))
        await db_session.commit()

        svc = BoltzSwapService()
        swaps = await svc.get_swaps_for_user(db_session, test_user.id)

        assert len(swaps) == 3

    @pytest.mark.asyncio
    async def test_returns_empty_for_other_user(self, db_session, test_user, test_admin_user):
        """Doesn't return swaps belonging to other users."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        db_session.add(BoltzSwap(
            boltz_swap_id="other_user_swap", user_id=test_user.id,
            invoice_amount_sats=25000, destination_address="bc1qother",
            preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.CREATED, status_history=[],
        ))
        await db_session.commit()

        svc = BoltzSwapService()
        swaps = await svc.get_swaps_for_user(db_session, test_admin_user.id)

        assert len(swaps) == 0

    @pytest.mark.asyncio
    async def test_limit_parameter(self, db_session, test_user):
        """Respects the limit parameter."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        for i in range(5):
            db_session.add(BoltzSwap(
                boltz_swap_id=f"limit_{i}", user_id=test_user.id,
                invoice_amount_sats=25000, destination_address=f"bc1qlim{i}",
                preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
                claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
                status=SwapStatus.CREATED, status_history=[],
            ))
        await db_session.commit()

        svc = BoltzSwapService()
        swaps = await svc.get_swaps_for_user(db_session, test_user.id, limit=2)

        assert len(swaps) == 2


# ============================================================================
# Service — broadcast_transaction
# ============================================================================

class TestBroadcastTransaction:
    """Tests for broadcasting raw transactions via Boltz API."""

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService._request")
    async def test_broadcast_success(self, mock_request):
        from app.services.boltz_service import BoltzSwapService

        mock_request.return_value = ({"id": "txid_broadcast"}, None)
        svc = BoltzSwapService()
        txid, err = await svc.broadcast_transaction("0200000001...")

        assert txid == "txid_broadcast"
        assert err is None

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.BoltzSwapService._request")
    async def test_broadcast_error(self, mock_request):
        from app.services.boltz_service import BoltzSwapService

        mock_request.return_value = (None, "Invalid transaction")
        svc = BoltzSwapService()
        txid, err = await svc.broadcast_transaction("bad_hex")

        assert txid is None
        assert "Invalid transaction" in err


# ============================================================================
# Service — cancel edge cases
# ============================================================================

class TestCancelEdgeCases:
    """Tests for cancel in every non-cancellable status."""

    @pytest.mark.asyncio
    async def test_cancel_claiming_rejected(self, db_session, test_user):
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        swap = BoltzSwap(
            boltz_swap_id="cancel_claiming", user_id=test_user.id,
            invoice_amount_sats=25000, destination_address="bc1q",
            preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.CLAIMING, status_history=[],
        )
        db_session.add(swap)
        await db_session.commit()

        svc = BoltzSwapService()
        success, err = await svc.cancel_swap(db_session, swap)
        assert success is False
        assert "claiming" in err

    @pytest.mark.asyncio
    async def test_cancel_completed_rejected(self, db_session, test_user):
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        swap = BoltzSwap(
            boltz_swap_id="cancel_completed", user_id=test_user.id,
            invoice_amount_sats=25000, destination_address="bc1q",
            preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.COMPLETED, status_history=[],
        )
        db_session.add(swap)
        await db_session.commit()

        svc = BoltzSwapService()
        success, err = await svc.cancel_swap(db_session, swap)
        assert success is False
        assert "Cannot cancel" in err

    @pytest.mark.asyncio
    async def test_cancel_updates_status_history(self, db_session, test_user):
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        swap = BoltzSwap(
            boltz_swap_id="cancel_history", user_id=test_user.id,
            invoice_amount_sats=25000, destination_address="bc1q",
            preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.CREATED, status_history=[],
        )
        db_session.add(swap)
        await db_session.commit()

        svc = BoltzSwapService()
        await svc.cancel_swap(db_session, swap)

        assert swap.status == SwapStatus.CANCELLED
        assert swap.error_message == "Cancelled by user"
        assert swap.completed_at is not None
        assert any(h["status"] == "cancelled" for h in swap.status_history)


# ============================================================================
# Endpoint — additional coverage
# ============================================================================

class TestInitiateSwapAdditional:
    """Additional endpoint tests for POST /wallet/cold-storage/lightning."""

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.lnd_service")
    @patch("app.api.endpoints.cold_storage.boltz_service")
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_insufficient_lightning_balance(
        self, mock_settings, mock_boltz, mock_lnd, async_client, test_admin_user,
    ):
        """400 when Lightning channel balance is too low."""
        mock_settings.use_lnd = True
        mock_lnd.get_channel_balance = AsyncMock(return_value={"local_balance_sat": "10000"})

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/cold-storage/lightning",
            json={
                "amount_sats": 50000,
                "destination_address": "bc1qtestaddress1234567890123",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
        assert "Insufficient" in response.json()["detail"]

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.lnd_service")
    @patch("app.api.endpoints.cold_storage.boltz_service")
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_boltz_creation_error(
        self, mock_settings, mock_boltz, mock_lnd, async_client, test_admin_user,
    ):
        """502 when Boltz swap creation fails."""
        mock_settings.use_lnd = True
        mock_lnd.get_channel_balance = AsyncMock(return_value={"local_balance_sat": "1000000"})
        mock_boltz.create_reverse_swap = AsyncMock(return_value=(None, "Boltz server error"))

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/cold-storage/lightning",
            json={
                "amount_sats": 50000,
                "destination_address": "bc1qtestaddress1234567890123",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 502
        assert "Boltz server error" in response.json()["detail"]

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_address_too_short_rejected(self, mock_settings, async_client, test_admin_user):
        """Rejects destination address shorter than 26 chars (422)."""
        mock_settings.use_lnd = True

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/cold-storage/lightning",
            json={
                "amount_sats": 50000,
                "destination_address": "bc1qshort",  # 8 chars, too short
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_amount_above_max_rejected(self, mock_settings, async_client, test_admin_user):
        """Rejects amount above Boltz maximum (422)."""
        mock_settings.use_lnd = True

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/cold-storage/lightning",
            json={
                "amount_sats": 30_000_000,
                "destination_address": "bc1qtestaddress1234567890123",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422


class TestGetSwapStatusEndpointSuccess:
    """Tests for GET /wallet/cold-storage/lightning/{swap_id} success path."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    @patch("app.api.endpoints.cold_storage.boltz_service")
    async def test_status_success(self, mock_boltz, mock_settings, async_client, test_user):
        """Returns swap details for a valid swap."""
        mock_settings.use_lnd = True

        from app.models.boltz_swap import SwapStatus
        mock_swap = MagicMock()
        mock_swap.id = uuid4()
        mock_swap.user_id = test_user.id  # Must match requesting user
        mock_swap.boltz_swap_id = "status_test"
        mock_swap.status = SwapStatus.INVOICE_PAID
        mock_swap.boltz_status = "invoice.set"
        mock_swap.invoice_amount_sats = 100000
        mock_swap.onchain_amount_sats = 99200
        mock_swap.destination_address = "bc1qfull"
        mock_swap.fee_percentage = "0.5"
        mock_swap.miner_fee_sats = 795
        mock_swap.boltz_invoice = "lnbc1m..."
        mock_swap.claim_txid = None
        mock_swap.error_message = None
        mock_swap.created_at = datetime.now(timezone.utc)
        mock_swap.updated_at = datetime.now(timezone.utc)
        mock_swap.completed_at = None

        mock_boltz.get_swap_by_id = AsyncMock(return_value=mock_swap)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            f"/api/v1/wallet/cold-storage/lightning/{mock_swap.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["boltz_swap_id"] == "status_test"
        assert data["status"] == "invoice_paid"
        assert data["invoice_amount_sats"] == 100000
        assert data["onchain_amount_sats"] == 99200


class TestListSwapsEndpoint:
    """Tests for GET /wallet/cold-storage/lightning/swaps."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    @patch("app.api.endpoints.cold_storage.boltz_service")
    async def test_list_swaps_success(self, mock_boltz, mock_settings, async_client, test_user):
        """Returns list of swaps."""
        mock_settings.use_lnd = True

        from app.models.boltz_swap import SwapStatus

        mock_swap1 = MagicMock()
        mock_swap1.id = uuid4()
        mock_swap1.boltz_swap_id = "list_1"
        mock_swap1.status = SwapStatus.COMPLETED
        mock_swap1.boltz_status = "invoice.settled"
        mock_swap1.invoice_amount_sats = 50000
        mock_swap1.onchain_amount_sats = 49500
        mock_swap1.destination_address = "bc1q1"
        mock_swap1.fee_percentage = "0.5"
        mock_swap1.miner_fee_sats = 795
        mock_swap1.boltz_invoice = "lnbc..."
        mock_swap1.claim_txid = "tx1"
        mock_swap1.error_message = None
        mock_swap1.created_at = datetime.now(timezone.utc)
        mock_swap1.updated_at = datetime.now(timezone.utc)
        mock_swap1.completed_at = datetime.now(timezone.utc)

        mock_boltz.get_swaps_for_user = AsyncMock(return_value=[mock_swap1])

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/cold-storage/lightning/swaps",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["swaps"]) == 1
        assert data["swaps"][0]["boltz_swap_id"] == "list_1"
        assert data["swaps"][0]["claim_txid"] == "tx1"

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    @patch("app.api.endpoints.cold_storage.boltz_service")
    async def test_list_swaps_empty(self, mock_boltz, mock_settings, async_client, test_user):
        """Returns empty list when no swaps exist."""
        mock_settings.use_lnd = True
        mock_boltz.get_swaps_for_user = AsyncMock(return_value=[])

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/cold-storage/lightning/swaps",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["swaps"] == []


class TestCancelEndpointAdditional:
    """Additional cancel endpoint tests."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    @patch("app.api.endpoints.cold_storage.boltz_service")
    async def test_cancel_not_found(self, mock_boltz, mock_settings, async_client, test_admin_user):
        """404 when trying to cancel non-existent swap."""
        mock_settings.use_lnd = True
        mock_boltz.get_swap_by_id = AsyncMock(return_value=None)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        swap_id = str(uuid4())
        response = await async_client.post(
            f"/api/v1/wallet/cold-storage/lightning/{swap_id}/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_cancel_invalid_uuid(self, mock_settings, async_client, test_admin_user):
        """400 for invalid UUID on cancel."""
        mock_settings.use_lnd = True

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/cold-storage/lightning/not-valid-uuid/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_cancel_requires_admin(self, mock_settings, async_client, test_user):
        """Non-admin cannot cancel swaps."""
        mock_settings.use_lnd = True

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        swap_id = str(uuid4())
        response = await async_client.post(
            f"/api/v1/wallet/cold-storage/lightning/{swap_id}/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403


class TestEndpointAuth:
    """Tests that cold storage endpoints require authentication."""

    @pytest.mark.asyncio
    async def test_fees_unauthenticated(self, async_client):
        response = await async_client.get("/api/v1/wallet/cold-storage/lightning/fees")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_initiate_unauthenticated(self, async_client):
        response = await async_client.post(
            "/api/v1/wallet/cold-storage/lightning",
            json={"amount_sats": 50000, "destination_address": "bc1qtestaddress1234567890123"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_status_unauthenticated(self, async_client):
        swap_id = str(uuid4())
        response = await async_client.get(
            f"/api/v1/wallet/cold-storage/lightning/{swap_id}",
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_cancel_unauthenticated(self, async_client):
        swap_id = str(uuid4())
        response = await async_client.post(
            f"/api/v1/wallet/cold-storage/lightning/{swap_id}/cancel",
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_list_swaps_unauthenticated(self, async_client):
        response = await async_client.get("/api/v1/wallet/cold-storage/lightning/swaps")
        assert response.status_code == 403


# ============================================================================
# Model — additional
# ============================================================================

class TestBoltzSwapModelAdditional:
    """Additional model constraint tests."""

    @pytest.mark.asyncio
    async def test_unique_boltz_swap_id(self, db_session, test_user):
        """boltz_swap_id must be unique."""
        from app.models.boltz_swap import BoltzSwap, SwapStatus
        from sqlalchemy.exc import IntegrityError

        swap1 = BoltzSwap(
            boltz_swap_id="unique_test", user_id=test_user.id,
            invoice_amount_sats=25000, destination_address="bc1q1",
            preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.CREATED, status_history=[],
        )
        db_session.add(swap1)
        await db_session.commit()

        swap2 = BoltzSwap(
            boltz_swap_id="unique_test", user_id=test_user.id,
            invoice_amount_sats=50000, destination_address="bc1q2",
            preimage_hex="e" * 64, preimage_hash_hex="f" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.CREATED, status_history=[],
        )
        db_session.add(swap2)

        with pytest.raises(IntegrityError):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_default_direction_is_reverse(self, db_session, test_user):
        """Default direction is REVERSE."""
        from app.models.boltz_swap import BoltzSwap, SwapStatus, BoltzSwapDirection

        swap = BoltzSwap(
            boltz_swap_id="direction_test", user_id=test_user.id,
            invoice_amount_sats=25000, destination_address="bc1qdir",
            preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.CREATED, status_history=[],
        )
        db_session.add(swap)
        await db_session.commit()

        assert swap.direction == BoltzSwapDirection.REVERSE

    @pytest.mark.asyncio
    async def test_default_recovery_count_zero(self, db_session, test_user):
        """Default recovery count is 0."""
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        swap = BoltzSwap(
            boltz_swap_id="recovery_count_test", user_id=test_user.id,
            invoice_amount_sats=25000, destination_address="bc1qrc",
            preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            status=SwapStatus.CREATED, status_history=[],
        )
        db_session.add(swap)
        await db_session.commit()

        assert swap.recovery_count == 0


# ============================================================================
# Security Tests — Swap Ownership Verification
# ============================================================================

class TestSwapOwnershipVerification:
    """Tests that swap endpoints enforce ownership checks.

    Security: Any authenticated user should NOT be able to view or cancel
    another user's swap by guessing the swap UUID.
    """

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    @patch("app.api.endpoints.cold_storage.boltz_service")
    async def test_status_returns_404_for_other_users_swap(
        self, mock_boltz, mock_settings, async_client, test_user, test_admin_user,
    ):
        """GET status returns 404 when swap belongs to a different user."""
        mock_settings.use_lnd = True

        from app.models.boltz_swap import SwapStatus
        mock_swap = MagicMock()
        mock_swap.id = uuid4()
        mock_swap.boltz_swap_id = "ownership_test"
        mock_swap.user_id = test_admin_user.id  # Swap belongs to admin
        mock_swap.status = SwapStatus.CREATED
        mock_swap.boltz_status = "swap.created"
        mock_swap.invoice_amount_sats = 50000
        mock_swap.onchain_amount_sats = None
        mock_swap.destination_address = "bc1qtest"
        mock_swap.fee_percentage = None
        mock_swap.miner_fee_sats = None
        mock_swap.boltz_invoice = None
        mock_swap.claim_txid = None
        mock_swap.error_message = None
        mock_swap.created_at = datetime.now(timezone.utc)
        mock_swap.updated_at = datetime.now(timezone.utc)
        mock_swap.completed_at = None

        mock_boltz.get_swap_by_id = AsyncMock(return_value=mock_swap)

        from app.core.security import create_access_token
        # Regular user tries to view admin's swap
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            f"/api/v1/wallet/cold-storage/lightning/{mock_swap.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    @patch("app.api.endpoints.cold_storage.boltz_service")
    async def test_status_returns_200_for_own_swap(
        self, mock_boltz, mock_settings, async_client, test_user,
    ):
        """GET status returns 200 when swap belongs to the requesting user."""
        mock_settings.use_lnd = True

        from app.models.boltz_swap import SwapStatus
        mock_swap = MagicMock()
        mock_swap.id = uuid4()
        mock_swap.boltz_swap_id = "own_swap_test"
        mock_swap.user_id = test_user.id  # Swap belongs to requesting user
        mock_swap.status = SwapStatus.CREATED
        mock_swap.boltz_status = "swap.created"
        mock_swap.invoice_amount_sats = 50000
        mock_swap.onchain_amount_sats = None
        mock_swap.destination_address = "bc1qtest"
        mock_swap.fee_percentage = None
        mock_swap.miner_fee_sats = None
        mock_swap.boltz_invoice = None
        mock_swap.claim_txid = None
        mock_swap.error_message = None
        mock_swap.created_at = datetime.now(timezone.utc)
        mock_swap.updated_at = datetime.now(timezone.utc)
        mock_swap.completed_at = None

        mock_boltz.get_swap_by_id = AsyncMock(return_value=mock_swap)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            f"/api/v1/wallet/cold-storage/lightning/{mock_swap.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    @patch("app.api.endpoints.cold_storage.boltz_service")
    async def test_cancel_returns_404_for_other_users_swap(
        self, mock_boltz, mock_settings, async_client, test_admin_user, test_user,
    ):
        """POST cancel returns 404 when swap belongs to a different admin."""
        mock_settings.use_lnd = True

        mock_swap = MagicMock()
        mock_swap.id = uuid4()
        mock_swap.user_id = test_user.id  # Swap belongs to regular user

        mock_boltz.get_swap_by_id = AsyncMock(return_value=mock_swap)

        from app.core.security import create_access_token
        # Admin tries to cancel another user's swap
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            f"/api/v1/wallet/cold-storage/lightning/{mock_swap.id}/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404


# ============================================================================
# Security Tests — Bitcoin Address Validation
# ============================================================================

class TestBitcoinAddressValidation:
    """Tests for destination address format validation."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_valid_bech32_address(self, mock_settings, async_client, test_admin_user):
        """Accepts valid bech32 (bc1q...) address."""
        mock_settings.use_lnd = True
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        # bc1q address — will fail later at balance check, but should pass validation
        with patch("app.services.lnd_service.lnd_service") as mock_lnd, \
             patch("app.api.endpoints.cold_storage.boltz_service") as mock_boltz:
            mock_lnd.get_channel_balance = AsyncMock(return_value={"local_balance_sat": "0"})

            response = await async_client.post(
                "/api/v1/wallet/cold-storage/lightning",
                json={
                    "amount_sats": 50000,
                    "destination_address": "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            # 400 = insufficient balance (passed validation)
            assert response.status_code == 400

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_valid_bech32m_taproot_address(self, mock_settings, async_client, test_admin_user):
        """Accepts valid bech32m taproot (bc1p...) address."""
        mock_settings.use_lnd = True
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            mock_lnd.get_channel_balance = AsyncMock(return_value={"local_balance_sat": "0"})

            response = await async_client.post(
                "/api/v1/wallet/cold-storage/lightning",
                json={
                    "amount_sats": 50000,
                    "destination_address": "bc1p5cyxnuxmeuwuvkwfem96lqzszee2456yqe96gaamqtzmrssqhtsqvq4evs",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 400  # Insufficient balance, but passed validation

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_valid_p2pkh_address(self, mock_settings, async_client, test_admin_user):
        """Accepts valid P2PKH (1...) address."""
        mock_settings.use_lnd = True
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            mock_lnd.get_channel_balance = AsyncMock(return_value={"local_balance_sat": "0"})

            response = await async_client.post(
                "/api/v1/wallet/cold-storage/lightning",
                json={
                    "amount_sats": 50000,
                    "destination_address": "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 400

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_valid_p2sh_address(self, mock_settings, async_client, test_admin_user):
        """Accepts valid P2SH (3...) address."""
        mock_settings.use_lnd = True
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            mock_lnd.get_channel_balance = AsyncMock(return_value={"local_balance_sat": "0"})

            response = await async_client.post(
                "/api/v1/wallet/cold-storage/lightning",
                json={
                    "amount_sats": 50000,
                    "destination_address": "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 400

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_rejects_testnet_address(self, mock_settings, async_client, test_admin_user):
        """Rejects testnet (tb1...) addresses."""
        mock_settings.use_lnd = True
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/cold-storage/lightning",
            json={
                "amount_sats": 50000,
                "destination_address": "tb1qw508d6qejxtdg4y5r3zarvary0c5xw7kxpjzsx",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_rejects_random_string(self, mock_settings, async_client, test_admin_user):
        """Rejects non-Bitcoin-address strings."""
        mock_settings.use_lnd = True
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/cold-storage/lightning",
            json={
                "amount_sats": 50000,
                "destination_address": "not_a_valid_bitcoin_address_at_all_xxxx",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    @patch("app.api.endpoints.cold_storage.settings")
    async def test_rejects_ethereum_address(self, mock_settings, async_client, test_admin_user):
        """Rejects Ethereum addresses."""
        mock_settings.use_lnd = True
        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/cold-storage/lightning",
            json={
                "amount_sats": 50000,
                "destination_address": "0x742d35Cc6634C0532925a3b844Bc9e7595f2bD6E",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422


# ============================================================================
# Security Tests — Private Key Not in Process Args
# ============================================================================

class TestKeypairGenerationSecurity:
    """Tests that keypair generation doesn't expose private keys."""

    @pytest.mark.asyncio
    async def test_keypair_uses_stdin_not_args(self):
        """Private key is passed via stdin, not command-line args."""
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
            await _generate_keypair()

        # Verify stdin (input=) was used in communicate()
        comm_call = mock_proc.communicate.call_args
        assert comm_call.kwargs.get("input") is not None or (
            comm_call.args and comm_call.args[0] is not None
        )

        # Verify the command-line args contain process.stdin (not hardcoded key)
        exec_args = mock_exec.call_args.args
        cmd_str = " ".join(str(a) for a in exec_args)
        assert "process.stdin" in cmd_str


# ============================================================================
# Security Tests — Claim Timeout Message
# ============================================================================

class TestClaimTimeoutMessage:
    """Tests that the timeout error message reflects the actual timeout."""

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.settings")
    @patch("app.services.boltz_service.asyncio.create_subprocess_exec")
    async def test_timeout_message_with_tor(self, mock_exec, mock_settings, db_session, test_user):
        """Timeout message shows 120s when using Tor proxy."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        mock_settings.boltz_use_tor = True
        mock_settings.lnd_tor_proxy = "socks5://tor:9050"
        mock_settings.boltz_onion_url = "http://boltz.onion/api/v2"

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()
        mock_exec.return_value = mock_proc

        swap = BoltzSwap(
            boltz_swap_id="timeout_test", user_id=test_user.id,
            invoice_amount_sats=50000, destination_address="bc1qtimeout",
            preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            boltz_refund_public_key_hex="03" + "e" * 64,
            boltz_swap_tree_json={"claimLeaf": {"version": 192, "output": "aa"}},
            status=SwapStatus.CLAIMING, status_history=[],
        )

        svc = BoltzSwapService()
        txid, err = await svc.cooperative_claim(swap, "0200000001...")

        assert txid is None
        assert "120s" in err

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.settings")
    @patch("app.services.boltz_service.asyncio.create_subprocess_exec")
    async def test_timeout_message_without_tor(self, mock_exec, mock_settings, db_session, test_user):
        """Timeout message shows 60s when not using Tor."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        mock_settings.boltz_use_tor = False
        mock_settings.lnd_tor_proxy = ""
        mock_settings.boltz_api_url = "https://api.boltz.exchange/v2"

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()
        mock_exec.return_value = mock_proc

        swap = BoltzSwap(
            boltz_swap_id="timeout_test_clearnet", user_id=test_user.id,
            invoice_amount_sats=50000, destination_address="bc1qtimeout2",
            preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            boltz_refund_public_key_hex="03" + "e" * 64,
            boltz_swap_tree_json={"claimLeaf": {"version": 192, "output": "aa"}},
            status=SwapStatus.CLAIMING, status_history=[],
        )

        svc = BoltzSwapService()
        txid, err = await svc.cooperative_claim(swap, "0200000001...")

        assert txid is None
        assert "60s" in err


# ============================================================================
# Security Tests — Claim Input Sanitization
# ============================================================================

class TestClaimErrorSanitization:
    """Tests that claim errors don't leak sensitive data."""

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.asyncio.create_subprocess_exec")
    async def test_claim_failure_stderr_truncated(self, mock_exec, db_session, test_user):
        """Claim error messages are truncated to prevent data leakage."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        # Simulate failure with verbose stderr
        sensitive_stderr = "Error: " + "x" * 1000

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", sensitive_stderr.encode()))
        mock_proc.returncode = 1
        mock_exec.return_value = mock_proc

        swap = BoltzSwap(
            boltz_swap_id="sanitize_test", user_id=test_user.id,
            invoice_amount_sats=50000, destination_address="bc1qsanitize",
            preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            boltz_refund_public_key_hex="03" + "e" * 64,
            boltz_swap_tree_json={"claimLeaf": {"version": 192, "output": "aa"}},
            status=SwapStatus.CLAIMING, status_history=[],
        )

        svc = BoltzSwapService()
        txid, err = await svc.cooperative_claim(swap, "0200000001...")

        assert txid is None
        assert err is not None
        # "Claim script failed: " prefix + max 500 chars of stderr
        assert len(err) <= 600


# ============================================================================
# Security Tests — Cooperative Claim Passes Proxy
# ============================================================================

class TestClaimProxyPassing:
    """Tests that cooperative_claim passes SOCKS proxy when Tor is enabled."""

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.settings")
    @patch("app.services.boltz_service.asyncio.create_subprocess_exec")
    async def test_claim_includes_socks_proxy(self, mock_exec, mock_settings, db_session, test_user):
        """Claim input includes socksProxy field when Tor is enabled."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        mock_settings.boltz_use_tor = True
        mock_settings.lnd_tor_proxy = "socks5://tor-proxy:9050"
        mock_settings.boltz_onion_url = "http://boltz.onion/api/v2"

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(
            json.dumps({"txid": "tor_claim_txid"}).encode(),
            b"",
        ))
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        swap = BoltzSwap(
            boltz_swap_id="proxy_test", user_id=test_user.id,
            invoice_amount_sats=50000, destination_address="bc1qproxy",
            preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            boltz_refund_public_key_hex="03" + "e" * 64,
            boltz_swap_tree_json={"claimLeaf": {"version": 192, "output": "aa"}},
            status=SwapStatus.CLAIMING, status_history=[],
        )

        svc = BoltzSwapService()
        txid, err = await svc.cooperative_claim(swap, "0200000001...")

        assert txid == "tor_claim_txid"
        # Verify the input passed to subprocess includes socksProxy
        comm_call = mock_proc.communicate.call_args
        input_data = comm_call.kwargs.get("input") or (comm_call.args[0] if comm_call.args else b"")
        input_json = json.loads(input_data.decode())
        assert input_json.get("socksProxy") == "socks5://tor-proxy:9050"
        assert input_json.get("boltzUrl") == "http://boltz.onion/api/v2"

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.settings")
    @patch("app.services.boltz_service.asyncio.create_subprocess_exec")
    async def test_claim_no_proxy_when_tor_disabled(self, mock_exec, mock_settings, db_session, test_user):
        """Claim input does NOT include socksProxy when Tor is disabled."""
        from app.services.boltz_service import BoltzSwapService
        from app.models.boltz_swap import BoltzSwap, SwapStatus

        mock_settings.boltz_use_tor = False
        mock_settings.lnd_tor_proxy = ""
        mock_settings.boltz_api_url = "https://api.boltz.exchange/v2"

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(
            json.dumps({"txid": "clearnet_claim_txid"}).encode(),
            b"",
        ))
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        swap = BoltzSwap(
            boltz_swap_id="no_proxy_test", user_id=test_user.id,
            invoice_amount_sats=50000, destination_address="bc1qnoproxy",
            preimage_hex="a" * 64, preimage_hash_hex="b" * 64,
            claim_private_key_hex="c" * 64, claim_public_key_hex="02" + "d" * 64,
            boltz_refund_public_key_hex="03" + "e" * 64,
            boltz_swap_tree_json={"claimLeaf": {"version": 192, "output": "aa"}},
            status=SwapStatus.CLAIMING, status_history=[],
        )

        svc = BoltzSwapService()
        txid, err = await svc.cooperative_claim(swap, "0200000001...")

        assert txid == "clearnet_claim_txid"
        comm_call = mock_proc.communicate.call_args
        input_data = comm_call.kwargs.get("input") or (comm_call.args[0] if comm_call.args else b"")
        input_json = json.loads(input_data.decode())
        assert "socksProxy" not in input_json
        assert input_json.get("boltzUrl") == "https://api.boltz.exchange/v2"
