"""
Unit tests for Mempool Fee Estimation Service.

Tests fee rate fetching, caching, priority mapping, and error handling.
"""
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import app.services.mempool_fee_service as fee_module
from app.services.mempool_fee_service import (
    MempoolFeeService,
    PRIORITY_MAP,
    PRIORITY_TARGET_CONF,
    _CACHE_TTL_SECONDS,
)


# Sample Mempool API response
SAMPLE_FEES = {
    "fastestFee": 25,
    "halfHourFee": 15,
    "hourFee": 8,
    "economyFee": 4,
    "minimumFee": 1,
}


@pytest.fixture(autouse=True)
def clear_fee_cache():
    """Clear the module-level fee cache before each test."""
    fee_module._fee_cache = None
    fee_module._fee_cache_time = 0
    yield
    fee_module._fee_cache = None
    fee_module._fee_cache_time = 0


# ============================================================================
# MempoolFeeService — get_recommended_fees
# ============================================================================

class TestGetRecommendedFees:
    """Tests for fetching recommended fees from Mempool."""

    @pytest.mark.asyncio
    @patch("app.services.mempool_fee_service.settings")
    async def test_fetch_fees_success(self, mock_settings):
        """Successfully fetches and returns fee rates."""
        mock_settings.lnd_mempool_url = "https://mempool.space"

        service = MempoolFeeService()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_FEES
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await service.get_recommended_fees()

        assert result is not None
        assert result["fastestFee"] == 25
        assert result["halfHourFee"] == 15
        assert result["hourFee"] == 8

    @pytest.mark.asyncio
    @patch("app.services.mempool_fee_service.settings")
    async def test_fetch_fees_cached(self, mock_settings):
        """Returns cached result within TTL."""
        mock_settings.lnd_mempool_url = "https://mempool.space"

        # Pre-populate cache
        fee_module._fee_cache = SAMPLE_FEES
        fee_module._fee_cache_time = time.time()

        service = MempoolFeeService()
        result = await service.get_recommended_fees()

        assert result == SAMPLE_FEES

    @pytest.mark.asyncio
    @patch("app.services.mempool_fee_service.settings")
    async def test_fetch_fees_stale_cache_refetches(self, mock_settings):
        """Refetches when cache is older than TTL."""
        mock_settings.lnd_mempool_url = "https://mempool.space"

        # Pre-populate stale cache
        fee_module._fee_cache = {"fastestFee": 10}
        fee_module._fee_cache_time = time.time() - _CACHE_TTL_SECONDS - 1

        service = MempoolFeeService()

        new_fees = {**SAMPLE_FEES, "fastestFee": 50}
        mock_response = MagicMock()
        mock_response.json.return_value = new_fees
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await service.get_recommended_fees()

        assert result["fastestFee"] == 50

    @pytest.mark.asyncio
    @patch("app.services.mempool_fee_service.settings")
    async def test_fetch_fees_http_error(self, mock_settings):
        """Returns None on HTTP error."""
        mock_settings.lnd_mempool_url = "https://mempool.space"

        service = MempoolFeeService()

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
                "error", request=MagicMock(), response=mock_response
            )
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await service.get_recommended_fees()

        assert result is None

    @pytest.mark.asyncio
    @patch("app.services.mempool_fee_service.settings")
    async def test_fetch_fees_connection_error(self, mock_settings):
        """Returns None on connection error."""
        mock_settings.lnd_mempool_url = "https://mempool.space"

        service = MempoolFeeService()

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await service.get_recommended_fees()

        assert result is None

    @pytest.mark.asyncio
    @patch("app.services.mempool_fee_service.settings")
    async def test_fetch_fees_missing_fields(self, mock_settings):
        """Returns None when response is missing required fields."""
        mock_settings.lnd_mempool_url = "https://mempool.space"

        service = MempoolFeeService()

        mock_response = MagicMock()
        mock_response.json.return_value = {"fastestFee": 25}  # Missing others
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client

            result = await service.get_recommended_fees()

        assert result is None


# ============================================================================
# MempoolFeeService — get_fee_for_priority
# ============================================================================

class TestGetFeeForPriority:
    """Tests for priority-based fee rate lookup."""

    @pytest.mark.asyncio
    async def test_low_priority(self):
        """Low priority returns hourFee."""
        service = MempoolFeeService()
        service.get_recommended_fees = AsyncMock(return_value=SAMPLE_FEES)

        result = await service.get_fee_for_priority("low")
        assert result == 8

    @pytest.mark.asyncio
    async def test_medium_priority(self):
        """Medium priority returns halfHourFee."""
        service = MempoolFeeService()
        service.get_recommended_fees = AsyncMock(return_value=SAMPLE_FEES)

        result = await service.get_fee_for_priority("medium")
        assert result == 15

    @pytest.mark.asyncio
    async def test_high_priority(self):
        """High priority returns fastestFee."""
        service = MempoolFeeService()
        service.get_recommended_fees = AsyncMock(return_value=SAMPLE_FEES)

        result = await service.get_fee_for_priority("high")
        assert result == 25

    @pytest.mark.asyncio
    async def test_unknown_priority_defaults_medium(self):
        """Unknown priority defaults to medium."""
        service = MempoolFeeService()
        service.get_recommended_fees = AsyncMock(return_value=SAMPLE_FEES)

        result = await service.get_fee_for_priority("ultra")
        assert result == 15  # halfHourFee

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        """Priority string is case-insensitive."""
        service = MempoolFeeService()
        service.get_recommended_fees = AsyncMock(return_value=SAMPLE_FEES)

        result = await service.get_fee_for_priority("HIGH")
        assert result == 25

    @pytest.mark.asyncio
    async def test_mempool_unreachable_returns_none(self):
        """Returns None when Mempool is unreachable."""
        service = MempoolFeeService()
        service.get_recommended_fees = AsyncMock(return_value=None)

        result = await service.get_fee_for_priority("medium")
        assert result is None

    @pytest.mark.asyncio
    async def test_minimum_one_sat_per_vbyte(self):
        """Fee rate is clamped to at least 1 sat/vB."""
        service = MempoolFeeService()
        service.get_recommended_fees = AsyncMock(return_value={
            **SAMPLE_FEES, "hourFee": 0
        })

        result = await service.get_fee_for_priority("low")
        assert result == 1


# ============================================================================
# MempoolFeeService — get_target_conf_for_priority
# ============================================================================

class TestGetTargetConf:
    """Tests for LND fallback target confirmations."""

    def test_low_priority_target_conf(self):
        """Low priority targets ~1 day (144 blocks)."""
        service = MempoolFeeService()
        assert service.get_target_conf_for_priority("low") == 144

    def test_medium_priority_target_conf(self):
        """Medium priority targets ~1 hour (6 blocks)."""
        service = MempoolFeeService()
        assert service.get_target_conf_for_priority("medium") == 6

    def test_high_priority_target_conf(self):
        """High priority targets next block."""
        service = MempoolFeeService()
        assert service.get_target_conf_for_priority("high") == 1

    def test_unknown_defaults_to_6(self):
        """Unknown priority defaults to 6 blocks."""
        service = MempoolFeeService()
        assert service.get_target_conf_for_priority("unknown") == 6


# ============================================================================
# Priority mapping constants
# ============================================================================

class TestPriorityConstants:
    """Tests for priority mapping constants."""

    def test_priority_map_keys(self):
        """All three priority levels are mapped."""
        assert set(PRIORITY_MAP.keys()) == {"low", "medium", "high"}

    def test_priority_target_conf_keys(self):
        """All three priority levels have target confs."""
        assert set(PRIORITY_TARGET_CONF.keys()) == {"low", "medium", "high"}

    def test_target_confs_descending(self):
        """Target confs decrease with priority (more blocks for low)."""
        assert PRIORITY_TARGET_CONF["low"] > PRIORITY_TARGET_CONF["medium"]
        assert PRIORITY_TARGET_CONF["medium"] > PRIORITY_TARGET_CONF["high"]
