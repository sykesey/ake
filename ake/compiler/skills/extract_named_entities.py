"""Extract named entities (ORGs, dates, currencies, persons, percentages) from text.

No LLM calls — deterministic regex-based extraction for common entity types.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

EntityKind = Literal["ORG", "DATE", "CURRENCY", "PERSON", "PERCENT"]


@dataclass(frozen=True)
class NamedEntity:
    """A single named entity extracted from a text span."""

    kind: EntityKind
    value: str
    char_start: int
    char_end: int


# ── Regex patterns ──────────────────────────────────────────────────────────

# Currency: $1.2B, USD 100M, €500 million, £1,200, AUD 450.5
_CURRENCY_RE = re.compile(
    r"""(?ix)
    (?:USD|EUR|GBP|AUD|CAD|JPY|CHF|CNY|INR|\$|€|£|¥)\s*
    (?:\d{1,3}(?:,\d{3})*|(?:\d+))(?:\.\d+)?\s*
    (?:billion|b|bn|million|m|mn|thousand|k|trillion|t)?
    """,
)

# Date: 31 Dec 2023, FY2022, Q3 2025, 2024-03-15, Jan. 15, 2026, December 2024
_DATE_RE = re.compile(
    r"""(?ix)
    (?:
        FY\d{2,4}                                                |
        Q[1-4]\s*\d{4}                                            |
        \d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4} |
        (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4} |
        (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}        |
        \d{4}-\d{2}-\d{2}
    )
    """,
)

# Org: uppercase sequences of 2+ letters, common suffixes like Inc/Corp/Ltd/LLC
_ORG_RE = re.compile(
    r"""(?x)
    \b(?:
        [A-Z][a-z]*(?:\s(?:&|and)\s[A-Z][a-z]*)*\s
        (?:Inc\.?|Corp\.?|Corporation|Ltd\.?|LLC|PLC|Limited|Group|Holdings|Co\.?)
    )\b
    """,
)

# Percent: 15%, 3.2 percent, 42 pct
_PERCENT_RE = re.compile(
    r"""(?ix)
    \d{1,3}(?:\.\d+)?\s*(?:%|percent|pct)
    """,
)

# Person: two capitalized words (simplistic)
_PERSON_RE = re.compile(r"\b[A-Z][a-z]+\s[A-Z][a-z]+\b")


# ── Public API ──────────────────────────────────────────────────────────────


def extract_named_entities(
    text: str,
    types: list[EntityKind] | None = None,
) -> list[NamedEntity]:
    """Extract entities of the given kinds from *text*.

    Args:
        text:  The text span to scan.
        types: Entity kinds to extract.  If None or empty, extract all kinds.

    Returns:
        List of :class:`NamedEntity` records, in order of appearance.
    """
    if not types:
        types = ["ORG", "DATE", "CURRENCY", "PERSON", "PERCENT"]

    entities: list[NamedEntity] = []

    if "CURRENCY" in types:
        for m in _CURRENCY_RE.finditer(text):
            entities.append(
                NamedEntity(
                    kind="CURRENCY",
                    value=m.group(0).strip(),
                    char_start=m.start(),
                    char_end=m.end(),
                )
            )

    if "DATE" in types:
        for m in _DATE_RE.finditer(text):
            entities.append(
                NamedEntity(
                    kind="DATE",
                    value=m.group(0).strip(),
                    char_start=m.start(),
                    char_end=m.end(),
                )
            )

    if "ORG" in types:
        for m in _ORG_RE.finditer(text):
            entities.append(
                NamedEntity(
                    kind="ORG",
                    value=m.group(0).strip(),
                    char_start=m.start(),
                    char_end=m.end(),
                )
            )

    if "PERCENT" in types:
        for m in _PERCENT_RE.finditer(text):
            entities.append(
                NamedEntity(
                    kind="PERCENT",
                    value=m.group(0).strip(),
                    char_start=m.start(),
                    char_end=m.end(),
                )
            )

    if "PERSON" in types:
        for m in _PERSON_RE.finditer(text):
            entities.append(
                NamedEntity(
                    kind="PERSON",
                    value=m.group(0).strip(),
                    char_start=m.start(),
                    char_end=m.end(),
                )
            )

    # Sort by position in text.
    entities.sort(key=lambda e: e.char_start)
    return entities