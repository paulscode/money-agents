from pydantic import SecretStr
"""
Unit tests for the LND Lightning tool executor (_execute_lnd_lightning).

Tests all 10 action types, budget enforcement, transaction recording,
parameter validation, and error handling.
"""
import pytest
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from uuid import uuid4, UUID
from unittest.mock import AsyncMock, MagicMock, patch, ANY


# Mirror the ToolExecutionResult from tool_execution_service
@dataclass
class ToolExecutionResult:
    success: bool
    output: Any
    error: Optional[str] = None
    duration_ms: int = 0
    cost_units: int = 0
    cost_details: Optional[Dict] = None


@dataclass
class BudgetCheckResult:
    allowed: bool
    trigger: Any = None
    reason: str = ""
    budget_context: Optional[Dict] = None


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def tool_executor():
    """Provide a minimal ToolExecutor instance."""
    from app.services.tool_execution_service import ToolExecutor
    executor = ToolExecutor.__new__(ToolExecutor)
    return executor


@pytest.fixture
def mock_tool():
    """Provide a mock Tool object for passing to the executor."""
    mock = MagicMock()
    mock.name = "LND Lightning"
    mock.slug = "lnd_lightning"
    return mock


# ============================================================================
# Missing / empty action
# ============================================================================

class TestActionValidation:
    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_missing_action(self, mock_settings, tool_executor, mock_tool):
        """Returns error when action is missing."""
        result = await tool_executor._execute_lnd_lightning(mock_tool, {})
        assert result.success is False
        assert "Missing required parameter: action" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_empty_action(self, mock_settings, tool_executor, mock_tool):
        """Returns error when action is empty string."""
        result = await tool_executor._execute_lnd_lightning(mock_tool, {"action": ""})
        assert result.success is False

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_unknown_action(self, mock_settings, tool_executor, mock_tool):
        """Returns error for an unrecognized action."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            result = await tool_executor._execute_lnd_lightning(mock_tool, {"action": "destroy_node"})
        assert result.success is False
        assert "Unknown action" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_lnd_not_configured(self, mock_settings, tool_executor, mock_tool):
        """Returns error when LND credentials are missing."""
        mock_settings.lnd_macaroon_hex = SecretStr("")
        with patch("app.services.lnd_service.lnd_service", None):
            result = await tool_executor._execute_lnd_lightning(mock_tool, {"action": "balance"})
        assert result.success is False
        assert "not configured" in result.error


# ============================================================================
# Read-only actions
# ============================================================================

class TestReadOnlyActions:
    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_balance(self, mock_settings, tool_executor, mock_tool):
        """Balance action merges wallet + channel balance."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            mock_lnd.get_wallet_balance = AsyncMock(return_value={"confirmed_balance": 100000})
            mock_lnd.get_channel_balance = AsyncMock(return_value={"local_balance_sat": 50000})

            result = await tool_executor._execute_lnd_lightning(mock_tool, {"action": "balance"})

        assert result.success is True
        assert result.output["wallet_balance"]["confirmed_balance"] == 100000
        assert result.output["channel_balance"]["local_balance_sat"] == 50000

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_info(self, mock_settings, tool_executor, mock_tool):
        """Info action returns node information."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            mock_lnd.get_info = AsyncMock(return_value={"alias": "testnode", "synced_to_chain": True})

            result = await tool_executor._execute_lnd_lightning(mock_tool, {"action": "info"})

        assert result.success is True
        assert result.output["alias"] == "testnode"

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_list_payments(self, mock_settings, tool_executor, mock_tool):
        """List payments with custom limit."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            mock_lnd.get_recent_payments = AsyncMock(return_value=[{"value": 100}])

            result = await tool_executor._execute_lnd_lightning(mock_tool, {"action": "list_payments", "limit": 5})

        assert result.success is True
        assert len(result.output["payments"]) == 1
        mock_lnd.get_recent_payments.assert_called_once_with(max_payments=5)

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_list_invoices(self, mock_settings, tool_executor, mock_tool):
        """List invoices with default limit."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            mock_lnd.get_recent_invoices = AsyncMock(return_value=[{"memo": "test"}])

            result = await tool_executor._execute_lnd_lightning(mock_tool, {"action": "list_invoices"})

        assert result.success is True
        assert len(result.output["invoices"]) == 1
        mock_lnd.get_recent_invoices.assert_called_once_with(num_max_invoices=20)

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_list_channels(self, mock_settings, tool_executor, mock_tool):
        """List channels returns channel list."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            mock_lnd.get_channels = AsyncMock(return_value=[{"chan_id": "123"}])

            result = await tool_executor._execute_lnd_lightning(mock_tool, {"action": "list_channels"})

        assert result.success is True
        assert result.output["channels"][0]["chan_id"] == "123"


# ============================================================================
# Estimate fee
# ============================================================================

class TestEstimateFee:
    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_estimate_fee_success(self, mock_settings, tool_executor, mock_tool):
        """Estimate fee combines mempool + LND estimates."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")

        with patch("app.services.lnd_service.lnd_service") as mock_lnd, \
             patch("app.services.mempool_fee_service.mempool_fee_service") as mock_mempool:

            mock_lnd.estimate_fee = AsyncMock(return_value={"fee_sat": 500, "sat_per_vbyte": 10})
            mock_mempool.get_recommended_fees = AsyncMock(return_value={"fastestFee": 20})
            mock_mempool.get_fee_for_priority = AsyncMock(return_value=15)
            mock_mempool.get_target_conf_for_priority = MagicMock(return_value=6)

            result = await tool_executor._execute_lnd_lightning(
                mock_tool, {"action": "estimate_fee", "address": "bc1q...", "amount_sats": 10000}
            )

        assert result.success is True
        assert result.output["fee_priority"] == "medium"
        assert result.output["recommended_sat_per_vbyte"] == 15

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_estimate_fee_missing_address(self, mock_settings, tool_executor, mock_tool):
        """Error when address is missing."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        with patch("app.services.lnd_service.lnd_service"):
            result = await tool_executor._execute_lnd_lightning(
                mock_tool, {"action": "estimate_fee", "amount_sats": 10000}
            )
        assert result.success is False
        assert "address" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_estimate_fee_zero_amount(self, mock_settings, tool_executor, mock_tool):
        """Error when amount is zero."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        with patch("app.services.lnd_service.lnd_service"):
            result = await tool_executor._execute_lnd_lightning(
                mock_tool, {"action": "estimate_fee", "address": "bc1q...", "amount_sats": 0}
            )
        assert result.success is False


# ============================================================================
# Create invoice (receive)
# ============================================================================

class TestCreateInvoice:
    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_create_invoice_success(self, mock_settings, tool_executor, mock_tool):
        """Creates invoice and records pending transaction."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        user_id = str(uuid4())
        exec_id = str(uuid4())

        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            mock_lnd.create_invoice = AsyncMock(return_value={
                "payment_request": "lnbc10u1p...",
                "r_hash": "abcdef",
            })

            mock_session = AsyncMock()
            mock_budget_svc = AsyncMock()
            mock_budget_svc.record_transaction = AsyncMock()

            with patch.object(tool_executor, "_get_db_session") as mock_get_db:
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
                mock_ctx.__aexit__ = AsyncMock(return_value=False)
                mock_get_db.return_value = mock_ctx

                with patch("app.services.bitcoin_budget_service.BitcoinBudgetService", return_value=mock_budget_svc):
                    result = await tool_executor._execute_lnd_lightning(mock_tool, {
                        "action": "create_invoice",
                        "amount_sats": 5000,
                        "memo": "Test invoice",
                        "__ma_user_id": user_id,
                        "__ma_execution_id": exec_id,
                    })

        assert result.success is True
        assert result.output["payment_request"] == "lnbc10u1p..."
        assert result.cost_units == 0  # Receiving, no cost

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_create_invoice_zero_amount(self, mock_settings, tool_executor, mock_tool):
        """Error when amount_sats is zero."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        with patch("app.services.lnd_service.lnd_service"):
            result = await tool_executor._execute_lnd_lightning(
                mock_tool, {"action": "create_invoice", "amount_sats": 0}
            )
        assert result.success is False
        assert "amount_sats" in result.error


# ============================================================================
# Decode invoice
# ============================================================================

class TestDecodeInvoice:
    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_decode_invoice_success(self, mock_settings, tool_executor, mock_tool):
        """Decodes a BOLT11 payment request."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            mock_lnd.decode_payment_request = AsyncMock(return_value=(
                {"num_satoshis": "10000", "destination": "03abc..."}, None
            ))

            result = await tool_executor._execute_lnd_lightning(
                mock_tool, {"action": "decode_invoice", "payment_request": "lnbc10u1p..."}
            )

        assert result.success is True
        assert result.output["num_satoshis"] == "10000"

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_decode_invoice_missing_request(self, mock_settings, tool_executor, mock_tool):
        """Error when payment_request is missing."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        with patch("app.services.lnd_service.lnd_service"):
            result = await tool_executor._execute_lnd_lightning(
                mock_tool, {"action": "decode_invoice"}
            )
        assert result.success is False
        assert "payment_request" in result.error


# ============================================================================
# Pay invoice (budget-enforced)
# ============================================================================

class TestPayInvoice:
    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_pay_invoice_allowed(self, mock_settings, tool_executor, mock_tool):
        """Successful payment when budget allows."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        user_id = str(uuid4())

        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            mock_lnd.decode_payment_request = AsyncMock(return_value=(
                {"num_satoshis": "5000"}, None
            ))
            mock_lnd.send_payment_sync = AsyncMock(return_value=({
                "payment_hash": "xyz789",
                "payment_route": {"total_fees": 5},
            }, None))

            mock_session = AsyncMock()
            mock_budget_svc = AsyncMock()
            
            budget_result = MagicMock()
            budget_result.allowed = True
            mock_budget_svc.check_spend = AsyncMock(return_value=budget_result)
            mock_budget_svc.record_transaction = AsyncMock()

            with patch.object(tool_executor, "_get_db_session") as mock_get_db:
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
                mock_ctx.__aexit__ = AsyncMock(return_value=False)
                mock_get_db.return_value = mock_ctx

                with patch("app.services.bitcoin_budget_service.BitcoinBudgetService", return_value=mock_budget_svc):
                    result = await tool_executor._execute_lnd_lightning(mock_tool, {
                        "action": "pay_invoice",
                        "payment_request": "lnbc5u1p...",
                        "__ma_user_id": user_id,
                    })

        assert result.success is True
        assert result.output["amount_sats"] == 5000
        assert result.output["fee_sats"] == 5
        assert result.cost_units == 5005

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_pay_invoice_budget_denied(self, mock_settings, tool_executor, mock_tool):
        """Payment denied when over budget."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")

        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            mock_lnd.decode_payment_request = AsyncMock(return_value=(
                {"num_satoshis": "500000"}, None
            ))

            mock_session = AsyncMock()
            mock_budget_svc = AsyncMock()

            trigger_mock = MagicMock()
            trigger_mock.value = "over_budget"
            budget_result = MagicMock()
            budget_result.allowed = False
            budget_result.trigger = trigger_mock
            budget_result.reason = "Campaign budget exceeded"
            budget_result.budget_context = {"remaining": 100}

            mock_budget_svc.check_spend = AsyncMock(return_value=budget_result)

            with patch.object(tool_executor, "_get_db_session") as mock_get_db:
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
                mock_ctx.__aexit__ = AsyncMock(return_value=False)
                mock_get_db.return_value = mock_ctx

                with patch("app.services.bitcoin_budget_service.BitcoinBudgetService", return_value=mock_budget_svc):
                    result = await tool_executor._execute_lnd_lightning(mock_tool, {
                        "action": "pay_invoice",
                        "payment_request": "lnbc500u1p...",
                    })

        assert result.success is False
        assert "Budget check failed" in result.error
        assert result.output["budget_check"]["trigger"] == "over_budget"

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_pay_invoice_missing_request(self, mock_settings, tool_executor, mock_tool):
        """Error when payment_request is empty."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        with patch("app.services.lnd_service.lnd_service"):
            result = await tool_executor._execute_lnd_lightning(
                mock_tool, {"action": "pay_invoice"}
            )
        assert result.success is False
        assert "payment_request" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_pay_invoice_decode_fails(self, mock_settings, tool_executor, mock_tool):
        """Error when decode_payment_request returns an error."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            mock_lnd.decode_payment_request = AsyncMock(return_value=(None, "Invalid BOLT11"))

            result = await tool_executor._execute_lnd_lightning(
                mock_tool, {"action": "pay_invoice", "payment_request": "invalid_bolt11"}
            )
        assert result.success is False
        assert "decode" in result.error.lower()

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_pay_invoice_zero_amount(self, mock_settings, tool_executor, mock_tool):
        """Error when decoded invoice has zero amount."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            mock_lnd.decode_payment_request = AsyncMock(return_value=(
                {"num_satoshis": "0"}, None
            ))

            result = await tool_executor._execute_lnd_lightning(
                mock_tool, {"action": "pay_invoice", "payment_request": "lnbc1p..."}
            )
        assert result.success is False
        assert "amount" in result.error.lower()

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_pay_invoice_payment_error(self, mock_settings, tool_executor, mock_tool):
        """Handles LND returning a payment_error in the response."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")

        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            mock_lnd.decode_payment_request = AsyncMock(return_value=(
                {"num_satoshis": "1000"}, None
            ))
            mock_lnd.send_payment_sync = AsyncMock(return_value=(
                None, "Payment failed: insufficient_balance"
            ))

            mock_session = AsyncMock()
            mock_budget_svc = AsyncMock()
            budget_result = MagicMock()
            budget_result.allowed = True
            mock_budget_svc.check_spend = AsyncMock(return_value=budget_result)

            with patch.object(tool_executor, "_get_db_session") as mock_get_db:
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
                mock_ctx.__aexit__ = AsyncMock(return_value=False)
                mock_get_db.return_value = mock_ctx

                with patch("app.services.bitcoin_budget_service.BitcoinBudgetService", return_value=mock_budget_svc):
                    result = await tool_executor._execute_lnd_lightning(mock_tool, {
                        "action": "pay_invoice",
                        "payment_request": "lnbc1u1p...",
                    })

        assert result.success is False
        assert "Payment failed" in result.error


# ============================================================================
# Send on-chain (budget-enforced)
# ============================================================================

class TestSendOnchain:
    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_send_onchain_allowed(self, mock_settings, tool_executor, mock_tool):
        """Successful on-chain send when budget allows."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        user_id = str(uuid4())

        with patch("app.services.lnd_service.lnd_service") as mock_lnd, \
             patch("app.services.mempool_fee_service.mempool_fee_service") as mock_mempool:

            mock_lnd.send_coins = AsyncMock(return_value=({"txid": "deadbeef"}, None))
            mock_mempool.get_fee_for_priority = AsyncMock(return_value=10)

            mock_session = AsyncMock()
            mock_budget_svc = AsyncMock()
            budget_result = MagicMock()
            budget_result.allowed = True
            mock_budget_svc.check_spend = AsyncMock(return_value=budget_result)
            mock_budget_svc.record_transaction = AsyncMock()

            with patch.object(tool_executor, "_get_db_session") as mock_get_db:
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
                mock_ctx.__aexit__ = AsyncMock(return_value=False)
                mock_get_db.return_value = mock_ctx

                with patch("app.services.bitcoin_budget_service.BitcoinBudgetService", return_value=mock_budget_svc):
                    result = await tool_executor._execute_lnd_lightning(mock_tool, {
                        "action": "send_onchain",
                        "address": "bc1qtest...",
                        "amount_sats": 50000,
                        "__ma_user_id": user_id,
                    })

        assert result.success is True
        assert result.output["txid"] == "deadbeef"
        assert result.output["fee_source"] == "mempool:medium"
        assert result.cost_units == 50000

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_send_onchain_budget_denied(self, mock_settings, tool_executor, mock_tool):
        """On-chain send denied when over budget."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")

        with patch("app.services.lnd_service.lnd_service"), \
             patch("app.services.mempool_fee_service.mempool_fee_service") as mock_mempool:

            mock_mempool.get_fee_for_priority = AsyncMock(return_value=10)

            mock_session = AsyncMock()
            mock_budget_svc = AsyncMock()

            trigger_mock = MagicMock()
            trigger_mock.value = "global_limit"
            budget_result = MagicMock()
            budget_result.allowed = False
            budget_result.trigger = trigger_mock
            budget_result.reason = "Global limit reached"
            budget_result.budget_context = {}

            mock_budget_svc.check_spend = AsyncMock(return_value=budget_result)

            with patch.object(tool_executor, "_get_db_session") as mock_get_db:
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
                mock_ctx.__aexit__ = AsyncMock(return_value=False)
                mock_get_db.return_value = mock_ctx

                with patch("app.services.bitcoin_budget_service.BitcoinBudgetService", return_value=mock_budget_svc):
                    result = await tool_executor._execute_lnd_lightning(mock_tool, {
                        "action": "send_onchain",
                        "address": "bc1qtest...",
                        "amount_sats": 50000,
                    })

        assert result.success is False
        assert "Budget check failed" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_send_onchain_missing_address(self, mock_settings, tool_executor, mock_tool):
        """Error when address is missing."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        with patch("app.services.lnd_service.lnd_service"), \
             patch("app.services.mempool_fee_service.mempool_fee_service") as mock_mempool:
            mock_mempool.get_fee_for_priority = AsyncMock(return_value=10)

            result = await tool_executor._execute_lnd_lightning(
                mock_tool, {"action": "send_onchain", "amount_sats": 1000}
            )
        assert result.success is False
        assert "address" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_send_onchain_explicit_fee_rate(self, mock_settings, tool_executor, mock_tool):
        """Uses explicit sat_per_vbyte when provided."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")

        with patch("app.services.lnd_service.lnd_service") as mock_lnd, \
             patch("app.services.mempool_fee_service.mempool_fee_service") as mock_mempool:

            mock_lnd.send_coins = AsyncMock(return_value=({"txid": "beef"}, None))
            # mempool should not be called for fee when explicit rate provided
            mock_mempool.get_fee_for_priority = AsyncMock(return_value=10)

            mock_session = AsyncMock()
            mock_budget_svc = AsyncMock()
            budget_result = MagicMock()
            budget_result.allowed = True
            mock_budget_svc.check_spend = AsyncMock(return_value=budget_result)

            with patch.object(tool_executor, "_get_db_session") as mock_get_db:
                mock_ctx = AsyncMock()
                mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
                mock_ctx.__aexit__ = AsyncMock(return_value=False)
                mock_get_db.return_value = mock_ctx

                with patch("app.services.bitcoin_budget_service.BitcoinBudgetService", return_value=mock_budget_svc):
                    result = await tool_executor._execute_lnd_lightning(mock_tool, {
                        "action": "send_onchain",
                        "address": "bc1qtest...",
                        "amount_sats": 10000,
                        "sat_per_vbyte": 25,
                    })

        assert result.success is True
        assert result.output["fee_source"] == "explicit"
        assert result.output["sat_per_vbyte_used"] == 25
        mock_lnd.send_coins.assert_called_once_with(
            address="bc1qtest...", amount_sats=10000, sat_per_vbyte=25
        )


# ============================================================================
# Exception handling
# ============================================================================

class TestExceptionHandling:
    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_lnd_exception_caught(self, mock_settings, tool_executor, mock_tool):
        """Exceptions from LND calls are caught and returned as generic errors."""
        mock_settings.lnd_macaroon_hex = SecretStr("aabb")
        with patch("app.services.lnd_service.lnd_service") as mock_lnd:
            mock_lnd.get_info = AsyncMock(side_effect=Exception("Connection refused"))

            result = await tool_executor._execute_lnd_lightning(mock_tool, {"action": "info"})

        assert result.success is False
        # Agent-facing error is generic (HIGH-3 mitigation — no infrastructure leakage)
        assert "Lightning operation failed" in result.error
