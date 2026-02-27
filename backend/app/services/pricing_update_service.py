"""
Pricing Update Service — Keeps LLM model pricing accurate automatically.

Fetches current per-token pricing from OpenRouter's public API (free, no auth)
which covers Anthropic and OpenAI models.  Z.ai/GLM and Ollama models are
skipped (GLM has no public pricing API; Ollama is always free).

Runs:
  • On application startup  (via startup_service.initialize_on_startup)
  • Daily at 03:00 UTC      (via Celery beat task)
  • On-demand                (via POST /usage/pricing/refresh)

Updates the in-memory ``MODEL_PRICING`` dict in ``usage_service.py`` so that
all subsequent cost calculations use the latest prices.  Changes are logged
at WARNING level so admins notice price movements.

OpenRouter API docs: https://openrouter.ai/docs/api-reference/list-available-models
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import httpx

from app.core.datetime_utils import utc_now

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenRouter API
# ---------------------------------------------------------------------------
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/models"
REQUEST_TIMEOUT = 30  # seconds

# SGA3-L1: Validate the hardcoded URL at import time to prevent SSRF if this
# constant is ever changed to a configurable value.
from app.core.security import validate_target_url as _validate_url
_validate_url(OPENROUTER_API_URL)

# ---------------------------------------------------------------------------
# Provider prefixes used for routing in OpenRouter IDs
# ---------------------------------------------------------------------------
_PROVIDER_PREFIXES = ("anthropic/", "openai/", "google/", "meta-llama/", "mistralai/")

# ---------------------------------------------------------------------------
# Explicit mapping: our MODEL_PRICING key → OpenRouter model ID.
# Only models that the user is actually billed for need to be here.
# Free models (Ollama, GLM flash) and Z.ai models (no OpenRouter listing)
# are intentionally excluded.
# ---------------------------------------------------------------------------
_OUR_KEY_TO_OPENROUTER: Dict[str, str] = {
    # --- Anthropic -----------------------------------------------------------
    "claude-opus-4-6":      "anthropic/claude-opus-4-6",
    "claude-opus-4-5":      "anthropic/claude-opus-4-5",
    "claude-opus-4-1":      "anthropic/claude-opus-4-1",
    "claude-opus-4":        "anthropic/claude-opus-4",
    "claude-sonnet-4-6":    "anthropic/claude-sonnet-4-6",
    "claude-sonnet-4-5":    "anthropic/claude-sonnet-4-5",
    "claude-sonnet-4":      "anthropic/claude-sonnet-4",
    "claude-haiku-4-5":     "anthropic/claude-haiku-4-5",
    "claude-3-5-haiku":     "anthropic/claude-3.5-haiku",
    "claude-3-7-sonnet":    "anthropic/claude-3.7-sonnet",
    "claude-3-5-sonnet":    "anthropic/claude-3.5-sonnet",
    "claude-3-opus":        "anthropic/claude-3-opus",
    "claude-3-sonnet":      "anthropic/claude-3-sonnet",
    "claude-3-haiku":       "anthropic/claude-3-haiku",
    # --- OpenAI --------------------------------------------------------------
    "gpt-5.2":              "openai/gpt-5.2",
    "gpt-5.1":              "openai/gpt-5.1",
    "gpt-5-mini":           "openai/gpt-5-mini",
    "gpt-5-nano":           "openai/gpt-5-nano",
    "gpt-5":                "openai/gpt-5",
    "gpt-4.1-mini":         "openai/gpt-4.1-mini",
    "gpt-4.1-nano":         "openai/gpt-4.1-nano",
    "gpt-4.1":              "openai/gpt-4.1",
    "gpt-4o-mini":          "openai/gpt-4o-mini",
    "gpt-4o":               "openai/gpt-4o",
    "o4-mini":              "openai/o4-mini",
    "o3-pro":               "openai/o3-pro",
    "o3-mini":              "openai/o3-mini",
    "o3":                   "openai/o3",
    "o1-preview":           "openai/o1-preview",
    "o1-mini":              "openai/o1-mini",
    "o1":                   "openai/o1",
    "gpt-4-turbo":          "openai/gpt-4-turbo",
    "gpt-4":                "openai/gpt-4",
    "gpt-3.5-turbo":        "openai/gpt-3.5-turbo",
}

# Reverse index: openrouter_id → our_key  (built once at import time)
_OPENROUTER_TO_OUR_KEY: Dict[str, str] = {v: k for k, v in _OUR_KEY_TO_OPENROUTER.items()}


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class PriceChange:
    """Records a single price change detected for a model."""
    model_key: str
    old_input: float
    old_output: float
    new_input: float
    new_output: float

    @property
    def input_delta(self) -> float:
        return round(self.new_input - self.old_input, 4)

    @property
    def output_delta(self) -> float:
        return round(self.new_output - self.old_output, 4)

    def __str__(self) -> str:
        return (
            f"{self.model_key}: "
            f"input ${self.old_input:.2f}→${self.new_input:.2f} "
            f"(Δ{self.input_delta:+.2f}), "
            f"output ${self.old_output:.2f}→${self.new_output:.2f} "
            f"(Δ{self.output_delta:+.2f})"
        )


@dataclass
class PricingUpdateResult:
    """Summary of a pricing update run."""
    success: bool = True
    checked_at: str = ""
    models_checked: int = 0
    models_matched: int = 0
    models_updated: int = 0
    models_not_found: List[str] = field(default_factory=list)
    changes: List[PriceChange] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "checked_at": self.checked_at,
            "models_checked": self.models_checked,
            "models_matched": self.models_matched,
            "models_updated": self.models_updated,
            "models_not_found": self.models_not_found,
            "changes": [
                {
                    "model": c.model_key,
                    "old": {"input_per_1m": c.old_input, "output_per_1m": c.old_output},
                    "new": {"input_per_1m": c.new_input, "output_per_1m": c.new_output},
                }
                for c in self.changes
            ],
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# Module-level state — stores the most recent update result
# ---------------------------------------------------------------------------
_last_result: Optional[PricingUpdateResult] = None


def get_last_pricing_update() -> Optional[PricingUpdateResult]:
    """Return the result of the most recent pricing update (or None)."""
    return _last_result


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _per_token_to_per_million(per_token_str: str) -> float:
    """Convert OpenRouter per-token price string to per-1M-tokens float.

    OpenRouter returns prices as *strings* (e.g. ``"0.000003"``).
    We multiply by 1 000 000 and round to 4 decimals.
    """
    try:
        val = float(per_token_str)
    except (TypeError, ValueError):
        return 0.0
    return round(val * 1_000_000, 4)


async def _fetch_openrouter_models() -> Dict[str, Tuple[float, float]]:
    """Fetch model pricing from OpenRouter.

    Returns:
        Dict mapping OpenRouter model ID → (input_per_1M, output_per_1M).
    """
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.get(OPENROUTER_API_URL)
        resp.raise_for_status()
        data = resp.json()

    models: Dict[str, Tuple[float, float]] = {}
    for entry in data.get("data", []):
        model_id = entry.get("id", "")
        pricing = entry.get("pricing")
        if not pricing:
            continue
        input_price = _per_token_to_per_million(pricing.get("prompt", "0"))
        output_price = _per_token_to_per_million(pricing.get("completion", "0"))
        models[model_id] = (input_price, output_price)

    return models


def _match_and_update(
    openrouter_prices: Dict[str, Tuple[float, float]],
) -> PricingUpdateResult:
    """Compare OpenRouter prices with our MODEL_PRICING and apply updates.

    Updates are applied in-place to the module-level ``MODEL_PRICING`` dict
    in ``usage_service`` so they take effect immediately.
    """
    from app.services.usage_service import MODEL_PRICING

    result = PricingUpdateResult(
        checked_at=utc_now().isoformat(),
        models_checked=len(_OUR_KEY_TO_OPENROUTER),
    )

    for our_key, or_id in _OUR_KEY_TO_OPENROUTER.items():
        or_price = openrouter_prices.get(or_id)

        if or_price is None:
            # Try flexible matching: OpenRouter may add date suffixes
            # e.g. "anthropic/claude-opus-4-6-20261001"
            # Only match if the suffix is a date pattern to avoid false positives
            # (e.g. "openai/gpt-4o-mini" must NOT match "openai/gpt-4o" or "openai/gpt-4")
            for full_id, price in openrouter_prices.items():
                if full_id.startswith(or_id):
                    suffix = full_id[len(or_id):]
                    if suffix == "" or re.match(r"^-\d{8}$", suffix):
                        or_price = price
                        break

        if or_price is None:
            result.models_not_found.append(our_key)
            continue

        result.models_matched += 1
        new_input, new_output = or_price

        # Skip if new pricing is (0, 0) for a model that should cost money.
        # OpenRouter occasionally returns 0 for models in beta or during outages.
        current = MODEL_PRICING.get(our_key, (0.0, 0.0))
        if new_input == 0.0 and new_output == 0.0 and (current[0] > 0 or current[1] > 0):
            logger.debug(
                "Skipping zero pricing from OpenRouter for %s (keeping %s)",
                our_key,
                current,
            )
            continue

        # SA-17: Reject implausible price spikes (>10x current price).
        # Protects against corrupted API responses or upstream data errors.
        MAX_PRICE_MULTIPLIER = 10.0
        old_max = max(current[0], current[1])
        new_max = max(new_input, new_output)
        if old_max > 0 and new_max > old_max * MAX_PRICE_MULTIPLIER:
            logger.warning(
                "Rejecting implausible price spike for %s: "
                "current=(%s, %s) → new=(%s, %s) (>%sx increase)",
                our_key, current[0], current[1], new_input, new_output,
                MAX_PRICE_MULTIPLIER,
            )
            continue

        old_input, old_output = current

        # Check if price has changed (use small epsilon for float comparison)
        if abs(old_input - new_input) > 0.005 or abs(old_output - new_output) > 0.005:
            change = PriceChange(
                model_key=our_key,
                old_input=old_input,
                old_output=old_output,
                new_input=new_input,
                new_output=new_output,
            )
            result.changes.append(change)
            result.models_updated += 1
            MODEL_PRICING[our_key] = (new_input, new_output)
            logger.warning("Pricing updated — %s", change)

    return result


async def refresh_model_pricing() -> PricingUpdateResult:
    """Fetch latest pricing from OpenRouter and update MODEL_PRICING.

    This is the main entry point called from:
      - startup_service.initialize_on_startup()
      - Celery beat daily task
      - POST /usage/pricing/refresh endpoint

    Safe to call from any context — catches all errors and returns a result.
    """
    global _last_result

    logger.info("Refreshing LLM model pricing from OpenRouter...")

    try:
        openrouter_prices = await _fetch_openrouter_models()
        logger.info(
            "Fetched %d models from OpenRouter", len(openrouter_prices)
        )

        result = _match_and_update(openrouter_prices)

        if result.models_updated > 0:
            logger.warning(
                "Pricing refresh complete: %d model(s) updated out of %d checked",
                result.models_updated,
                result.models_checked,
            )
        else:
            logger.info(
                "Pricing refresh complete: all %d matched model(s) up to date "
                "(%d not found on OpenRouter)",
                result.models_matched,
                len(result.models_not_found),
            )

        _last_result = result
        return result

    except httpx.HTTPStatusError as e:
        msg = f"OpenRouter API error: {e.response.status_code} {e.response.text[:200]}"
        logger.error("Pricing refresh failed: %s", msg)
        result = PricingUpdateResult(
            success=False,
            checked_at=utc_now().isoformat(),
            error=msg,
        )
        _last_result = result
        return result

    except (httpx.RequestError, Exception) as e:
        msg = f"Pricing refresh failed: {type(e).__name__}: {e}"
        logger.error(msg)
        result = PricingUpdateResult(
            success=False,
            checked_at=utc_now().isoformat(),
            error=str(e),
        )
        _last_result = result
        return result
