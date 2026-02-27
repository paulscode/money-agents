from pydantic import SecretStr
from pydantic import SecretStr
"""
Unit tests for LND Lightning Node Service.

Tests all LND REST API communication methods with mocked HTTP client.
Covers: authentication, TLS, Tor .onion detection, read-only operations,
payment operations (create invoice, send payment, send on-chain, fee estimates).
"""
import base64
import ssl
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from uuid import uuid4

import httpx
import pytest

from app.services.lnd_service import LNDService, _is_onion_url


# ============================================================================
# Helpers
# ============================================================================

def _make_lnd_service():
    """Create a fresh LNDService instance for testing.

    Sets ``_bake_attempted = True`` to skip the runtime macaroon
    baking step — tests that exercise baking should reset this.
    """
    service = LNDService()
    service._bake_attempted = True
    return service


def _mock_response(json_data: dict, status_code: int = 200):
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = str(json_data)
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ============================================================================
# Tor .onion URL detection
# ============================================================================

class TestOnionDetection:
    """Tests for _is_onion_url helper."""

    def test_regular_url(self):
        """Regular HTTPS URL is not .onion."""
        assert _is_onion_url("https://mynode.local:8080") is False

    def test_onion_url(self):
        """.onion URL is detected."""
        assert _is_onion_url("https://abc123def456.onion:8080") is True

    def test_empty_url(self):
        """Empty string is not .onion."""
        assert _is_onion_url("") is False

    def test_ip_url(self):
        """IP address is not .onion."""
        assert _is_onion_url("https://192.168.1.100:8080") is False

    def test_malformed_url(self):
        """Malformed URL returns False."""
        assert _is_onion_url("not a url") is False


# ============================================================================
# LNDService — Authentication and headers
# ============================================================================

class TestLNDServiceAuth:
    """Tests for LND authentication header construction."""

    @patch("app.services.lnd_service.settings")
    def test_get_headers_with_macaroon(self, mock_settings):
        """Headers include macaroon when configured."""
        mock_settings.lnd_macaroon_hex = SecretStr("abcdef1234567890")
        service = _make_lnd_service()
        headers = service._get_auth_headers()
        assert headers["Grpc-Metadata-macaroon"] == "abcdef1234567890"

    @patch("app.services.lnd_service.settings")
    def test_get_headers_without_macaroon(self, mock_settings):
        """Headers are empty when no macaroon configured."""
        mock_settings.lnd_macaroon_hex = SecretStr("")
        service = _make_lnd_service()
        headers = service._get_auth_headers()
        assert "Grpc-Metadata-macaroon" not in headers

    @patch("app.services.lnd_service.settings")
    def test_ssl_context_with_cert(self, mock_settings):
        """SSL context is created when TLS cert is provided."""
        # Generate a self-signed cert stand-in (just base64 text)
        mock_settings.lnd_tls_cert = base64.b64encode(b"dummy cert data").decode()
        service = _make_lnd_service()
        # The method will try to load the cert — it'll fail on invalid data but shouldn't crash
        ctx = service._get_ssl_context()
        # Since the cert data is invalid, it should return None (warning logged)
        assert ctx is None

    @patch("app.services.lnd_service.settings")
    def test_ssl_context_without_cert(self, mock_settings):
        """No SSL context when no cert configured."""
        mock_settings.lnd_tls_cert = ""
        service = _make_lnd_service()
        ctx = service._get_ssl_context()
        assert ctx is None

    @patch("app.services.lnd_service.settings")
    def test_tor_proxy_for_onion(self, mock_settings):
        """Tor proxy returned for .onion URLs."""
        mock_settings.lnd_rest_url = "https://abc123.onion:8080"
        mock_settings.lnd_tor_proxy = "socks5://tor-proxy:9050"
        service = _make_lnd_service()
        proxy = service._get_tor_proxy()
        assert proxy == "socks5://tor-proxy:9050"

    @patch("app.services.lnd_service.settings")
    def test_no_tor_proxy_for_clearnet(self, mock_settings):
        """No Tor proxy for clearnet URLs."""
        mock_settings.lnd_rest_url = "https://mynode:8080"
        mock_settings.lnd_tor_proxy = "socks5://tor-proxy:9050"
        service = _make_lnd_service()
        proxy = service._get_tor_proxy()
        assert proxy is None


# ============================================================================
# LNDService — Scoped Macaroon Baking
# ============================================================================

class TestMacaroonBaking:
    """Tests for least-privilege macaroon baking."""

    @patch("app.services.lnd_service.settings")
    def test_scoped_headers_readonly(self, mock_settings):
        """Read-only scope returns the baked readonly macaroon."""
        mock_settings.lnd_macaroon_hex = SecretStr("admin_mac")
        service = _make_lnd_service()
        service._readonly_macaroon_hex = "readonly_mac"
        service._write_macaroon_hex = "write_mac"

        headers = service._get_auth_headers("readonly")
        assert headers["Grpc-Metadata-macaroon"] == "readonly_mac"

    @patch("app.services.lnd_service.settings")
    def test_scoped_headers_write(self, mock_settings):
        """Write scope returns the baked write macaroon."""
        mock_settings.lnd_macaroon_hex = SecretStr("admin_mac")
        service = _make_lnd_service()
        service._readonly_macaroon_hex = "readonly_mac"
        service._write_macaroon_hex = "write_mac"

        headers = service._get_auth_headers("write")
        assert headers["Grpc-Metadata-macaroon"] == "write_mac"

    @patch("app.services.lnd_service.settings")
    def test_scoped_headers_fallback_to_admin(self, mock_settings):
        """Falls back to admin macaroon when baking hasn't occurred."""
        mock_settings.lnd_macaroon_hex = SecretStr("admin_mac")
        service = _make_lnd_service()
        # No baked macaroons set

        headers = service._get_auth_headers("readonly")
        assert headers["Grpc-Metadata-macaroon"] == "admin_mac"

        headers = service._get_auth_headers("write")
        assert headers["Grpc-Metadata-macaroon"] == "admin_mac"

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_bake_success(self, mock_settings):
        """Successful bake stores scoped macaroons."""
        mock_settings.lnd_macaroon_hex = SecretStr("admin_mac_hex")
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = LNDService()  # Fresh — _bake_attempted is False
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False

        # First call returns readonly mac, second returns write mac
        mock_client.request.side_effect = [
            _mock_response({"macaroon": "baked_readonly_hex"}),
            _mock_response({"macaroon": "baked_write_hex"}),
        ]
        service._client = mock_client

        await service._bake_scoped_macaroons()

        assert service._bake_attempted is True
        assert service._readonly_macaroon_hex == "baked_readonly_hex"
        assert service._write_macaroon_hex == "baked_write_hex"

        # Verify two POST /v1/macaroon calls were made
        assert mock_client.request.call_count == 2
        calls = mock_client.request.call_args_list
        assert calls[0][0] == ("POST", "/v1/macaroon")
        assert calls[1][0] == ("POST", "/v1/macaroon")
        # Admin macaroon was used for baking
        assert calls[0][1]["headers"]["Grpc-Metadata-macaroon"] == "admin_mac_hex"

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_bake_failure_falls_back(self, mock_settings):
        """Failed bake sets _bake_attempted but leaves scoped macs as None."""
        mock_settings.lnd_macaroon_hex = SecretStr("admin_mac_hex")
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = LNDService()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.side_effect = Exception("Unavailable")
        service._client = mock_client

        await service._bake_scoped_macaroons()

        assert service._bake_attempted is True
        assert service._readonly_macaroon_hex is None
        assert service._write_macaroon_hex is None

        # Fallback: admin macaroon still used
        headers = service._get_auth_headers("readonly")
        assert headers["Grpc-Metadata-macaroon"] == "admin_mac_hex"

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_bake_idempotent(self, mock_settings):
        """Second bake call is a no-op (lock + flag)."""
        mock_settings.lnd_macaroon_hex = SecretStr("admin_mac_hex")
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = LNDService()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.side_effect = [
            _mock_response({"macaroon": "ro_mac"}),
            _mock_response({"macaroon": "wr_mac"}),
        ]
        service._client = mock_client

        await service._bake_scoped_macaroons()
        await service._bake_scoped_macaroons()  # Should be no-op

        # Only 2 calls (the initial bake), not 4
        assert mock_client.request.call_count == 2

    @patch("app.services.lnd_service.settings")
    def test_sanitize_error_strips_scoped_macaroons(self, mock_settings):
        """_sanitize_error strips both admin and baked macaroons."""
        mock_settings.lnd_macaroon_hex = SecretStr("admin_hex")
        service = _make_lnd_service()
        service._readonly_macaroon_hex = "readonly_hex"
        service._write_macaroon_hex = "write_hex"

        error = "Error with admin_hex and readonly_hex and write_hex"
        sanitized = service._sanitize_error(error)
        assert "admin_hex" not in sanitized
        assert "readonly_hex" not in sanitized
        assert "write_hex" not in sanitized
        assert "[REDACTED]" in sanitized

    @patch("app.services.lnd_service.settings")
    def test_tor_proxy_missing_warns(self, mock_settings):
        """Warning when .onion URL but no proxy configured."""
        mock_settings.lnd_rest_url = "https://abc123.onion:8080"
        mock_settings.lnd_tor_proxy = ""
        service = _make_lnd_service()
        proxy = service._get_tor_proxy()
        assert proxy is None


# ============================================================================
# LNDService — Read-only operations
# ============================================================================

class TestLNDServiceReadOps:
    """Tests for LND read-only API operations."""

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_get_info_success(self, mock_settings):
        """Node info is correctly parsed from LND response."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test_macaroon")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response({
            "alias": "mynode",
            "identity_pubkey": "02abcdef",
            "num_active_channels": 5,
            "num_inactive_channels": 1,
            "num_pending_channels": 0,
            "num_peers": 10,
            "block_height": 820000,
            "synced_to_chain": True,
            "synced_to_graph": True,
            "version": "0.17.0",
            "commit_hash": "abc123",
            "uris": ["02abc@127.0.0.1:9735"],
        })
        service._client = mock_client

        result = await service.get_info()
        assert result is not None
        assert result["alias"] == "mynode"
        assert result["identity_pubkey"] == "02abcdef"
        assert result["num_active_channels"] == 5
        assert result["synced_to_chain"] is True
        assert result["block_height"] == 820000

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_get_info_connection_error(self, mock_settings):
        """Returns None on connection error."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.side_effect = httpx.ConnectError("refused")
        service._client = mock_client

        result = await service.get_info()
        assert result is None

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_get_wallet_balance(self, mock_settings):
        """Wallet balance is parsed correctly."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response({
            "total_balance": "500000",
            "confirmed_balance": "450000",
            "unconfirmed_balance": "50000",
            "locked_balance": "0",
            "reserved_balance_anchor_chan": "10000",
        })
        service._client = mock_client

        result = await service.get_wallet_balance()
        assert result is not None
        assert result["total_balance"] == 500000
        assert result["confirmed_balance"] == 450000
        assert result["unconfirmed_balance"] == 50000

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_get_channel_balance(self, mock_settings):
        """Channel balance with nested sat objects is parsed correctly."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response({
            "local_balance": {"sat": "300000", "msat": "300000000"},
            "remote_balance": {"sat": "200000", "msat": "200000000"},
            "pending_open_local_balance": {},
            "pending_open_remote_balance": {},
            "unsettled_local_balance": {"sat": "5000"},
            "unsettled_remote_balance": {},
        })
        service._client = mock_client

        result = await service.get_channel_balance()
        assert result is not None
        assert result["local_balance_sat"] == 300000
        assert result["remote_balance_sat"] == 200000
        assert result["unsettled_local_sat"] == 5000

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_get_channels(self, mock_settings):
        """Channel list is parsed with all fields."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response({
            "channels": [
                {
                    "chan_id": "123456",
                    "remote_pubkey": "02def",
                    "capacity": "1000000",
                    "local_balance": "600000",
                    "remote_balance": "400000",
                    "active": True,
                    "private": False,
                    "peer_alias": "ACINQ",
                },
            ]
        })
        service._client = mock_client

        result = await service.get_channels()
        assert result is not None
        assert len(result) == 1
        assert result[0]["chan_id"] == "123456"
        assert result[0]["capacity"] == 1000000
        assert result[0]["active"] is True
        assert result[0]["peer_alias"] == "ACINQ"

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_get_pending_channels(self, mock_settings):
        """Pending channels counts are extracted."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response({
            "pending_open_channels": [{"channel": {}}],
            "pending_closing_channels": [],
            "pending_force_closing_channels": [],
            "waiting_close_channels": [],
            "total_limbo_balance": "25000",
        })
        service._client = mock_client

        result = await service.get_pending_channels()
        assert result["pending_open_channels"] == 1
        assert result["total_limbo_balance"] == 25000

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_get_recent_payments(self, mock_settings):
        """Recent payments are parsed with status and fees."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response({
            "payments": [
                {
                    "payment_hash": "abc123",
                    "value_sat": "10000",
                    "fee_sat": "5",
                    "status": "SUCCEEDED",
                    "creation_date": "1700000000",
                },
            ]
        })
        service._client = mock_client

        result = await service.get_recent_payments(max_payments=5)
        assert len(result) == 1
        assert result[0]["value_sat"] == 10000
        assert result[0]["fee_sat"] == 5
        assert result[0]["status"] == "SUCCEEDED"

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_get_recent_invoices(self, mock_settings):
        """Recent invoices are parsed correctly."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response({
            "invoices": [
                {
                    "memo": "Test invoice",
                    "r_hash": "hash123",
                    "value": "5000",
                    "settled": True,
                    "creation_date": "1700000000",
                    "settle_date": "1700000100",
                    "amt_paid_sat": "5000",
                    "state": "SETTLED",
                    "is_keysend": False,
                    "payment_request": "lnbc50u1...",
                },
            ]
        })
        service._client = mock_client

        result = await service.get_recent_invoices(num_max_invoices=5)
        assert len(result) == 1
        assert result[0]["memo"] == "Test invoice"
        assert result[0]["settled"] is True
        assert result[0]["amt_paid_sat"] == 5000

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_get_onchain_transactions(self, mock_settings):
        """On-chain transactions are parsed with correct types."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response({
            "transactions": [
                {
                    "tx_hash": "tx_abc",
                    "amount": "-50000",
                    "num_confirmations": "6",
                    "block_height": "820001",
                    "time_stamp": "1700000000",
                    "total_fees": "250",
                    "label": "withdrawal",
                },
            ]
        })
        service._client = mock_client

        result = await service.get_onchain_transactions(max_txns=5)
        assert len(result) == 1
        assert result[0]["tx_hash"] == "tx_abc"
        assert result[0]["amount"] == -50000
        assert result[0]["total_fees"] == 250

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_get_wallet_summary(self, mock_settings):
        """Wallet summary fetches info, balance, channels in parallel."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()

        # Mock all the sub-methods
        service.get_info = AsyncMock(return_value={
            "alias": "testnode",
            "num_active_channels": 3,
            "num_pending_channels": 0,
            "synced_to_chain": True,
        })
        service.get_wallet_balance = AsyncMock(return_value={
            "confirmed_balance": 100000,
            "unconfirmed_balance": 5000,
        })
        service.get_channel_balance = AsyncMock(return_value={
            "local_balance_sat": 200000,
            "remote_balance_sat": 150000,
        })
        service.get_pending_channels = AsyncMock(return_value={
            "pending_open_channels": 0,
        })

        result = await service.get_wallet_summary()
        assert result is not None
        assert result["connected"] is True
        assert result["totals"]["total_balance_sats"] == 300000  # 100000 + 200000
        assert result["totals"]["onchain_sats"] == 100000
        assert result["totals"]["lightning_local_sats"] == 200000

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_get_wallet_summary_all_none(self, mock_settings):
        """Summary returns None when all sub-calls fail."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        service.get_info = AsyncMock(return_value=None)
        service.get_wallet_balance = AsyncMock(return_value=None)
        service.get_channel_balance = AsyncMock(return_value=None)
        service.get_pending_channels = AsyncMock(return_value=None)

        result = await service.get_wallet_summary()
        assert result is None


# ============================================================================
# LNDService — Payment operations
# ============================================================================

class TestLNDServicePaymentOps:
    """Tests for LND payment write operations."""

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_create_invoice_success(self, mock_settings):
        """Invoice creation returns r_hash and payment_request."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False

        # r_hash comes as base64 from LND REST
        r_hash_bytes = bytes.fromhex("deadbeef")
        r_hash_b64 = base64.b64encode(r_hash_bytes).decode()

        mock_client.request.return_value = _mock_response({
            "r_hash": r_hash_b64,
            "payment_request": "lnbc10u1p...",
            "add_index": "42",
        })
        service._client = mock_client

        data, error = await service.create_invoice(amount_sats=1000, memo="test")
        assert error is None
        assert data["r_hash"] == "deadbeef"
        assert data["payment_request"] == "lnbc10u1p..."

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_create_invoice_error(self, mock_settings):
        """Invoice creation returns error on LND failure."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response(
            {"message": "wallet locked"}, status_code=500
        )
        service._client = mock_client

        data, error = await service.create_invoice(amount_sats=1000)
        assert data is None
        assert "wallet locked" in error

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_decode_payment_request(self, mock_settings):
        """Payment request is decoded with all fields."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response({
            "destination": "02abcdef",
            "payment_hash": "hash123",
            "num_satoshis": "50000",
            "timestamp": "1700000000",
            "expiry": "3600",
            "description": "Test payment",
            "cltv_expiry": "40",
            "num_msat": "50000000",
            "features": {},
        })
        service._client = mock_client

        data, error = await service.decode_payment_request("lnbc50u1p...")
        assert error is None
        assert data["num_satoshis"] == 50000
        assert data["description"] == "Test payment"

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_send_payment_sync_success(self, mock_settings):
        """Successful payment returns hash and route details."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False

        p_hash = base64.b64encode(bytes.fromhex("aabbccdd")).decode()
        preimage = base64.b64encode(bytes.fromhex("11223344")).decode()

        mock_client.request.return_value = _mock_response({
            "payment_hash": p_hash,
            "payment_preimage": preimage,
            "payment_error": "",
            "payment_route": {
                "total_amt": "50000",
                "total_fees": "5",
                "total_amt_msat": "50000000",
                "total_fees_msat": "5000",
                "hops": [{"chan_id": "123"}],
            },
        })
        service._client = mock_client

        data, error = await service.send_payment_sync(
            payment_request="lnbc50u1p...", fee_limit_sats=100
        )
        assert error is None
        assert data["payment_hash"] == "aabbccdd"
        assert data["payment_preimage"] == "11223344"
        assert data["payment_route"]["total_fees"] == 5
        assert data["payment_route"]["hops"] == 1

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_send_payment_sync_payment_error(self, mock_settings):
        """Payment-level error (200 with payment_error) is returned."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response({
            "payment_hash": "",
            "payment_error": "insufficient_balance",
            "payment_route": None,
        })
        service._client = mock_client

        data, error = await service.send_payment_sync(payment_request="lnbc50u1p...")
        assert data is None
        assert "insufficient_balance" in error

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_send_coins_success(self, mock_settings):
        """On-chain send returns txid."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response({
            "txid": "tx_abcdef1234567890",
        })
        service._client = mock_client

        data, error = await service.send_coins(
            address="bc1qtest", amount_sats=100000, sat_per_vbyte=5
        )
        assert error is None
        assert data["txid"] == "tx_abcdef1234567890"

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_estimate_fee(self, mock_settings):
        """Fee estimate returns sat_per_vbyte and total fee."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response({
            "fee_sat": "560",
            "feerate_sat_per_byte": "4",
            "sat_per_vbyte": "4",
        })
        service._client = mock_client

        data, error = await service.estimate_fee(
            address="bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq", amount_sats=100000, target_conf=6
        )
        assert error is None
        assert data["fee_sat"] == 560
        assert data["sat_per_vbyte"] == 4

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_lookup_invoice(self, mock_settings):
        """Invoice lookup returns settlement status."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response({
            "memo": "test invoice",
            "r_hash": "hash123",
            "value": "5000",
            "settled": True,
            "creation_date": "1700000000",
            "settle_date": "1700000100",
            "amt_paid_sat": "5000",
            "state": "SETTLED",
            "payment_request": "lnbc50u1...",
            "is_keysend": False,
        })
        service._client = mock_client

        data, error = await service.lookup_invoice("abcdef0123456789")
        assert error is None
        assert data["settled"] is True
        assert data["value"] == 5000
        assert data["state"] == "SETTLED"

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_close_client(self, mock_settings):
        """Client is properly closed."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        service._client = mock_client

        await service.close()
        mock_client.aclose.assert_called_once()
        assert service._client is None


# ============================================================================
# LNDService — Channel Management
# ============================================================================

class TestLNDServiceChannelOps:
    """Tests for LND channel management operations."""

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_connect_peer_success(self, mock_settings):
        """Peer connection succeeds."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response({})
        service._client = mock_client

        data, error = await service.connect_peer(
            pubkey="03" + "ab" * 32,
            host="127.0.0.1:9735",
        )
        assert error is None
        assert data == {}
        mock_client.request.assert_called_once()
        call_args = mock_client.request.call_args
        assert call_args[0] == ("POST", "/v1/peers")

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_connect_peer_already_connected(self, mock_settings):
        """Already connected peer is not treated as error."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        # LND returns HTTP error when peer is already connected
        error_resp = MagicMock(spec=httpx.Response)
        error_resp.status_code = 500
        error_resp.text = "already connected to peer"
        error_resp.json.return_value = {"message": "already connected to peer"}
        error_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=error_resp
        )
        mock_client.request.return_value = error_resp
        service._client = mock_client

        data, error = await service.connect_peer(
            pubkey="03" + "ab" * 32,
            host="127.0.0.1:9735",
        )
        assert error is None
        assert data == {}

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_connect_peer_error(self, mock_settings):
        """Connection error is propagated."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.side_effect = httpx.ConnectError("refused")
        service._client = mock_client

        data, error = await service.connect_peer(
            pubkey="03" + "ab" * 32,
            host="127.0.0.1:9735",
        )
        assert data is None
        assert "refused" in error

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_open_channel_success(self, mock_settings):
        """Channel open returns funding txid."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False

        # LND returns funding_txid_bytes as base64-encoded reversed bytes
        txid_hex = "a1b2c3d4e5f6a7b8" + "00" * 24
        txid_bytes = bytes.fromhex(txid_hex)
        txid_b64 = base64.b64encode(txid_bytes[::-1]).decode()

        mock_client.request.return_value = _mock_response({
            "funding_txid_bytes": txid_b64,
            "output_index": 0,
        })
        service._client = mock_client

        data, error = await service.open_channel(
            node_pubkey_hex="03" + "ab" * 32,
            local_funding_amount=500000,
            sat_per_vbyte=10,
        )
        assert error is None
        assert data["funding_txid"] == txid_hex
        assert data["output_index"] == 0

        # Verify the request body
        call_args = mock_client.request.call_args
        assert call_args[0] == ("POST", "/v1/channels")
        body = call_args[1]["json"]
        assert body["local_funding_amount"] == "500000"
        assert body["sat_per_vbyte"] == "10"

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_open_channel_error(self, mock_settings):
        """Channel open error is returned."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response(
            {"message": "not enough funds"}, status_code=500
        )
        service._client = mock_client

        data, error = await service.open_channel(
            node_pubkey_hex="03" + "ab" * 32,
            local_funding_amount=99999999,
        )
        assert data is None
        assert "not enough funds" in error

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_open_channel_no_fee_rate(self, mock_settings):
        """Channel open without explicit fee rate omits sat_per_vbyte."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False

        txid_bytes = bytes(32)
        txid_b64 = base64.b64encode(txid_bytes).decode()
        mock_client.request.return_value = _mock_response({
            "funding_txid_bytes": txid_b64,
            "output_index": 0,
        })
        service._client = mock_client

        data, error = await service.open_channel(
            node_pubkey_hex="03" + "ab" * 32,
            local_funding_amount=1000000,
        )
        assert error is None
        body = mock_client.request.call_args[1]["json"]
        assert "sat_per_vbyte" not in body

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_get_pending_channels_detail_success(self, mock_settings):
        """Pending channels detail returns structured list."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response({
            "pending_open_channels": [
                {
                    "channel": {
                        "remote_node_pub": "03" + "ab" * 32,
                        "channel_point": "abc123:0",
                        "capacity": "1000000",
                        "local_balance": "1000000",
                        "remote_balance": "0",
                    },
                    "commit_fee": "500",
                    "confirmation_height": 0,
                },
            ],
            "pending_closing_channels": [],
            "pending_force_closing_channels": [
                {
                    "channel": {
                        "remote_node_pub": "02" + "cd" * 32,
                        "channel_point": "def456:1",
                        "capacity": "500000",
                        "local_balance": "250000",
                        "remote_balance": "250000",
                    },
                    "closing_txid": "tx789",
                    "blocks_til_maturity": 144,
                },
            ],
            "waiting_close_channels": [],
        })
        service._client = mock_client

        result = await service.get_pending_channels_detail()
        assert result is not None
        assert len(result) == 2

        # Pending open channel
        assert result[0]["type"] == "pending_open"
        assert result[0]["capacity"] == 1000000
        assert result[0]["commit_fee"] == 500
        assert result[0]["remote_node_pub"] == "03" + "ab" * 32

        # Force closing channel
        assert result[1]["type"] == "force_closing"
        assert result[1]["capacity"] == 500000
        assert result[1]["closing_txid"] == "tx789"
        assert result[1]["blocks_til_maturity"] == 144

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_get_pending_channels_detail_empty(self, mock_settings):
        """Empty pending channels returns empty list."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.return_value = _mock_response({
            "pending_open_channels": [],
            "pending_closing_channels": [],
            "pending_force_closing_channels": [],
            "waiting_close_channels": [],
        })
        service._client = mock_client

        result = await service.get_pending_channels_detail()
        assert result == []

    @pytest.mark.asyncio
    @patch("app.services.lnd_service.settings")
    async def test_get_pending_channels_detail_connection_error(self, mock_settings):
        """Returns None on connection failure."""
        mock_settings.lnd_rest_url = "https://localhost:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("test")
        mock_settings.lnd_tls_verify = False
        mock_settings.lnd_tls_cert = ""
        mock_settings.lnd_tor_proxy = ""

        service = _make_lnd_service()
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.request.side_effect = httpx.ConnectError("refused")
        service._client = mock_client

        result = await service.get_pending_channels_detail()
        assert result is None
