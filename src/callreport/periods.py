"""Quarter-end report dates."""

from __future__ import annotations

import re
from datetime import date

QUARTER_PATTERN = re.compile(r"^(\d{4})[Qq]([1-4])$")
QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def parse_quarter(text: str) -> date:
    """Parse ``2012Q1`` or ``2012-03-31`` into a quarter-end date."""
    text = text.strip()
    match = QUARTER_PATTERN.match(text)
    if match:
        year, quarter = int(match.group(1)), int(match.group(2))
        return date(year, *QUARTER_END[quarter])
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"Invalid period {text!r}; use YYYYQn (e.g. 2012Q1) or YYYY-MM-DD") from None
    if (parsed.month, parsed.day) not in QUARTER_END.values():
        raise ValueError(f"{text} is not a quarter-end date")
    return parsed


def select_periods(available: list[date], start: str, end: str) -> list[date]:
    """Available report dates within ``[start, end]``, newest first.

    ``end`` may be ``"latest"`` for the newest period FFIEC has published.
    """
    if not available:
        raise ValueError("FFIEC returned no reporting periods")
    first = parse_quarter(start)
    last = max(available) if end.strip().lower() == "latest" else parse_quarter(end)
    if first > last:
        raise ValueError(f"Start period {first} is after end period {last}")
    selected = sorted((p for p in available if first <= p <= last), reverse=True)
    if not selected:
        raise ValueError(f"No published periods between {first} and {last}")
    return selected
