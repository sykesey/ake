"""Normalize varied currency expressions → float millions.

No LLM calls — deterministic regex-based parsing of currency strings.

Edge cases covered (per F006):
- ``"$1.2B"`` → 1200.0
- ``"1,200 million"`` → 1200.0
- ``"USD 1.2bn"`` → 1200.0
- ``"$0.85"`` → 0.00000085
- ``"€500M"`` → 500.0
"""
from __future__ import annotations

import re
from typing import Optional

# Map scale suffixes to their multiplier (relative to base units, not millions).
_SCALES: dict[str, float] = {
    "trillion": 1_000_000_000_000,
    "t": 1_000_000_000_000,
    "billion": 1_000_000_000,
    "b": 1_000_000_000,
    "bn": 1_000_000_000,
    "million": 1_000_000,
    "m": 1_000_000,
    "mn": 1_000_000,
    "k": 1_000,
    "thousand": 1_000,
}

# Regex to capture the numeric value, plus an optional scale suffix.
_CURRENCY_PARSE_RE = re.compile(
    r"""(?ix)^
    (?:USD|EUR|GBP|AUD|CAD|JPY|CHF|CNY|INR|\$|€|£|¥)?\s*
    (-?(?:\d{1,3}(?:,\d{3})*|(?:\d+))(?:\.\d+)?)\s*
    (trillion|t|billion|b|bn|million|m|mn|thousand|k)?
    $
    """,
)

# For the millions divisor.
_ONE_MILLION: float = 1_000_000.0


def normalize_currency(value_str: str) -> Optional[float]:
    """Parse a currency expression and return the value in millions.

    Args:
        value_str: A human-written currency string e.g. ``"$1.2B"``.

    Returns:
        Float value in millions, or *None* if the string cannot be parsed.
    """
    stripped = value_str.strip().replace(",", "")
    m = _CURRENCY_PARSE_RE.match(stripped)
    if m is None:
        return None

    number = float(m.group(1))
    scale_word = (m.group(2) or "").lower()
    multiplier = _SCALES.get(scale_word, 1.0)

    absolute = number * multiplier
    return absolute / _ONE_MILLION