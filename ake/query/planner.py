"""Query planner — translates a declarative ``Query`` into a ``RetrievalPlan``.

Starts with keyword matching (ADR-001: compile at ingest, not query time).
Upgrade to LLM-based routing only when eval recall targets are missed.
"""
from __future__ import annotations

import re

from ake.query.interface import Query, RetrievalPlan


def plan(query: Query) -> RetrievalPlan:
    """Translate a declarative Query into a RetrievalPlan via keyword matching.

    Strategy:
    1. If ``query.contexts`` is non-empty, use those artifact types directly.
    2. Otherwise, infer artifact types by matching words in ``query.ask``
       and ``query.shape`` keys against known artifact type keywords.
    3. Promote known structured filter keys from ``query.filters`` (entity_id,
       fiscal_year, etc.) into the plan's structured_filters.
    4. If no structured filters are usable, treat ``query.ask`` as a
       semantic query for pgvector lookup.
    """
    artifact_types: list[str]
    if query.contexts:
        artifact_types = list(query.contexts)
    else:
        artifact_types = _infer_artifact_types(query)

    structured_filters: dict[str, str | int | float] = {}
    for key in ("entity_id", "artifact_type", "fiscal_year"):
        if key in query.filters:
            structured_filters[key] = query.filters[key]

    # If we have structured filters we can do a direct Postgres lookup.
    # If contexts were explicitly provided, do a broad fetch (all artifacts of
    # those types) and let the composer answer the NL question — using the full
    # question string as an ILIKE pattern against payload text never matches.
    # Only fall back to text search when types were inferred and nothing else
    # constrains the query.
    semantic_query: str | None = None
    if not structured_filters and not query.contexts:
        semantic_query = query.ask

    return RetrievalPlan(
        artifact_types=artifact_types,
        structured_filters=structured_filters,
        semantic_query=semantic_query,
        max_results=query.budget.max_artifacts,
    )


# ── keyword-based type inference ──────────────────────────────────────────────

# Map lowercase tokens → artifact_type hints.
# Extended by registering domain schemas (Layer 4).
_KEYWORD_MAP: dict[str, list[str]] = {
    "financial": ["financials_10k", "financials"],
    "revenue": ["financials_10k", "financials"],
    "income": ["financials_10k", "financials"],
    "eps": ["financials_10k", "financials"],
    "balance": ["financials_10k", "financials"],
    "contract": ["contract_terms"],
    "obligation": ["contract_terms"],
    "term": ["contract_terms"],
    "board": ["board_members"],
    "director": ["board_members"],
    "executive": ["board_members"],
    "compensation": ["executive_comp"],
    "risk": ["risk_factors"],
    "litigation": ["legal_proceedings"],
    "ip": ["ip_portfolio", "patents"],
    "patent": ["ip_portfolio", "patents"],
    "subsidiary": ["subsidiaries"],
    "compliance": ["compliance", "regulatory"],
}


def _tokenize(text: str) -> set[str]:
    """Lowercase alpha tokens."""
    return set(re.findall(r"[a-z]+", text.lower()))


def _infer_artifact_types(query: Query) -> list[str]:
    """Score artifact types by keyword overlap with ask + shape keys."""
    tokens = _tokenize(query.ask)
    for key in query.shape:
        tokens.update(_tokenize(str(key)))

    scores: dict[str, int] = {}
    for token in tokens:
        for artifact_type in _KEYWORD_MAP.get(token, []):
            scores[artifact_type] = scores.get(artifact_type, 0) + 1

    if not scores:
        return []

    max_score = max(scores.values())
    # Return all types that match at the highest score tier.
    return [t for t, s in scores.items() if s == max_score]