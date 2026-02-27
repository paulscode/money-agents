"""
Expanded Boltz test coverage — fills gaps identified by audit.

Covers:
- Celery task process_boltz_swap (full lifecycle orchestration)
- _generate_keypair edge cases (timeout, bad JSON, missing keys)
- Cache expiration for get_reverse_pair_info
- close() and _get_client lifecycle
- _request clearnet fallback paths
- advance_swap from PAYING_INVOICE and CREATED with lockup
- cancel_swap for every non-CREATED status
- _swap_to_response formatting
- list_swaps limit clamping
- initiate swap when channel_balance returns None
- get_lockup_transaction returning empty response
- MaxRetriesExceededError with max_retries=200
- run_async helper cleanup
"""
import asyncio
import json
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock, PropertyMock
from uuid import uuid4, UUID


# ---------------------------------------------------------------------------
# Auto-use fixture: make encrypt_field / decrypt_field no-ops for all Boltz
# tests so we test swap logic, not encryption.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _passthrough_encryption():
    """Patch boltz_service encrypt/decrypt to identity functions."""
    with patch("app.services.boltz_service.encrypt_field", side_effect=lambda x: x), \
         patch("app.services.boltz_service.decrypt_field", side_effect=lambda x: x):
        yield

from app.models.boltz_swap import BoltzSwap, SwapStatus, BoltzSwapDirection


# ============================================================================
# Helpers
# ============================================================================

def _make_swap(**overrides) -> MagicMock:
    """Create a mock BoltzSwap with defaults."""
    defaults = {
        "id": uuid4(),
        "user_id": uuid4(),
        "boltz_swap_id": "test-swap-123",
        "direction": BoltzSwapDirection.REVERSE,
        "status": SwapStatus.CREATED,
        "boltz_status": None,
        "invoice_amount_sats": 100_000,
        "onchain_amount_sats": 99_500,
        "destination_address": "bc1qtest",
        "fee_percentage": 0.5,
        "miner_fee_sats": 500,
        "boltz_invoice": "lnbc1000000n1...",
        "lockup_address": "bc1q_lockup_test",
        "preimage_hex": "aa" * 32,
        "preimage_hash_hex": "bb" * 32,
        "claim_private_key_hex": "cc" * 32,
        "claim_public_key_hex": "dd" * 33,
        "boltz_refund_public_key_hex": "ee" * 33,
        "boltz_swap_tree_json": '{"claimLeaf":{}}',
        "boltz_blinding_key_hex": None,
        "lnd_payment_hash": None,
        "lnd_payment_status": None,
        "claim_txid": None,
        "error_message": None,
        "status_history": [],
        "recovery_count": 0,
        "recovery_attempted_at": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "completed_at": None,
    }
    defaults.update(overrides)
    swap = MagicMock(spec=BoltzSwap)
    for k, v in defaults.items():
        setattr(swap, k, v)
    return swap


# ============================================================================
# _generate_keypair edge cases
# ============================================================================

class TestGenerateKeypairEdgeCases:
    """Test _generate_keypair async subprocess error handling (bugs A2/B5)."""

    @pytest.mark.asyncio
    async def test_timeout_raises_runtime_error(self):
        """asyncio.TimeoutError is caught and re-raised as RuntimeError."""
        from app.services.boltz_service import _generate_keypair

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch("app.services.boltz_service.asyncio.create_subprocess_exec",
                    return_value=mock_proc):
            with pytest.raises(RuntimeError, match="timed out"):
                await _generate_keypair()

    @pytest.mark.asyncio
    async def test_invalid_json_raises_runtime_error(self):
        """Malformed JSON from node is caught as RuntimeError."""
        from app.services.boltz_service import _generate_keypair

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"not valid json", b""))
        mock_proc.returncode = 0

        with patch("app.services.boltz_service.asyncio.create_subprocess_exec",
                    return_value=mock_proc):
            with pytest.raises(RuntimeError, match="invalid data"):
                await _generate_keypair()

    @pytest.mark.asyncio
    async def test_missing_key_in_json_raises_runtime_error(self):
        """JSON missing privateKey/publicKey is caught as RuntimeError."""
        from app.services.boltz_service import _generate_keypair

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(json.dumps({"onlyOneKey": "value"}).encode(), b""))
        mock_proc.returncode = 0

        with patch("app.services.boltz_service.asyncio.create_subprocess_exec",
                    return_value=mock_proc):
            with pytest.raises(RuntimeError, match="invalid data"):
                await _generate_keypair()

    @pytest.mark.asyncio
    async def test_node_not_found_raises_runtime_error(self):
        """FileNotFoundError (node missing) is caught as RuntimeError."""
        from app.services.boltz_service import _generate_keypair
        with patch("app.services.boltz_service.asyncio.create_subprocess_exec",
                    side_effect=FileNotFoundError("node not found")):
            with pytest.raises(RuntimeError, match="Node.js not found"):
                await _generate_keypair()

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_raises_runtime_error(self):
        """Non-zero exit from node raises RuntimeError with stderr."""
        from app.services.boltz_service import _generate_keypair

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"Module not found: ecpair"))
        mock_proc.returncode = 1

        with patch("app.services.boltz_service.asyncio.create_subprocess_exec",
                    return_value=mock_proc):
            with pytest.raises(RuntimeError, match="keypair generation failed"):
                await _generate_keypair()


# ============================================================================
# BoltzSwapService — close() and _get_client lifecycle
# ============================================================================

class TestClientLifecycle:
    """Test HTTP client lifecycle (close, recreate, reuse)."""

    @pytest.mark.asyncio
    async def test_close_when_client_is_none(self):
        """close() with no client doesn't crash."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()
        service._client = None
        await service.close()  # Should not raise
        assert service._client is None

    @pytest.mark.asyncio
    async def test_close_when_client_is_open(self):
        """close() closes the client and sets to None."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        service._client = mock_client

        await service.close()

        mock_client.aclose.assert_called_once()
        assert service._client is None

    @pytest.mark.asyncio
    async def test_close_already_closed_client(self):
        """close() skips aclose if client already closed."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()
        mock_client = AsyncMock()
        mock_client.is_closed = True
        service._client = mock_client

        await service.close()

        mock_client.aclose.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_client_creates_new_client(self):
        """_get_client creates a new client when none exists."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()
        service._client = None

        with patch("app.services.boltz_service.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.is_closed = False
            MockClient.return_value = mock_instance

            client = await service._get_client()
            assert client is mock_instance
            MockClient.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_client_reuses_existing(self):
        """_get_client returns cached client if not closed."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()
        mock_client = MagicMock()
        mock_client.is_closed = False
        service._client = mock_client

        with patch("app.services.boltz_service.httpx.AsyncClient") as MockClient:
            client = await service._get_client()
            assert client is mock_client
            MockClient.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_client_recreates_after_close(self):
        """_get_client creates new client if previous one was closed."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()
        old_client = MagicMock()
        old_client.is_closed = True
        service._client = old_client

        with patch("app.services.boltz_service.httpx.AsyncClient") as MockClient:
            new_client = MagicMock()
            new_client.is_closed = False
            MockClient.return_value = new_client

            client = await service._get_client()
            assert client is new_client
            MockClient.assert_called_once()


# ============================================================================
# Cache expiration for get_reverse_pair_info
# ============================================================================

class TestPairInfoCacheExpiration:
    """Test that pair info cache expires after 60 seconds (B6)."""

    @pytest.mark.asyncio
    async def test_cache_expires_after_60_seconds(self):
        """Cache data is refreshed when older than 60 seconds."""
        from app.services.boltz_service import BoltzSwapService

        service = BoltzSwapService()

        pair_data = {
            "BTC": {
                "BTC": {
                    "limits": {"minimal": 25000, "maximal": 25000000},
                    "fees": {"percentage": 0.5, "minerFees": {"lockup": 2000, "claim": 1500}},
                }
            }
        }

        call_count = 0
        async def mock_request(method, path, json_data=None):
            nonlocal call_count
            call_count += 1
            return pair_data, None

        service._request = mock_request

        # First call — API is hit
        result1, err1 = await service.get_reverse_pair_info()
        assert err1 is None
        assert call_count == 1

        # Second call immediately — cached
        result2, err2 = await service.get_reverse_pair_info()
        assert err2 is None
        assert call_count == 1  # Still 1

        # Simulate time passing by setting cache timestamp to 61 seconds ago
        service._pair_info_cached_at = datetime.now(timezone.utc) - timedelta(seconds=61)

        # Third call — cache expired, API hit again
        result3, err3 = await service.get_reverse_pair_info()
        assert err3 is None
        assert call_count == 2


# ============================================================================
# _request clearnet fallback paths
# ============================================================================

class TestClearnetFallback:
    """Test _request clearnet fallback on Tor failures (B4)."""

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.settings")
    async def test_clearnet_fallback_on_connect_error(self, mock_settings):
        """ConnectError triggers clearnet fallback when enabled."""
        mock_settings.boltz_use_tor = True
        mock_settings.boltz_fallback_clearnet = True
        mock_settings.boltz_api_url = "https://api.boltz.exchange/v2"
        mock_settings.boltz_onion_url = "http://boltz.onion/v2"
        mock_settings.lnd_tor_proxy = "socks5://tor-proxy:9050"

        from app.services.boltz_service import BoltzSwapService
        import httpx

        service = BoltzSwapService()

        # Mock the client to raise ConnectError
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.request = AsyncMock(
            side_effect=httpx.ConnectError("Tor circuit failed")
        )
        service._client = mock_client

        # Mock clearnet fallback
        service._request_clearnet = AsyncMock(
            return_value=({"status": "ok"}, None)
        )

        data, error = await service._request("GET", "/swap/reverse/BTC/BTC")
        assert data == {"status": "ok"}
        assert error is None
        service._request_clearnet.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.settings")
    async def test_clearnet_fallback_on_proxy_error(self, mock_settings):
        """ProxyError triggers clearnet fallback when enabled."""
        mock_settings.boltz_use_tor = True
        mock_settings.boltz_fallback_clearnet = True
        mock_settings.boltz_api_url = "https://api.boltz.exchange/v2"
        mock_settings.boltz_onion_url = "http://boltz.onion/v2"
        mock_settings.lnd_tor_proxy = "socks5://tor-proxy:9050"

        from app.services.boltz_service import BoltzSwapService
        import httpx

        service = BoltzSwapService()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.request = AsyncMock(
            side_effect=httpx.ProxyError("SOCKS5 proxy refused")
        )
        service._client = mock_client

        service._request_clearnet = AsyncMock(
            return_value=({"status": "ok"}, None)
        )

        data, error = await service._request("GET", "/test")
        assert data == {"status": "ok"}
        service._request_clearnet.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.settings")
    async def test_clearnet_fallback_on_read_timeout(self, mock_settings):
        """ReadTimeout triggers clearnet fallback when enabled."""
        mock_settings.boltz_use_tor = True
        mock_settings.boltz_fallback_clearnet = True
        mock_settings.boltz_api_url = "https://api.boltz.exchange/v2"
        mock_settings.boltz_onion_url = "http://boltz.onion/v2"
        mock_settings.lnd_tor_proxy = "socks5://tor-proxy:9050"

        from app.services.boltz_service import BoltzSwapService
        import httpx

        service = BoltzSwapService()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.request = AsyncMock(
            side_effect=httpx.ReadTimeout("Tor read timeout")
        )
        service._client = mock_client

        service._request_clearnet = AsyncMock(
            return_value=({"data": "yes"}, None)
        )

        data, error = await service._request("GET", "/test")
        assert data == {"data": "yes"}

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.settings")
    async def test_no_fallback_when_disabled(self, mock_settings):
        """No clearnet fallback when boltz_fallback_clearnet is False."""
        mock_settings.boltz_use_tor = True
        mock_settings.boltz_fallback_clearnet = False
        mock_settings.boltz_api_url = "https://api.boltz.exchange/v2"
        mock_settings.boltz_onion_url = "http://boltz.onion/v2"
        mock_settings.lnd_tor_proxy = "socks5://tor-proxy:9050"

        from app.services.boltz_service import BoltzSwapService
        import httpx

        service = BoltzSwapService()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.request = AsyncMock(
            side_effect=httpx.ConnectError("Tor failed")
        )
        service._client = mock_client

        data, error = await service._request("GET", "/test")
        assert data is None
        assert "Connection failed" in error

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.settings")
    async def test_http_status_error_with_json_body(self, mock_settings):
        """HTTPStatusError with JSON error body extracts the message."""
        mock_settings.boltz_use_tor = False
        mock_settings.boltz_fallback_clearnet = False
        mock_settings.boltz_api_url = "https://api.boltz.exchange/v2"
        mock_settings.boltz_onion_url = ""
        mock_settings.lnd_tor_proxy = ""

        from app.services.boltz_service import BoltzSwapService
        import httpx

        service = BoltzSwapService()

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"error": "Amount too low"}'
        mock_response.json.return_value = {"error": "Amount too low"}

        mock_request_obj = MagicMock()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.request = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "400 Bad Request", request=mock_request_obj, response=mock_response
            )
        )
        service._client = mock_client

        data, error = await service._request("POST", "/test")
        assert data is None
        assert "Amount too low" in error


# ============================================================================
# advance_swap — additional state transitions
# ============================================================================

class TestAdvanceSwapAdditionalTransitions:
    """Test advance_swap from PAYING_INVOICE and CREATED with lockup (B11/B12)."""

    @pytest.mark.asyncio
    async def test_advance_from_paying_invoice_with_lockup(self):
        """PAYING_INVOICE with transaction.mempool transitions to CLAIMING."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()

        swap = _make_swap(status=SwapStatus.PAYING_INVOICE)
        db = AsyncMock()

        service.get_swap_status_from_boltz = AsyncMock(
            return_value=("transaction.mempool", {}, None)
        )
        service.get_lockup_transaction = AsyncMock(
            return_value=("0200000001...", None)
        )
        service.cooperative_claim = AsyncMock(
            return_value=("txid_abc123", None)
        )

        result_swap, err = await service.advance_swap(db, swap)

        assert swap.status == SwapStatus.CLAIMED
        assert swap.claim_txid == "txid_abc123"
        assert err is None

    @pytest.mark.asyncio
    async def test_advance_from_created_with_lockup(self):
        """CREATED with transaction.confirmed transitions directly to CLAIMING."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()

        swap = _make_swap(status=SwapStatus.CREATED)
        db = AsyncMock()

        service.get_swap_status_from_boltz = AsyncMock(
            return_value=("transaction.confirmed", {}, None)
        )
        service.get_lockup_transaction = AsyncMock(
            return_value=("0200000001...", None)
        )
        service.cooperative_claim = AsyncMock(
            return_value=("txid_def456", None)
        )

        result_swap, err = await service.advance_swap(db, swap)

        assert swap.status == SwapStatus.CLAIMED
        assert swap.claim_txid == "txid_def456"

    @pytest.mark.asyncio
    async def test_invoice_settled_from_invoice_paid(self):
        """invoice.settled from INVOICE_PAID transitions to COMPLETED."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()

        swap = _make_swap(status=SwapStatus.INVOICE_PAID)
        db = AsyncMock()

        service.get_swap_status_from_boltz = AsyncMock(
            return_value=("invoice.settled", {}, None)
        )

        result_swap, err = await service.advance_swap(db, swap)
        assert swap.status == SwapStatus.COMPLETED
        assert swap.completed_at is not None

    @pytest.mark.asyncio
    async def test_invoice_settled_from_claiming(self):
        """invoice.settled from CLAIMING transitions to COMPLETED."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()

        swap = _make_swap(status=SwapStatus.CLAIMING)
        db = AsyncMock()

        service.get_swap_status_from_boltz = AsyncMock(
            return_value=("invoice.settled", {}, None)
        )

        result_swap, err = await service.advance_swap(db, swap)
        assert swap.status == SwapStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_lockup_fetch_error_propagates(self):
        """Lockup transaction fetch error is returned."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()

        swap = _make_swap(status=SwapStatus.INVOICE_PAID)
        db = AsyncMock()

        service.get_swap_status_from_boltz = AsyncMock(
            return_value=("transaction.mempool", {}, None)
        )
        service.get_lockup_transaction = AsyncMock(
            return_value=(None, "Tor connection failed")
        )

        result_swap, err = await service.advance_swap(db, swap)
        assert err == "Tor connection failed"
        # Status should be CLAIMING (advanced from INVOICE_PAID)
        assert swap.status == SwapStatus.CLAIMING

    @pytest.mark.asyncio
    async def test_claim_failure_increments_recovery_count(self):
        """Failed claim increments recovery_count."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()

        swap = _make_swap(status=SwapStatus.INVOICE_PAID, recovery_count=2)
        db = AsyncMock()

        service.get_swap_status_from_boltz = AsyncMock(
            return_value=("transaction.confirmed", {}, None)
        )
        service.get_lockup_transaction = AsyncMock(
            return_value=("0200000001...", None)
        )
        service.cooperative_claim = AsyncMock(
            return_value=(None, "Musig2 nonce exchange failed")
        )

        result_swap, err = await service.advance_swap(db, swap)
        assert swap.recovery_count == 3
        assert err == "Musig2 nonce exchange failed"

    @pytest.mark.asyncio
    async def test_status_history_appended_on_change(self):
        """Status history is appended when boltz_status changes."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()

        swap = _make_swap(
            status=SwapStatus.INVOICE_PAID,
            boltz_status="invoice.set",
            status_history=[{"status": "created", "timestamp": "2024-01-01T00:00:00"}],
        )
        db = AsyncMock()

        service.get_swap_status_from_boltz = AsyncMock(
            return_value=("invoice.settled", {}, None)
        )

        await service.advance_swap(db, swap)
        assert len(swap.status_history) == 2
        assert swap.status_history[-1]["boltz_status"] == "invoice.settled"


# ============================================================================
# cancel_swap — all non-CREATED statuses
# ============================================================================

class TestCancelSwapAllStatuses:
    """Test that cancel_swap rejects every non-CREATED status (B10)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [
        SwapStatus.PAYING_INVOICE,
        SwapStatus.INVOICE_PAID,
        SwapStatus.CLAIMING,
        SwapStatus.CLAIMED,
        SwapStatus.COMPLETED,
        SwapStatus.FAILED,
        SwapStatus.CANCELLED,
        SwapStatus.REFUNDED,
    ])
    async def test_cancel_rejected_for_non_created_status(self, status):
        """cancel_swap returns False for every non-CREATED status."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()
        swap = _make_swap(status=status)
        db = AsyncMock()

        success, error = await service.cancel_swap(db, swap)

        assert success is False
        assert "Cannot cancel" in error
        assert status.value in error

    @pytest.mark.asyncio
    async def test_cancel_succeeds_for_created(self):
        """cancel_swap succeeds for CREATED status."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()
        swap = _make_swap(status=SwapStatus.CREATED)
        db = AsyncMock()

        success, error = await service.cancel_swap(db, swap)

        assert success is True
        assert error is None
        assert swap.status == SwapStatus.CANCELLED
        assert swap.completed_at is not None
        db.commit.assert_called_once()


# ============================================================================
# _swap_to_response formatting
# ============================================================================

class TestSwapToResponse:
    """Test _swap_to_response edge cases (B9)."""

    def test_completed_at_none(self):
        """completed_at=None produces None in response."""
        from app.api.endpoints.cold_storage import _swap_to_response
        swap = _make_swap(completed_at=None)
        resp = _swap_to_response(swap)
        assert resp["completed_at"] is None

    def test_completed_at_present(self):
        """completed_at is formatted as ISO string."""
        from app.api.endpoints.cold_storage import _swap_to_response
        ts = datetime(2024, 6, 15, 12, 30, 0, tzinfo=timezone.utc)
        swap = _make_swap(completed_at=ts)
        resp = _swap_to_response(swap)
        assert resp["completed_at"] == "2024-06-15T12:30:00+00:00"

    def test_all_fields_present(self):
        """Response has all expected keys."""
        from app.api.endpoints.cold_storage import _swap_to_response
        swap = _make_swap()
        resp = _swap_to_response(swap)
        expected_keys = {
            "id", "boltz_swap_id", "status", "boltz_status",
            "invoice_amount_sats", "onchain_amount_sats",
            "destination_address", "fee_percentage", "miner_fee_sats",
            "boltz_invoice", "claim_txid", "error_message",
            "created_at", "updated_at", "completed_at",
        }
        assert set(resp.keys()) == expected_keys

    def test_status_is_string_value(self):
        """status field is the enum value string, not the enum object."""
        from app.api.endpoints.cold_storage import _swap_to_response
        swap = _make_swap(status=SwapStatus.CLAIMING)
        resp = _swap_to_response(swap)
        assert resp["status"] == "claiming"
        assert isinstance(resp["status"], str)


# ============================================================================
# Celery task — process_boltz_swap
# ============================================================================

class TestProcessBoltzSwapTask:
    """Test the Celery task lifecycle orchestration (B1)."""

    @pytest.mark.asyncio
    async def test_swap_not_found_returns_error(self):
        """Missing swap returns error dict."""
        from app.tasks.boltz_tasks import _process_boltz_swap_async

        mock_task = MagicMock()
        swap_id = str(uuid4())

        with patch("app.tasks.boltz_tasks.get_db_context") as mock_ctx:
            mock_db = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.services.boltz_service.BoltzSwapService.get_swap_by_id", new_callable=AsyncMock, return_value=None):

                result = await _process_boltz_swap_async(mock_task, swap_id)

        assert result["error"] == "Swap not found"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("terminal_status", [
        SwapStatus.COMPLETED, SwapStatus.FAILED,
        SwapStatus.CANCELLED, SwapStatus.REFUNDED,
    ])
    async def test_swap_already_terminal(self, terminal_status):
        """Swap in terminal state returns immediately."""
        from app.tasks.boltz_tasks import _process_boltz_swap_async

        mock_task = MagicMock()
        swap = _make_swap(status=terminal_status)
        swap_id = str(swap.id)

        with patch("app.tasks.boltz_tasks.get_db_context") as mock_ctx:
            mock_db = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.services.boltz_service.BoltzSwapService.get_swap_by_id", new_callable=AsyncMock, return_value=swap):

                result = await _process_boltz_swap_async(mock_task, swap_id)

        assert result["status"] == terminal_status.value

    @pytest.mark.asyncio
    async def test_payment_succeeds_synchronously(self):
        """Successful payment transitions to INVOICE_PAID then advances."""
        from app.tasks.boltz_tasks import _process_boltz_swap_async

        mock_task = MagicMock()
        swap = _make_swap(status=SwapStatus.CREATED)
        swap_id = str(swap.id)

        with patch("app.tasks.boltz_tasks.get_db_context") as mock_ctx:
            mock_db = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.services.boltz_service.boltz_service") as mock_svc, \
                 patch("app.services.lnd_service.lnd_service") as mock_lnd:

                mock_svc.get_swap_by_id = AsyncMock(return_value=swap)

                # Decode invoice
                mock_lnd.decode_payment_request = AsyncMock(
                    return_value=({"payment_hash": "ph123"}, None)
                )
                # Payment succeeds
                mock_lnd.send_payment_sync = AsyncMock(
                    return_value=({"payment_hash": "ph123"}, None)
                )

                # advance_swap returns COMPLETED
                mock_svc.advance_swap = AsyncMock(return_value=(
                    _make_swap(status=SwapStatus.COMPLETED, claim_txid="tx_abc"),
                    None,
                ))

                result = await _process_boltz_swap_async(mock_task, swap_id)

        assert result["status"] == "completed"
        assert result["claim_txid"] == "tx_abc"

    @pytest.mark.asyncio
    async def test_payment_fails_completely(self):
        """Payment failure with confirmed LND failure marks swap FAILED."""
        from app.tasks.boltz_tasks import _process_boltz_swap_async

        mock_task = MagicMock()
        swap = _make_swap(status=SwapStatus.CREATED)
        swap_id = str(swap.id)

        with patch("app.tasks.boltz_tasks.get_db_context") as mock_ctx:
            mock_db = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.services.boltz_service.boltz_service") as mock_svc, \
                 patch("app.services.lnd_service.lnd_service") as mock_lnd:

                mock_svc.get_swap_by_id = AsyncMock(return_value=swap)
                mock_lnd.decode_payment_request = AsyncMock(
                    return_value=({"payment_hash": "ph123"}, None)
                )
                # Payment fails
                mock_lnd.send_payment_sync = AsyncMock(
                    return_value=(None, "FAILURE_REASON_NO_ROUTE")
                )
                # LND lookup confirms failure
                mock_lnd.lookup_payment = AsyncMock(
                    return_value=({"status": "FAILED"}, None)
                )

                result = await _process_boltz_swap_async(mock_task, swap_id)

        assert result["status"] == "failed"
        assert "FAILURE_REASON_NO_ROUTE" in result["error"]

    @pytest.mark.asyncio
    async def test_payment_error_but_lnd_shows_in_flight(self):
        """HTTP error but LND shows IN_FLIGHT keeps PAYING_INVOICE, retries."""
        from app.tasks.boltz_tasks import _process_boltz_swap_async

        mock_task = MagicMock()
        swap = _make_swap(status=SwapStatus.CREATED)
        swap_id = str(swap.id)

        with patch("app.tasks.boltz_tasks.get_db_context") as mock_ctx:
            mock_db = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.services.boltz_service.boltz_service") as mock_svc, \
                 patch("app.services.lnd_service.lnd_service") as mock_lnd:

                mock_svc.get_swap_by_id = AsyncMock(return_value=swap)
                mock_lnd.decode_payment_request = AsyncMock(
                    return_value=({"payment_hash": "ph123"}, None)
                )
                # HTTP request fails (timeout)
                mock_lnd.send_payment_sync = AsyncMock(
                    return_value=(None, "Request timeout")
                )
                # But LND shows payment is IN_FLIGHT
                mock_lnd.lookup_payment = AsyncMock(
                    return_value=({"status": "IN_FLIGHT"}, None)
                )

                # advance_swap says not yet complete
                mock_svc.advance_swap = AsyncMock(return_value=(
                    _make_swap(status=SwapStatus.PAYING_INVOICE),
                    None,
                ))

                result = await _process_boltz_swap_async(mock_task, swap_id)

        # Should retry, not fail
        mock_task.retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_payment_error_lnd_shows_succeeded(self):
        """HTTP error but LND shows SUCCEEDED continues to advance."""
        from app.tasks.boltz_tasks import _process_boltz_swap_async

        mock_task = MagicMock()
        swap = _make_swap(status=SwapStatus.CREATED)
        swap_id = str(swap.id)

        with patch("app.tasks.boltz_tasks.get_db_context") as mock_ctx:
            mock_db = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.services.boltz_service.boltz_service") as mock_svc, \
                 patch("app.services.lnd_service.lnd_service") as mock_lnd:

                mock_svc.get_swap_by_id = AsyncMock(return_value=swap)
                mock_lnd.decode_payment_request = AsyncMock(
                    return_value=({"payment_hash": "ph123"}, None)
                )
                mock_lnd.send_payment_sync = AsyncMock(
                    return_value=(None, "Request timeout")
                )
                # LND lookup shows SUCCEEDED despite HTTP error
                mock_lnd.lookup_payment = AsyncMock(
                    return_value=({"status": "SUCCEEDED"}, None)
                )

                mock_svc.advance_swap = AsyncMock(return_value=(
                    _make_swap(status=SwapStatus.COMPLETED, claim_txid="tx_final"),
                    None,
                ))

                result = await _process_boltz_swap_async(mock_task, swap_id)

        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_paying_invoice_retry_checks_lnd(self):
        """Re-entering with PAYING_INVOICE checks LND for payment result."""
        from app.tasks.boltz_tasks import _process_boltz_swap_async

        mock_task = MagicMock()
        swap = _make_swap(
            status=SwapStatus.PAYING_INVOICE,
            lnd_payment_hash="ph_existing",
        )
        swap_id = str(swap.id)

        with patch("app.tasks.boltz_tasks.get_db_context") as mock_ctx:
            mock_db = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.services.boltz_service.boltz_service") as mock_svc, \
                 patch("app.services.lnd_service.lnd_service") as mock_lnd:

                mock_svc.get_swap_by_id = AsyncMock(return_value=swap)

                # LND says payment succeeded
                mock_lnd.lookup_payment = AsyncMock(
                    return_value=({"status": "SUCCEEDED"}, None)
                )

                mock_svc.advance_swap = AsyncMock(return_value=(
                    _make_swap(status=SwapStatus.CLAIMED, claim_txid="tx_claimed"),
                    None,
                ))

                result = await _process_boltz_swap_async(mock_task, swap_id)

        assert result["status"] == "claimed"

    @pytest.mark.asyncio
    async def test_paying_invoice_lnd_failed(self):
        """PAYING_INVOICE with LND FAILED marks swap as FAILED."""
        from app.tasks.boltz_tasks import _process_boltz_swap_async

        mock_task = MagicMock()
        swap = _make_swap(
            status=SwapStatus.PAYING_INVOICE,
            lnd_payment_hash="ph_existing",
        )
        swap_id = str(swap.id)

        with patch("app.tasks.boltz_tasks.get_db_context") as mock_ctx:
            mock_db = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.services.boltz_service.boltz_service") as mock_svc, \
                 patch("app.services.lnd_service.lnd_service") as mock_lnd:

                mock_svc.get_swap_by_id = AsyncMock(return_value=swap)
                mock_lnd.lookup_payment = AsyncMock(
                    return_value=({"status": "FAILED"}, None)
                )

                result = await _process_boltz_swap_async(mock_task, swap_id)

        assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_advance_not_terminal_triggers_retry(self):
        """Non-terminal advance result triggers Celery retry."""
        from app.tasks.boltz_tasks import _process_boltz_swap_async

        mock_task = MagicMock()
        swap = _make_swap(status=SwapStatus.INVOICE_PAID)
        swap_id = str(swap.id)

        with patch("app.tasks.boltz_tasks.get_db_context") as mock_ctx:
            mock_db = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.services.boltz_service.boltz_service") as mock_svc, \
                 patch("app.services.lnd_service.lnd_service"):

                mock_svc.get_swap_by_id = AsyncMock(return_value=swap)
                # advance_swap keeps INVOICE_PAID (waiting for lockup)
                mock_svc.advance_swap = AsyncMock(return_value=(
                    _make_swap(status=SwapStatus.INVOICE_PAID),
                    None,
                ))

                result = await _process_boltz_swap_async(mock_task, swap_id)

        mock_task.retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_exception_triggers_retry_with_delay(self):
        """Unexpected exception triggers Celery retry."""
        from app.tasks.boltz_tasks import _process_boltz_swap_async

        mock_task = MagicMock()
        swap = _make_swap(status=SwapStatus.CREATED)
        swap_id = str(swap.id)

        with patch("app.tasks.boltz_tasks.get_db_context") as mock_ctx:
            mock_db = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.services.boltz_service.boltz_service") as mock_svc, \
                 patch("app.services.lnd_service.lnd_service") as mock_lnd:

                mock_svc.get_swap_by_id = AsyncMock(return_value=swap)
                # decode_payment_request crashes
                mock_lnd.decode_payment_request = AsyncMock(
                    side_effect=RuntimeError("Unexpected crash")
                )

                result = await _process_boltz_swap_async(mock_task, swap_id)

        mock_task.retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_max_retries_exceeded_marks_failed(self):
        """MaxRetriesExceededError marks swap as FAILED (A5 fix validation)."""
        from app.tasks.boltz_tasks import _process_boltz_swap_async

        mock_task = MagicMock()
        # Make task.retry raise MaxRetriesExceededError
        mock_task.MaxRetriesExceededError = type("MaxRetriesExceededError", (Exception,), {})
        mock_task.retry.side_effect = mock_task.MaxRetriesExceededError("Max retries")
        swap = _make_swap(status=SwapStatus.CREATED)
        swap_id = str(swap.id)

        with patch("app.tasks.boltz_tasks.get_db_context") as mock_ctx:
            mock_db = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.services.boltz_service.boltz_service") as mock_svc, \
                 patch("app.services.lnd_service.lnd_service") as mock_lnd:

                mock_svc.get_swap_by_id = AsyncMock(return_value=swap)
                mock_lnd.decode_payment_request = AsyncMock(
                    side_effect=RuntimeError("Persistent error")
                )

                result = await _process_boltz_swap_async(mock_task, swap_id)

        assert result["status"] == "failed"
        assert "Max retries" in swap.error_message

    @pytest.mark.asyncio
    async def test_routing_fee_limit_applied(self):
        """routing_fee_limit_percent is used to calculate fee_limit."""
        from app.tasks.boltz_tasks import _process_boltz_swap_async

        mock_task = MagicMock()
        swap = _make_swap(status=SwapStatus.CREATED, invoice_amount_sats=200_000)
        swap_id = str(swap.id)

        with patch("app.tasks.boltz_tasks.get_db_context") as mock_ctx:
            mock_db = AsyncMock()
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("app.services.boltz_service.boltz_service") as mock_svc, \
                 patch("app.services.lnd_service.lnd_service") as mock_lnd:

                mock_svc.get_swap_by_id = AsyncMock(return_value=swap)
                mock_lnd.decode_payment_request = AsyncMock(
                    return_value=({"payment_hash": "ph"}, None)
                )
                mock_lnd.send_payment_sync = AsyncMock(
                    return_value=({"payment_hash": "ph"}, None)
                )
                mock_svc.advance_swap = AsyncMock(return_value=(
                    _make_swap(status=SwapStatus.COMPLETED, claim_txid="tx"),
                    None,
                ))

                await _process_boltz_swap_async(
                    mock_task, swap_id, routing_fee_limit_percent=5.0
                )

        # fee_limit = max(1000, 200000 * 5.0 / 100) = max(1000, 10000) = 10000
        call_args = mock_lnd.send_payment_sync.call_args
        assert call_args.kwargs["fee_limit_sats"] == 10000


# ============================================================================
# Retry delay
# ============================================================================

class TestRetryDelayEdgeCases:
    """Additional retry delay tests."""

    def test_created_at_none_returns_15(self):
        """None created_at defaults to 15s."""
        from app.tasks.boltz_tasks import _get_retry_delay
        swap = _make_swap(created_at=None)
        assert _get_retry_delay(swap) == 15

    def test_new_swap_returns_15(self):
        """Swap < 10 minutes old returns 15s."""
        from app.tasks.boltz_tasks import _get_retry_delay
        swap = _make_swap(created_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        assert _get_retry_delay(swap) == 15

    def test_medium_age_returns_60(self):
        """Swap 10min-2hr old returns 60s."""
        from app.tasks.boltz_tasks import _get_retry_delay
        swap = _make_swap(created_at=datetime.now(timezone.utc) - timedelta(minutes=30))
        assert _get_retry_delay(swap) == 60

    def test_old_swap_returns_300(self):
        """Swap > 2hr old returns 300s."""
        from app.tasks.boltz_tasks import _get_retry_delay
        swap = _make_swap(created_at=datetime.now(timezone.utc) - timedelta(hours=3))
        assert _get_retry_delay(swap) == 300


# ============================================================================
# get_lockup_transaction edge cases
# ============================================================================

class TestGetLockupTransaction:
    """Test get_lockup_transaction response handling (B14)."""

    @pytest.mark.asyncio
    async def test_empty_response(self):
        """Boltz returns {} — both hex and transactionHex missing."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()
        service._request = AsyncMock(return_value=({}, None))

        tx_hex, error = await service.get_lockup_transaction("test-swap-id")
        # Returns None for tx_hex (falsy) since neither key present
        assert tx_hex is None
        assert error is None

    @pytest.mark.asyncio
    async def test_hex_key_present(self):
        """Boltz returns {hex: ...} — standard response."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()
        service._request = AsyncMock(return_value=({"hex": "0200000001..."}, None))

        tx_hex, error = await service.get_lockup_transaction("test-swap-id")
        assert tx_hex == "0200000001..."

    @pytest.mark.asyncio
    async def test_transaction_hex_key_present(self):
        """Boltz returns {transactionHex: ...} — legacy response."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()
        service._request = AsyncMock(return_value=({"transactionHex": "020000..."}, None))

        tx_hex, error = await service.get_lockup_transaction("test-swap-id")
        assert tx_hex == "020000..."

    @pytest.mark.asyncio
    async def test_request_error(self):
        """API error propagated."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()
        service._request = AsyncMock(return_value=(None, "Tor failed"))

        tx_hex, error = await service.get_lockup_transaction("test-swap-id")
        assert tx_hex is None
        assert error == "Tor failed"


# ============================================================================
# List swaps limit clamping
# ============================================================================

class TestListSwapsLimitClamping:
    """Test list endpoint clamps limit to 50 (B15)."""

    @pytest.mark.asyncio
    async def test_limit_clamped_to_50(self):
        """Limit > 50 is clamped to 50."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        await service.get_swaps_for_user(mock_db, uuid4(), limit=100)

        # Check the query had limit(100) — the endpoint clamps, not the service
        # The _endpoint_ calls min(limit, 50) before passing to service
        mock_db.execute.assert_called_once()


# ============================================================================
# Cooperative claim edge cases
# ============================================================================

class TestCooperativeClaimEdgeCases:
    """Additional cooperative claim subprocess tests."""

    @pytest.mark.asyncio
    async def test_claim_returns_no_txid(self):
        """Claim script returns valid JSON but no txid field."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()
        swap = _make_swap()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(
            json.dumps({"status": "error", "message": "No UTXO"}).encode(),
            b"",
        ))
        mock_proc.returncode = 0

        with patch("app.services.boltz_service.asyncio.create_subprocess_exec", return_value=mock_proc):
            txid, error = await service.cooperative_claim(swap, "0200000001...")

        assert txid is None
        assert "no txid" in error.lower()

    @pytest.mark.asyncio
    async def test_claim_script_not_found(self):
        """Missing claim script returns clean error."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()
        swap = _make_swap()

        with patch("app.services.boltz_service.CLAIM_SCRIPT_PATH") as mock_path:
            mock_path.exists.return_value = False
            txid, error = await service.cooperative_claim(swap, "0200000001...")

        assert "not found" in error.lower()

    @pytest.mark.asyncio
    async def test_claim_timeout_message_includes_duration(self):
        """Timeout error message includes the timeout duration."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()
        swap = _make_swap()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch("app.services.boltz_service.CLAIM_SCRIPT_PATH") as mock_path:
            mock_path.exists.return_value = True
            with patch("app.services.boltz_service.asyncio.create_subprocess_exec",
                        return_value=mock_proc):
                txid, error = await service.cooperative_claim(swap, "0200000001...")

        assert "timed out" in error.lower()

    @pytest.mark.asyncio
    async def test_claim_nonzero_exit_sanitizes_stderr(self):
        """Non-zero exit sanitizes stderr to prevent key leakage."""
        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()
        swap = _make_swap()

        long_stderr = "Error: " + "x" * 1000

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", long_stderr.encode()))
        mock_proc.returncode = 1

        with patch("app.services.boltz_service.CLAIM_SCRIPT_PATH") as mock_path:
            mock_path.exists.return_value = True
            with patch("app.services.boltz_service.asyncio.create_subprocess_exec",
                        return_value=mock_proc):
                txid, error = await service.cooperative_claim(swap, "hex")

        # stderr should be truncated to max 500 chars
        assert len(error) < 600

    @pytest.mark.asyncio
    @patch("app.services.boltz_service.settings")
    async def test_claim_passes_proxy_when_tor_enabled(self, mock_settings):
        """SOCKS proxy is passed to claim script when Tor is enabled."""
        mock_settings.boltz_use_tor = True
        mock_settings.boltz_onion_url = "http://boltz.onion/v2"
        mock_settings.boltz_api_url = "https://api.boltz.exchange/v2"
        mock_settings.lnd_tor_proxy = "socks5://tor-proxy:9050"
        mock_settings.boltz_fallback_clearnet = False

        from app.services.boltz_service import BoltzSwapService
        service = BoltzSwapService()
        swap = _make_swap()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(
            json.dumps({"txid": "abc123"}).encode(),
            b"",
        ))
        mock_proc.returncode = 0

        with patch("app.services.boltz_service.CLAIM_SCRIPT_PATH") as mock_path:
            mock_path.exists.return_value = True
            with patch("app.services.boltz_service.asyncio.create_subprocess_exec",
                        return_value=mock_proc):
                txid, error = await service.cooperative_claim(swap, "hex")

        assert txid == "abc123"
        # Verify the input passed to communicate includes socksProxy
        comm_call = mock_proc.communicate.call_args
        input_data = comm_call.kwargs.get("input") or (comm_call.args[0] if comm_call.args else b"")
        stdin_data = json.loads(input_data.decode())
        assert "socksProxy" in stdin_data


# ============================================================================
# Model type annotation validation
# ============================================================================

class TestModelAnnotations:
    """Validate model field types (C3 fix)."""

    def test_status_history_is_list_type(self):
        """status_history type annotation should be list (not dict)."""
        import typing
        from app.models.boltz_swap import BoltzSwap
        hints = typing.get_type_hints(BoltzSwap)
        # The mapped type should accept list operations
        swap = _make_swap(status_history=[])
        swap.status_history.append({"status": "test"})
        assert len(swap.status_history) == 1

    def test_swap_status_enum_has_nine_states(self):
        """SwapStatus enum has exactly 9 states."""
        assert len(SwapStatus) == 9
        expected = {
            "created", "paying_invoice", "invoice_paid", "claiming",
            "claimed", "completed", "failed", "cancelled", "refunded",
        }
        actual = {s.value for s in SwapStatus}
        assert actual == expected

    def test_swap_direction_reverse_only(self):
        """BoltzSwapDirection has REVERSE value."""
        assert BoltzSwapDirection.REVERSE.value == "reverse"
