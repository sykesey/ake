"""Locate a table element by its preceding heading and return rows as dicts.

No LLM calls — deterministic Python operating on normalised Element records.
"""
from __future__ import annotations

from ake.ingestion.element import Element

# Characters that commonly delimit columns in unstructured table text.
_COLUMN_SEPARATORS: frozenset[str] = frozenset({"|", "\t"})
_HEADING_TYPES: frozenset[str] = frozenset({"title", "header"})


def extract_table(
    elements: list[Element],
    heading: str,
    *,
    case_sensitive: bool = False,
    fuzzy: bool = True,
) -> list[dict[str, str]]:
    """Return rows from the first table whose preceding heading matches ``heading``.

    How it works
    ------------
    1. Scan ``elements`` for the first title/header whose text contains
       *heading* (case-insensitive unless *case_sensitive* is True, and
       allows partial substring match unless *fuzzy* is False).
    2. From that heading onward, find the next ``type == "table"`` element.
    3. Parse that table's text into a list of ``{column: value}`` dicts.

    Args:
        elements: Normalised Element records from the ingestion layer.
        heading:  Human-readable heading text such as ``"Share Repurchases"``.
        case_sensitive: If True, heading match must be exact case.
        fuzzy:           If False, requires exact heading text match (not substring).

    Returns:
        List of dicts keyed by column header.  An empty list if no matching
        heading-table pair is found.
    """
    # ── 1. Locate the heading ──────────────────────────────────────────────
    anchor_idx: int | None = None
    search = heading if case_sensitive else heading.lower()

    for i, el in enumerate(elements):
        if el.type not in _HEADING_TYPES:
            continue
        candidate = el.text if case_sensitive else el.text.lower()
        if fuzzy:
            if search in candidate:
                anchor_idx = i
                break
        else:
            if candidate == search:
                anchor_idx = i
                break

    if anchor_idx is None:
        return []

    # ── 2. Find the next table element ────────────────────────────────────
    for el in elements[anchor_idx:]:
        if el.type == "table":
            return _parse_table_text(el.text)

    return []


def _parse_table_text(text: str) -> list[dict[str, str]]:
    """Parse a delimited-table text block into list-of-dicts."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        # Need at least header + one data row.
        return []

    # Detect separator — prefer '|', fall back to '\t' then whitespace guessing.
    sep = _detect_separator(lines)
    if sep is None:
        return []

    headers = [h.strip() for h in lines[0].split(sep)]
    if not headers or all(h == "" for h in headers):
        return []

    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        cells = [c.strip() for c in line.split(sep)]
        row: dict[str, str] = {}
        for idx, header in enumerate(headers):
            row[header] = cells[idx] if idx < len(cells) else ""
        rows.append(row)
    return rows


def _detect_separator(lines: list[str]) -> str | None:
    """Return the best column separator for a set of table lines, or None."""
    for sep in ("\t", "|"):
        if all(sep in line for line in lines):
            return sep

    # No consistent separator found — maybe the table is single-column.
    if all("\t" not in ln and "|" not in ln for ln in lines):
        # Treat the entire line as a single column.
        return "\x00"  # sentinel that won't appear in text

    return None