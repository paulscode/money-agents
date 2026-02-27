"""Tests for pricing_update_service — automatic LLM pricing refresh.

Covers:
  • OpenRouter response parsing & per-token → per-1M conversion
  • Matching OpenRouter model IDs to local MODEL_PRICING keys
  • Price change detection and in-memory update
  • Zero-price safety guard
  • Flexible date-suffix matching
  • Error handling (network errors, bad responses)
  • Celery task wrapper
  • API endpoints (GET /usage/pricing, POST /usage/pricing/refresh)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.pricing_update_service import (
    PriceChange,
    PricingUpdateResult,
    _per_token_to_per_million,
    _match_and_update,
    _OUR_KEY_TO_OPENROUTER,
    _OPENROUTER_TO_OUR_KEY,
    refresh_model_pricing,
    get_last_pricing_update,
)


# =============================================================================
# Unit conversion
# =============================================================================


class TestPerTokenToPerMillion:
    """Test the per-token → per-1M-tokens conversion."""

    def test_standard_conversion(self):
        # $3.00 per 1M = $0.000003 per token
        assert _per_token_to_per_million("0.000003") == 3.0

    def test_zero(self):
        assert _per_token_to_per_million("0") == 0.0

    def test_small_value(self):
        # $0.15 per 1M
        assert _per_token_to_per_million("0.00000015") == 0.15

    def test_large_value(self):
        # $25.00 per 1M
        assert _per_token_to_per_million("0.000025") == 25.0

    def test_invalid_string(self):
        assert _per_token_to_per_million("not-a-number") == 0.0

    def test_none(self):
        assert _per_token_to_per_million(None) == 0.0

    def test_empty_string(self):
        assert _per_token_to_per_million("") == 0.0


# =============================================================================
# PriceChange dataclass
# =============================================================================


class TestPriceChange:
    def test_delta_calculation(self):
        c = PriceChange("gpt-4o", 2.50, 10.00, 3.00, 12.00)
        assert c.input_delta == 0.5
        assert c.output_delta == 2.0

    def test_str_representation(self):
        c = PriceChange("gpt-4o", 2.50, 10.00, 3.00, 12.00)
        s = str(c)
        assert "gpt-4o" in s
        assert "$2.50" in s
        assert "$3.00" in s


# =============================================================================
# PricingUpdateResult
# =============================================================================


class TestPricingUpdateResult:
    def test_to_dict(self):
        r = PricingUpdateResult(
            success=True,
            checked_at="2026-02-18T03:00:00",
            models_checked=5,
            models_matched=4,
            models_updated=1,
            models_not_found=["glm-5"],
            changes=[PriceChange("gpt-4o", 2.50, 10.00, 3.00, 12.00)],
        )
        d = r.to_dict()
        assert d["success"] is True
        assert d["models_updated"] == 1
        assert len(d["changes"]) == 1
        assert d["changes"][0]["model"] == "gpt-4o"
        assert d["changes"][0]["old"]["input_per_1m"] == 2.50
        assert d["changes"][0]["new"]["input_per_1m"] == 3.00


# =============================================================================
# Mapping integrity
# =============================================================================


class TestMappingIntegrity:
    """Verify that the bidirectional mapping is consistent."""

    def test_reverse_map_is_inverse(self):
        for our_key, or_id in _OUR_KEY_TO_OPENROUTER.items():
            assert _OPENROUTER_TO_OUR_KEY[or_id] == our_key

    def test_all_anthropic_models_have_prefix(self):
        for our_key, or_id in _OUR_KEY_TO_OPENROUTER.items():
            if our_key.startswith("claude"):
                assert or_id.startswith("anthropic/"), f"{our_key} → {or_id}"

    def test_all_openai_models_have_prefix(self):
        for our_key, or_id in _OUR_KEY_TO_OPENROUTER.items():
            if our_key.startswith(("gpt", "o1", "o3", "o4")):
                assert or_id.startswith("openai/"), f"{our_key} → {or_id}"


# =============================================================================
# _match_and_update  (core logic)
# =============================================================================


class TestMatchAndUpdate:
    """Tests for the matching + update logic against MODEL_PRICING."""

    def test_no_changes_when_prices_match(self):
        """When OpenRouter prices match our prices, nothing is updated."""
        from app.services.usage_service import MODEL_PRICING

        # Build fake OpenRouter data that matches current pricing
        openrouter_prices = {}
        for our_key, or_id in _OUR_KEY_TO_OPENROUTER.items():
            if our_key in MODEL_PRICING:
                inp, out = MODEL_PRICING[our_key]
                openrouter_prices[or_id] = (inp, out)

        result = _match_and_update(openrouter_prices)
        assert result.models_updated == 0
        assert len(result.changes) == 0

    def test_price_change_detected_and_applied(self):
        """Detect and apply a price change."""
        from app.services.usage_service import MODEL_PRICING

        key = "gpt-4o-mini"
        or_id = _OUR_KEY_TO_OPENROUTER.get(key)
        if or_id is None:
            pytest.skip("gpt-4o-mini not in mapping")

        original = MODEL_PRICING[key]
        # Feed a higher price
        fake_prices = {or_id: (0.20, 0.80)}
        result = _match_and_update(fake_prices)

        assert result.models_updated == 1
        assert result.changes[0].model_key == key
        assert MODEL_PRICING[key] == (0.20, 0.80)

        # Restore original
        MODEL_PRICING[key] = original

    def test_zero_price_not_applied_for_paid_model(self):
        """OpenRouter returning (0,0) for a paid model is ignored."""
        from app.services.usage_service import MODEL_PRICING

        key = "claude-sonnet-4-6"
        or_id = _OUR_KEY_TO_OPENROUTER.get(key)
        if or_id is None:
            pytest.skip("claude-sonnet-4-6 not in mapping")

        original = MODEL_PRICING[key]
        assert original[0] > 0 or original[1] > 0  # must be paid

        fake_prices = {or_id: (0.0, 0.0)}
        result = _match_and_update(fake_prices)

        # Price should NOT have changed
        assert MODEL_PRICING[key] == original
        assert result.models_updated == 0

    def test_date_suffix_matching(self):
        """OpenRouter IDs with date suffixes still match our keys."""
        from app.services.usage_service import MODEL_PRICING

        key = "claude-opus-4-6"
        or_id_base = _OUR_KEY_TO_OPENROUTER[key]
        # Simulate OpenRouter using a date-suffixed ID
        suffixed_id = or_id_base + "-20261201"

        original = MODEL_PRICING[key]
        fake_prices = {suffixed_id: (6.00, 30.00)}
        result = _match_and_update(fake_prices)

        assert result.models_updated == 1
        assert MODEL_PRICING[key] == (6.00, 30.00)

        # Restore
        MODEL_PRICING[key] = original

    def test_models_not_found_tracked(self):
        """Models missing from OpenRouter data are recorded."""
        result = _match_and_update({})  # No OpenRouter data
        assert len(result.models_not_found) == len(_OUR_KEY_TO_OPENROUTER)

    def test_small_rounding_difference_ignored(self):
        """Differences smaller than 0.005 are not treated as changes."""
        from app.services.usage_service import MODEL_PRICING

        key = "gpt-4o"
        or_id = _OUR_KEY_TO_OPENROUTER.get(key)
        if or_id is None:
            pytest.skip("gpt-4o not in mapping")

        original = MODEL_PRICING[key]
        # Add tiny difference within epsilon
        fake_prices = {or_id: (original[0] + 0.004, original[1] - 0.003)}
        result = _match_and_update(fake_prices)

        assert result.models_updated == 0
        MODEL_PRICING[key] = original  # Ensure restored


# =============================================================================
# refresh_model_pricing  (integration-style, mocked HTTP)
# =============================================================================


class TestRefreshModelPricing:
    """Test the top-level refresh function with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_success_path(self):
        """Successful refresh returns a valid result."""
        fake_resp = {
            "data": [
                {
                    "id": "anthropic/claude-sonnet-4-6",
                    "pricing": {
                        "prompt": "0.000003",
                        "completion": "0.000015",
                    },
                },
                {
                    "id": "openai/gpt-4.1-mini",
                    "pricing": {
                        "prompt": "0.0000004",
                        "completion": "0.0000016",
                    },
                },
            ]
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = fake_resp

        with patch("app.services.pricing_update_service.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await refresh_model_pricing()

        assert result.success is True
        assert result.models_checked > 0
        assert result.checked_at != ""

    @pytest.mark.asyncio
    async def test_network_error_handled(self):
        """Network errors result in success=False, not an exception."""
        import httpx

        with patch("app.services.pricing_update_service.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.side_effect = httpx.ConnectError("DNS resolution failed")
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await refresh_model_pricing()

        assert result.success is False
        assert "DNS resolution failed" in result.error

    @pytest.mark.asyncio
    async def test_http_error_handled(self):
        """HTTP 500 errors result in success=False."""
        import httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error",
            request=MagicMock(),
            response=mock_resp,
        )

        with patch("app.services.pricing_update_service.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            result = await refresh_model_pricing()

        assert result.success is False
        assert "500" in result.error

    @pytest.mark.asyncio
    async def test_last_result_stored(self):
        """get_last_pricing_update returns the most recent result."""
        fake_resp = {"data": []}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = fake_resp

        with patch("app.services.pricing_update_service.httpx.AsyncClient") as MockClient:
            instance = AsyncMock()
            instance.get.return_value = mock_resp
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = instance

            await refresh_model_pricing()

        last = get_last_pricing_update()
        assert last is not None
        assert last.success is True


# =============================================================================
# Celery task
# =============================================================================


class TestPricingCeleryTask:
    """Test the Celery task wrapper."""

    def test_task_returns_dict(self):
        """The Celery task returns a JSON-serialisable dict."""
        expected = PricingUpdateResult(
            success=True,
            checked_at="2026-02-18T03:00:00",
            models_checked=10,
            models_matched=8,
            models_updated=0,
        )

        mock_fn = AsyncMock(return_value=expected)
        with patch("app.services.pricing_update_service.refresh_model_pricing", mock_fn):
            from app.tasks.pricing_tasks import refresh_model_pricing as task_fn

            result = task_fn()

        assert isinstance(result, dict)
        assert result["success"] is True

    def test_task_handles_exception(self):
        """Exceptions in the async function are caught."""
        mock_fn = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("app.services.pricing_update_service.refresh_model_pricing", mock_fn):
            from app.tasks.pricing_tasks import refresh_model_pricing as task_fn

            result = task_fn()

        assert isinstance(result, dict)
        assert result["success"] is False
        assert "boom" in result["error"]


# =============================================================================
# API endpoints
# =============================================================================


class TestPricingAPIEndpoints:
    """Test the usage/pricing REST endpoints."""

    @pytest.fixture
    def admin_user(self):
        user = MagicMock()
        user.id = uuid4()
        user.role = "admin"
        return user

    @pytest.fixture
    def regular_user(self):
        user = MagicMock()
        user.id = uuid4()
        user.role = "user"
        return user

    @pytest.mark.asyncio
    async def test_get_pricing_status(self, admin_user):
        """GET /usage/pricing returns current pricing and last refresh."""
        from app.api.endpoints.usage import get_pricing_status

        result = await get_pricing_status(current_user=admin_user)
        assert "models" in result
        assert "last_refresh" in result
        # Should contain at least some model pricing entries
        assert len(result["models"]) > 0
        # Each entry should have input/output
        sample = next(iter(result["models"].values()))
        assert "input_per_1m" in sample
        assert "output_per_1m" in sample

    @pytest.mark.asyncio
    async def test_trigger_refresh_admin(self, admin_user):
        """POST /usage/pricing/refresh works for admin."""
        from app.api.endpoints.usage import trigger_pricing_refresh

        expected = PricingUpdateResult(
            success=True,
            checked_at="2026-02-18T03:00:00",
            models_checked=5,
            models_matched=4,
            models_updated=0,
        )
        mock_fn = AsyncMock(return_value=expected)
        with patch("app.services.pricing_update_service.refresh_model_pricing", mock_fn):
            result = await trigger_pricing_refresh(current_user=admin_user)

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_trigger_refresh_non_admin_forbidden(self, regular_user):
        """POST /usage/pricing/refresh rejects non-admin users."""
        from app.api.endpoints.usage import trigger_pricing_refresh
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await trigger_pricing_refresh(current_user=regular_user)

        assert exc_info.value.status_code == 403
