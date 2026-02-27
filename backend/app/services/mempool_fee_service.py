"""
Mempool Fee Estimation Service

Fetches recommended fee rates from the configured Mempool Explorer instance
and maps simple priority levels (low/medium/high) to sat/vByte fee rates.

API: GET {LND_MEMPOOL_URL}/api/v1/fees/recommended
Response: { fastestFee, halfHourFee, hourFee, economyFee, minimumFee }

Priority mapping:
  low    → hourFee     (~1 hour confirmation)
  medium → halfHourFee (~30 min confirmation)  [default]
  high   → fastestFee  (next block confirmation)
"""

import time
import logging
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Fee rate cache — Mempool fees don't change fast enough to need real-time queries
_fee_cache: Optional[dict] = None
_fee_cache_time: float = 0
_CACHE_TTL_SECONDS = 60  # Cache for 60 seconds


# Priority → Mempool field mapping
PRIORITY_MAP = {
    "low": "hourFee",
    "medium": "halfHourFee",
    "high": "fastestFee",
}

# Priority → LND target_conf fallback (blocks until confirmation)
PRIORITY_TARGET_CONF = {
    "low": 144,     # ~1 day
    "medium": 6,    # ~1 hour
    "high": 1,      # next block
}


class MempoolFeeService:
    """Fetches and caches recommended fee rates from Mempool Explorer."""

    def _get_base_url(self) -> str:
        """Get the Mempool API base URL from configuration."""
        return settings.lnd_mempool_url.rstrip("/")

    async def get_recommended_fees(self) -> Optional[dict]:
        """Fetch recommended fees from Mempool, with caching.

        Returns:
            {
                "fastestFee": int,    # sat/vB for next-block
                "halfHourFee": int,   # sat/vB for ~30 min
                "hourFee": int,       # sat/vB for ~1 hour
                "economyFee": int,    # sat/vB economy
                "minimumFee": int,    # minimum relay fee
            }
            or None if the request fails.
        """
        global _fee_cache, _fee_cache_time

        # Return cached result if fresh
        now = time.time()
        if _fee_cache and (now - _fee_cache_time) < _CACHE_TTL_SECONDS:
            return _fee_cache

        base_url = self._get_base_url()
        url = f"{base_url}/api/v1/fees/recommended"

        # Only skip TLS verification for self-hosted (non-public) Mempool instances
        is_public = "mempool.space" in base_url
        verify_tls = is_public  # True for public API, False for self-hosted

        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                verify=verify_tls,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()

            # Validate expected fields
            required = ["fastestFee", "halfHourFee", "hourFee", "economyFee", "minimumFee"]
            if not all(k in data for k in required):
                logger.warning("Mempool fee response missing fields: %s", data)
                return None

            # Cache the result
            _fee_cache = data
            _fee_cache_time = now
            logger.debug("Mempool fees cached: %s", data)
            return data

        except httpx.HTTPStatusError as e:
            logger.warning("Mempool fee API HTTP error: %s %s", e.response.status_code, url)
            return None
        except Exception as e:
            logger.warning("Mempool fee API unreachable (%s): %s", url, e)
            return None

    async def get_fee_for_priority(self, priority: str = "medium") -> Optional[int]:
        """Get the sat/vByte fee rate for a given priority level.

        Args:
            priority: "low", "medium", or "high"

        Returns:
            Fee rate in sat/vByte, or None if Mempool is unreachable.
        """
        priority = priority.lower()
        if priority not in PRIORITY_MAP:
            logger.warning("Unknown fee priority '%s', defaulting to medium", priority)
            priority = "medium"

        fees = await self.get_recommended_fees()
        if not fees:
            return None

        field = PRIORITY_MAP[priority]
        fee_rate = fees.get(field)

        # Ensure it's at least 1 sat/vB
        if fee_rate is not None and fee_rate < 1:
            fee_rate = 1

        return fee_rate

    def get_target_conf_for_priority(self, priority: str = "medium") -> int:
        """Get the LND target_conf (blocks) for a priority level.

        Used as fallback when Mempool is unreachable.

        Args:
            priority: "low", "medium", or "high"

        Returns:
            Target number of confirmations for LND fee estimation.
        """
        priority = priority.lower()
        return PRIORITY_TARGET_CONF.get(priority, 6)


# Singleton
mempool_fee_service = MempoolFeeService()
