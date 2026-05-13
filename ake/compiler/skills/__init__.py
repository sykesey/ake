"""Extraction skill library — reusable, tested primitives for artifact compilation.

All skills are deterministic Python; no LLM calls inside any skill.  The
compiler loop discovers available skills by inspecting this module.
"""
from __future__ import annotations

from ake.compiler.skills.extract_named_entities import NamedEntity, extract_named_entities
from ake.compiler.skills.extract_table import extract_table
from ake.compiler.skills.find_section import find_section
from ake.compiler.skills.locate_by_proximity import locate_by_proximity
from ake.compiler.skills.normalize_currency import normalize_currency
from ake.compiler.skills.normalize_date import normalize_date
from ake.compiler.skills.resolve_entity import resolve_entity

__all__ = [
    "NamedEntity",
    "extract_table",
    "find_section",
    "extract_named_entities",
    "normalize_currency",
    "normalize_date",
    "locate_by_proximity",
    "resolve_entity",
    "SKILL_REGISTRY",
]

# Registry of typed skill signatures — used by the compiler loop's refine prompt
# to show the LLM what skills are available without requiring Python inspection.
SKILL_REGISTRY: dict[str, str] = {
    "extract_table": (
        "extract_table(elements: list[Element], heading: str, *, "
        "case_sensitive: bool = False, fuzzy: bool = True) -> list[dict[str, str]]\n"
        "  Locate a table by heading text (substring match on title/header elements) "
        "and parse its body into list-of-dicts keyed by column header."
    ),
    "find_section": (
        "find_section(elements: list[Element], path: list[str]) -> list[Element]\n"
        "  Return elements whose section_path starts with the given prefix, "
        "e.g. ['Item 7', 'Capital Returns']."
    ),
    "extract_named_entities": (
        "extract_named_entities(text: str, types: list[str] | None = None) -> list[NamedEntity]\n"
        "  Extract ORG, DATE, CURRENCY, PERSON, PERCENT entities from a text span "
        "using regex.  Returns NamedEntity(kind, value, char_start, char_end)."
    ),
    "normalize_currency": (
        "normalize_currency(value_str: str) -> float | None\n"
        "  Normalize '$1.2B', '1,200 million', 'USD 1.2bn' → float millions. "
        "Handles billion/B/Bn, million/M/Mn, thousand/K, trillion/T suffixes. "
        "Supports $, €, £, ¥ and USD/EUR/GBP/AUD/CAD prefixes."
    ),
    "normalize_date": (
        "normalize_date(date_str: str) -> datetime.date | None\n"
        "  Parse 'FY2022', 'Q3 2025', '31 Dec 2023', 'Jan. 15, 2026', 'December 2024', "
        "'2024-03-15' → datetime.date.  Returns None for unparseable strings."
    ),
    "locate_by_proximity": (
        "locate_by_proximity(elements: list[Element], anchor_text: str, "
        "window: int = 3, *, case_sensitive: bool = False) -> list[Element]\n"
        "  Return elements within ±window of any element containing anchor_text. "
        "De-duplicates overlapping windows."
    ),
    "resolve_entity": (
        "resolve_entity(name: str, entity_registry: dict[str, str], *, "
        "case_sensitive: bool = False) -> str | None\n"
        "  Map a human-written entity mention to a canonical ID using fuzzy matching. "
        "Uses exact match → substring containment (longest key wins)."
    ),
}