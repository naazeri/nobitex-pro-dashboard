"""
Strict Iran Standard Time (IRST = UTC+03:30, no DST) utilities per ADR-010.

All calendar day boundaries, displayed timestamps, and SQLite metadata
timestamps across nob-api must use these helpers rather than OS-local
datetime.now() or zoneinfo/DST-dependent conversions.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, Union

# Strict UTC+03:30 without DST (Iran abolished DST in September 2022 / Shahrivar 1401)
IRST = timezone(timedelta(hours=3, minutes=30), name="IRST")


def now_irst() -> datetime:
    """Return current timezone-aware datetime in IRST (UTC+03:30)."""
    return datetime.now(IRST)


def today_irst_str() -> str:
    """Return today's date string (YYYY-MM-DD) in IRST (UTC+03:30)."""
    return now_irst().strftime("%Y-%m-%d")


def ts_to_irst_date_str(ts: Union[int, float]) -> str:
    """
    Convert a Unix timestamp (seconds or milliseconds) to YYYY-MM-DD in IRST.
    Automatically normalizes millisecond timestamps (> 1e11) to seconds.
    """
    val = float(ts)
    if val > 1e11:
        val /= 1000.0
    return datetime.fromtimestamp(val, tz=IRST).strftime("%Y-%m-%d")


def iso_to_irst_date_str(timestamp_str: Optional[str]) -> str:
    """
    Convert an ISO-8601 timestamp string (assumed UTC if naive) to YYYY-MM-DD in IRST.
    Returns empty string for None/empty inputs; falls back to first 10 chars if unparseable.
    """
    if not timestamp_str or len(timestamp_str) < 10:
        return ""
    s = timestamp_str.strip()
    try:
        clean_ts = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(IRST).strftime("%Y-%m-%d")
    except Exception:
        return s[:10]


def irst_date_bounds_to_utc_iso(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Convert IRST calendar date boundaries (YYYY-MM-DD or ISO-8601) into UTC ISO-8601
    strings suitable for lexicographical comparison against UTC ISO timestamps in SQLite
    (e.g., '2026-05-22T17:52:53.205501+00:00').

    - Date-only from_date ('YYYY-MM-DD') -> 'YYYY-MM-DD 00:00:00+03:30' -> UTC ISO
      (formatted without microseconds so '+' < '.' matches both with/without micros).
    - Date-only to_date ('YYYY-MM-DD') -> 'YYYY-MM-DD 23:59:59.999999+03:30' -> UTC ISO.
    - Timezone-aware datetimes are converted directly to UTC.
    - Naive datetimes (len > 10 without offset) are interpreted as IRST.
    """
    utc_from: Optional[str] = None
    utc_to: Optional[str] = None

    if from_date and str(from_date).strip():
        s = str(from_date).strip()
        try:
            if len(s) == 10:
                dt = datetime.fromisoformat(f"{s}T00:00:00").replace(tzinfo=IRST)
            else:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=IRST)
            utc_from = dt.astimezone(timezone.utc).isoformat()
        except Exception:
            utc_from = s

    if to_date and str(to_date).strip():
        s = str(to_date).strip()
        try:
            if len(s) == 10:
                dt = datetime.fromisoformat(f"{s}T23:59:59.999999").replace(tzinfo=IRST)
            else:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=IRST)
            utc_to = dt.astimezone(timezone.utc).isoformat()
        except Exception:
            utc_to = s if len(s) > 10 else f"{s}T23:59:59.999999+00:00"

    return utc_from, utc_to
