"""Normalize date expressions → ``datetime.date``.

No LLM calls — deterministic regex-based parsing of common date formats.

Handles:
- ``"FY2022"`` → date(2022, 1, 1)
- ``"31 Dec 2023"`` → date(2023, 12, 31)
- ``"Q3 2025"`` → date(2025, 7, 1)
- ``"December 2024"`` → date(2024, 12, 1)
- ``"2024-03-15"`` → date(2024, 3, 15)
- ``"Jan. 15, 2026"`` → date(2026, 1, 15)
- ``"15 Jan 2026"`` → date(2026, 1, 15)
"""
from __future__ import annotations

import datetime
import re
from typing import Optional

_MONTH_MAP: dict[str, int] = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_QUARTER_START_MONTH: dict[int, int] = {1: 1, 2: 4, 3: 7, 4: 10}

# ISO: 2024-03-15
_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")

# Fiscal year: FY2022, FY22
_FY_RE = re.compile(r"^FY\s*(\d{2,4})$", re.IGNORECASE)

# Quarter: Q3 2025, Q1 2024
_Q_RE = re.compile(r"^Q([1-4])\s*(\d{4})$", re.IGNORECASE)

# Day Month Year: 31 Dec 2023, 15 Jan 2026
_DMY_RE = re.compile(r"^(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{2,4})$", re.IGNORECASE)

# Month Day Comma Year: Jan. 15, 2026, December 31, 2024
_MDY_RE = re.compile(
    r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})$",
    re.IGNORECASE,
)

# Month Year: December 2024
_MY_RE = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{4})$", re.IGNORECASE)


def normalize_date(date_str: str) -> Optional[datetime.date]:
    """Parse a human-written date expression and return a ``datetime.date``.

    Args:
        date_str: A date expression e.g. ``"FY2022"``, ``"31 Dec 2023"``.

    Returns:
        A ``datetime.date``, or *None* if the string cannot be parsed.
    """
    stripped = date_str.strip()

    # ISO format: 2024-03-15
    m = _ISO_RE.match(stripped)
    if m:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # Fiscal year: FY2022
    m = _FY_RE.match(stripped)
    if m:
        year_str = m.group(1)
        if len(year_str) == 2:
            year = 2000 + int(year_str)
        else:
            year = int(year_str)
        return datetime.date(year, 1, 1)

    # Quarter: Q3 2025
    m = _Q_RE.match(stripped)
    if m:
        quarter = int(m.group(1))
        year = int(m.group(2))
        month = _QUARTER_START_MONTH[quarter]
        return datetime.date(year, month, 1)

    # Day Month Year: 31 Dec 2023
    m = _DMY_RE.match(stripped)
    if m:
        day = int(m.group(1))
        month = _MONTH_MAP[m.group(2)[:3].lower()]
        year = _normalize_year(m.group(3))
        return datetime.date(year, month, day)

    # Month Day Comma Year: Jan. 15, 2026
    m = _MDY_RE.match(stripped)
    if m:
        month = _MONTH_MAP[m.group(1)[:3].lower()]
        day = int(m.group(2))
        year = int(m.group(3))
        return datetime.date(year, month, day)

    # Month Year: December 2024
    m = _MY_RE.match(stripped)
    if m:
        month = _MONTH_MAP[m.group(1)[:3].lower()]
        year = int(m.group(2))
        return datetime.date(year, month, 1)

    return None


def _normalize_year(year_str: str) -> int:
    """Convert a 2-digit or 4-digit year string to int."""
    if len(year_str) == 2:
        return 2000 + int(year_str)
    return int(year_str)