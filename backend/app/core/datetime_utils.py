"""Timezone-aware datetime utilities.

All datetimes in this project should be timezone-aware (UTC).
Use these helpers instead of datetime.utcnow() which is deprecated
in Python 3.12+ and produces naive (timezone-unaware) datetimes.
"""
from datetime import datetime, timezone
from typing import Optional


def utc_now() -> datetime:
    """Return current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime is timezone-aware (UTC).

    Handles naive datetimes that may come from older DB records or
    code paths still using datetime.utcnow(). Returns None if input is None.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
