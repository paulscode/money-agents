from pydantic import SecretStr
from pydantic import SecretStr
"""
Unit tests for Wallet API endpoints.

Tests the FastAPI wallet routes using the async client fixture with mocked LND service.
Covers: config, summary, balance, channels, payments, invoices, transactions,
invoice creation, payment sending, on-chain sending, fee estimation, and the
require_lnd dependency guard.
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4


# ============================================================================
# require_lnd dependency — LND disabled
# ============================================================================

class TestRequireLndGuard:
    """Tests that endpoints return 404 when LND is disabled."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.settings")
    async def test_wallet_summary_lnd_disabled(self, mock_settings, async_client, test_user):
        """Wallet summary returns 404 when USE_LND is false."""
        mock_settings.use_lnd = False

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/summary",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 404
        assert "not enabled" in response.json()["detail"]

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.settings")
    async def test_wallet_config_always_accessible(self, mock_settings, async_client, test_user):
        """Wallet config is accessible even when LND is disabled."""
        mock_settings.use_lnd = False
        mock_settings.lnd_rest_url = ""
        mock_settings.lnd_macaroon_hex = SecretStr("")
        mock_settings.lnd_mempool_url = "https://mempool.space"

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/config",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False


# ============================================================================
# Wallet config
# ============================================================================

class TestWalletConfig:
    """Tests for GET /wallet/config."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.settings")
    async def test_config_fully_configured(self, mock_settings, async_client, test_admin_user):
        """Config shows fully configured when all LND settings present (admin view)."""
        mock_settings.use_lnd = True
        mock_settings.lnd_rest_url = "https://mynode:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("abc123")
        mock_settings.lnd_mempool_url = "https://mempool.space"
        mock_settings.lnd_max_payment_sats = 10000

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/config",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["rest_url_configured"] is True
        assert data["macaroon_configured"] is True
        assert data["mempool_url"] == "https://mempool.space"
        assert data["max_payment_sats"] == 10000

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.settings")
    async def test_config_non_admin_reduced(self, mock_settings, async_client, test_user):
        """Non-admin users see only enabled + mempool_url (GAP-6)."""
        mock_settings.use_lnd = True
        mock_settings.lnd_rest_url = "https://mynode:8080"
        mock_settings.lnd_macaroon_hex = SecretStr("abc123")
        mock_settings.lnd_mempool_url = "https://mempool.space"
        mock_settings.lnd_max_payment_sats = 10000

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/config",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["mempool_url"] == "https://mempool.space"
        assert "rest_url_configured" not in data
        assert "macaroon_configured" not in data
        assert "max_payment_sats" not in data


# ============================================================================
# Safety limit endpoints
# ============================================================================

class TestSafetyLimit:
    """Tests for GET/PUT /wallet/safety-limit."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet._get_safety_redis", return_value=None)
    @patch("app.api.endpoints.wallet.settings")
    async def test_get_safety_limit(self, mock_settings, _mock_redis, async_client, test_user):
        """Returns current max payment sats."""
        mock_settings.use_lnd = True
        mock_settings.lnd_max_payment_sats = 10000

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/safety-limit",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json()["max_payment_sats"] == 10000

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.settings")
    async def test_update_safety_limit(self, mock_settings, async_client, test_admin_user):
        """Updates max payment sats in-memory (admin-only)."""
        mock_settings.use_lnd = True
        mock_settings.lnd_max_payment_sats = 10000

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.put(
            "/api/v1/wallet/safety-limit",
            json={"max_payment_sats": 50000},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json()["max_payment_sats"] == 50000
        assert mock_settings.lnd_max_payment_sats == 50000

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.settings")
    async def test_update_safety_limit_no_limit(self, mock_settings, async_client, test_admin_user):
        """Setting to -1 means no limit (admin-only)."""
        mock_settings.use_lnd = True
        mock_settings.lnd_max_payment_sats = 10000

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.put(
            "/api/v1/wallet/safety-limit",
            json={"max_payment_sats": -1},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json()["max_payment_sats"] == -1

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.settings")
    async def test_update_safety_limit_negative_rejected(self, mock_settings, async_client, test_admin_user):
        """Values below -1 are rejected (admin-only)."""
        mock_settings.use_lnd = True
        mock_settings.lnd_max_payment_sats = 10000

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.put(
            "/api/v1/wallet/safety-limit",
            json={"max_payment_sats": -100},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.settings")
    async def test_update_safety_limit_zero_require_approval(self, mock_settings, async_client, test_admin_user):
        """Setting 0 means all transactions require approval (admin-only)."""
        mock_settings.use_lnd = True
        mock_settings.lnd_max_payment_sats = 10000

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.put(
            "/api/v1/wallet/safety-limit",
            json={"max_payment_sats": 0},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json()["max_payment_sats"] == 0
        assert mock_settings.lnd_max_payment_sats == 0

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.settings")
    async def test_safety_limit_lnd_disabled(self, mock_settings, async_client, test_user):
        """Safety limit endpoints require LND enabled."""
        mock_settings.use_lnd = False

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/safety-limit",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 404


# ============================================================================
# Read-only wallet endpoints
# ============================================================================

class TestWalletReadEndpoints:
    """Tests for read-only wallet endpoints."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.lnd_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_get_summary_success(self, mock_settings, mock_lnd, async_client, test_user):
        """Wallet summary returns combined data."""
        mock_settings.use_lnd = True
        mock_lnd.get_wallet_summary = AsyncMock(return_value={
            "connected": True,
            "totals": {"total_balance_sats": 500000},
        })

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/summary",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json()["connected"] is True

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.lnd_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_get_summary_lnd_down(self, mock_settings, mock_lnd, async_client, test_user):
        """503 when LND node is unreachable."""
        mock_settings.use_lnd = True
        mock_lnd.get_wallet_summary = AsyncMock(return_value=None)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/summary",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 503

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.lnd_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_get_node_info(self, mock_settings, mock_lnd, async_client, test_user):
        """Node info endpoint returns alias and sync status."""
        mock_settings.use_lnd = True
        mock_lnd.get_info = AsyncMock(return_value={
            "alias": "testnode",
            "synced_to_chain": True,
        })

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/info",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json()["alias"] == "testnode"

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.lnd_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_get_channels(self, mock_settings, mock_lnd, async_client, test_user):
        """Channels endpoint returns channel list."""
        mock_settings.use_lnd = True
        mock_lnd.get_channels = AsyncMock(return_value=[
            {"chan_id": "123", "capacity": 500000, "active": True},
        ])

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/channels",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["channels"]) == 1
        assert data["channels"][0]["active"] is True

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.lnd_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_get_payments(self, mock_settings, mock_lnd, async_client, test_user):
        """Payments endpoint returns payment list."""
        mock_settings.use_lnd = True
        mock_lnd.get_recent_payments = AsyncMock(return_value=[
            {"payment_hash": "abc", "value_sat": 5000, "status": "SUCCEEDED"},
        ])

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/payments",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json()["payments"][0]["value_sat"] == 5000

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.lnd_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_get_invoices(self, mock_settings, mock_lnd, async_client, test_user):
        """Invoices endpoint returns invoice list."""
        mock_settings.use_lnd = True
        mock_lnd.get_recent_invoices = AsyncMock(return_value=[
            {"memo": "test", "settled": True, "value": 1000},
        ])

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/invoices",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json()["invoices"][0]["settled"] is True

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.lnd_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_get_transactions(self, mock_settings, mock_lnd, async_client, test_user):
        """Transactions endpoint returns on-chain tx list."""
        mock_settings.use_lnd = True
        mock_lnd.get_onchain_transactions = AsyncMock(return_value=[
            {"tx_hash": "tx_abc", "amount": -50000, "num_confirmations": 6},
        ])

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/transactions",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        txns = response.json()["transactions"]
        assert len(txns) == 1
        assert txns[0]["tx_hash"] == "tx_abc"


# ============================================================================
# Fee estimation endpoint
# ============================================================================

class TestFeeEstimation:
    """Tests for GET /wallet/fees."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.mempool_fee_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_get_fees_success(self, mock_settings, mock_mempool, async_client, test_user):
        """Fee estimates return priority breakdown."""
        mock_settings.use_lnd = True
        mock_settings.lnd_mempool_url = "https://mempool.space"
        mock_mempool.get_recommended_fees = AsyncMock(return_value={
            "fastestFee": 25,
            "halfHourFee": 15,
            "hourFee": 8,
            "economyFee": 4,
            "minimumFee": 1,
        })

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/fees",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["priorities"]["high"]["sat_per_vbyte"] == 25
        assert data["priorities"]["medium"]["sat_per_vbyte"] == 15
        assert data["priorities"]["low"]["sat_per_vbyte"] == 8

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.mempool_fee_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_get_fees_mempool_down(self, mock_settings, mock_mempool, async_client, test_user):
        """Returns unavailable flag when Mempool Explorer is unreachable."""
        mock_settings.use_lnd = True
        mock_mempool.get_recommended_fees = AsyncMock(return_value=None)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/fees",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["unavailable"] is True
        assert data["priorities"] is None


# ============================================================================
# Payment operations — max payment safety limit
# ============================================================================

class TestMaxPaymentLimit:
    """Tests for the global max payment safety limit enforcement."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.lnd_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_send_onchain_enforces_safety_limit(self, mock_settings, mock_lnd, async_client, test_admin_user):
        """On-chain send must reject amounts exceeding the safety limit."""
        mock_settings.use_lnd = True
        mock_settings.lnd_max_payment_sats = 100000

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/send",
            json={
                "address": "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
                "amount_sats": 200000,
            },
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 400
        assert "safety limit" in response.json()["detail"].lower()
        mock_lnd.send_coins.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.lnd_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_send_payment_enforces_safety_limit(self, mock_settings, mock_lnd, async_client, test_admin_user):
        """Lightning payment must reject amounts exceeding the safety limit."""
        mock_settings.use_lnd = True
        mock_settings.lnd_max_payment_sats = 10000

        # Mock decode to return an amount over the limit
        mock_lnd.decode_payment_request = AsyncMock(return_value=(
            {"num_satoshis": "50000"},
            None
        ))

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/payments/send",
            json={"payment_request": "lnbc50u1p..."},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 400
        assert "safety limit" in response.json()["detail"].lower()
        mock_lnd.send_payment_sync.assert_not_called()


# ============================================================================
# Authentication required
# ============================================================================

class TestAuthRequired:
    """Tests that wallet endpoints require authentication."""

    @pytest.mark.asyncio
    async def test_summary_unauthenticated(self, async_client):
        """Wallet summary requires auth token."""
        response = await async_client.get("/api/v1/wallet/summary")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_config_unauthenticated(self, async_client):
        """Wallet config requires auth token."""
        response = await async_client.get("/api/v1/wallet/config")
        assert response.status_code in (401, 403)


# ============================================================================
# Channel management — open channel
# ============================================================================

class TestOpenChannel:
    """Tests for POST /wallet/channels/open."""

    VALID_PUBKEY = "02" + "ab" * 32  # 66 hex chars
    VALID_ADDRESS = VALID_PUBKEY + "@127.0.0.1:9735"

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.lnd_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_open_channel_success(self, mock_settings, mock_lnd, async_client, test_admin_user):
        """Successfully opens a channel and returns funding txid."""
        mock_settings.use_lnd = True
        mock_lnd.connect_peer = AsyncMock(return_value=({}, None))
        mock_lnd.open_channel = AsyncMock(return_value=(
            {"funding_txid": "abc123def456"},
            None,
        ))

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/channels/open",
            json={
                "node_address": self.VALID_ADDRESS,
                "local_funding_amount": 500000,
                "sat_per_vbyte": 10,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["funding_txid"] == "abc123def456"
        mock_lnd.connect_peer.assert_called_once_with(self.VALID_PUBKEY, "127.0.0.1:9735")
        mock_lnd.open_channel.assert_called_once_with(
            node_pubkey_hex=self.VALID_PUBKEY,
            local_funding_amount=500000,
            sat_per_vbyte=10,
        )

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.lnd_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_open_channel_peer_connect_fails(self, mock_settings, mock_lnd, async_client, test_admin_user):
        """502 when peer connection fails."""
        mock_settings.use_lnd = True
        mock_lnd.connect_peer = AsyncMock(return_value=(None, "connection refused"))

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/channels/open",
            json={
                "node_address": self.VALID_ADDRESS,
                "local_funding_amount": 500000,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 502
        assert "connect to peer" in response.json()["detail"]
        mock_lnd.open_channel.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.lnd_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_open_channel_open_fails(self, mock_settings, mock_lnd, async_client, test_admin_user):
        """502 when channel open fails (e.g. insufficient funds)."""
        mock_settings.use_lnd = True
        mock_lnd.connect_peer = AsyncMock(return_value=({}, None))
        mock_lnd.open_channel = AsyncMock(return_value=(None, "not enough funds"))

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/channels/open",
            json={
                "node_address": self.VALID_ADDRESS,
                "local_funding_amount": 500000,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 502
        assert "not enough funds" in response.json()["detail"]

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.settings")
    async def test_open_channel_invalid_pubkey_length(self, mock_settings, async_client, test_admin_user):
        """400 when pubkey is wrong length."""
        mock_settings.use_lnd = True

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/channels/open",
            json={
                "node_address": "02abcd@127.0.0.1:9735",
                "local_funding_amount": 500000,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
        assert "pubkey length" in response.json()["detail"]

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.settings")
    async def test_open_channel_non_hex_pubkey(self, mock_settings, async_client, test_admin_user):
        """400 when pubkey contains non-hex chars."""
        mock_settings.use_lnd = True

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        bad_pubkey = "zz" + "ab" * 32  # 66 chars but not valid hex
        response = await async_client.post(
            "/api/v1/wallet/channels/open",
            json={
                "node_address": f"{bad_pubkey}@127.0.0.1:9735",
                "local_funding_amount": 500000,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
        assert "hexadecimal" in response.json()["detail"]

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.settings")
    async def test_open_channel_missing_at_sign(self, mock_settings, async_client, test_admin_user):
        """400 when node address has no @ separator."""
        mock_settings.use_lnd = True

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/channels/open",
            json={
                "node_address": self.VALID_PUBKEY,  # no @host:port
                "local_funding_amount": 500000,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
        assert "pubkey@host:port" in response.json()["detail"]

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.settings")
    async def test_open_channel_non_admin_rejected(self, mock_settings, async_client, test_user):
        """Non-admin users cannot open channels."""
        mock_settings.use_lnd = True

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/channels/open",
            json={
                "node_address": self.VALID_ADDRESS,
                "local_funding_amount": 500000,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.settings")
    async def test_open_channel_zero_amount_rejected(self, mock_settings, async_client, test_admin_user):
        """Pydantic rejects local_funding_amount of 0 (must be > 0)."""
        mock_settings.use_lnd = True

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/channels/open",
            json={
                "node_address": self.VALID_ADDRESS,
                "local_funding_amount": 0,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.lnd_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_open_channel_no_fee_rate(self, mock_settings, mock_lnd, async_client, test_admin_user):
        """sat_per_vbyte is optional — omitting it passes None to LND."""
        mock_settings.use_lnd = True
        mock_lnd.connect_peer = AsyncMock(return_value=({}, None))
        mock_lnd.open_channel = AsyncMock(return_value=(
            {"funding_txid": "tx_no_fee"},
            None,
        ))

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_admin_user.id)})

        response = await async_client.post(
            "/api/v1/wallet/channels/open",
            json={
                "node_address": self.VALID_ADDRESS,
                "local_funding_amount": 100000,
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        mock_lnd.open_channel.assert_called_once_with(
            node_pubkey_hex=self.VALID_PUBKEY,
            local_funding_amount=100000,
            sat_per_vbyte=None,
        )


# ============================================================================
# Channel management — pending channels detail
# ============================================================================

class TestPendingChannelsDetail:
    """Tests for GET /wallet/channels/pending/detail."""

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.lnd_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_pending_channels_success(self, mock_settings, mock_lnd, async_client, test_user):
        """Returns detailed pending channel list."""
        mock_settings.use_lnd = True
        mock_lnd.get_pending_channels_detail = AsyncMock(return_value=[
            {
                "type": "pending_open",
                "capacity": 500000,
                "remote_node_pub": "02ab" + "cd" * 31,
                "channel_point": "abc:0",
            },
        ])

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/channels/pending/detail",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["pending_channels"]) == 1
        assert data["pending_channels"][0]["type"] == "pending_open"
        assert data["pending_channels"][0]["capacity"] == 500000

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.lnd_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_pending_channels_empty(self, mock_settings, mock_lnd, async_client, test_user):
        """Returns empty list when no pending channels."""
        mock_settings.use_lnd = True
        mock_lnd.get_pending_channels_detail = AsyncMock(return_value=[])

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/channels/pending/detail",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["pending_channels"] == []

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.lnd_service")
    @patch("app.api.endpoints.wallet.settings")
    async def test_pending_channels_lnd_down(self, mock_settings, mock_lnd, async_client, test_user):
        """503 when LND is unreachable."""
        mock_settings.use_lnd = True
        mock_lnd.get_pending_channels_detail = AsyncMock(return_value=None)

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/channels/pending/detail",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 503
        assert "LND" in response.json()["detail"]

    @pytest.mark.asyncio
    @patch("app.api.endpoints.wallet.settings")
    async def test_pending_channels_lnd_disabled(self, mock_settings, async_client, test_user):
        """Returns 404 when LND is disabled."""
        mock_settings.use_lnd = False

        from app.core.security import create_access_token
        token = create_access_token(data={"sub": str(test_user.id)})

        response = await async_client.get(
            "/api/v1/wallet/channels/pending/detail",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404
