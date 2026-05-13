"""Public data models for the declarative query interface (F004).

``Query`` is what agents declare. ``QueryResult`` is what they get back.
``RetrievalPlan`` is the intermediate representation the planner emits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueryBudget:
    """Resource caps enforced per-call by ``execute()``."""

    max_artifacts: int = 20
    timeout_seconds: float = 10.0


@dataclass
class Query:
    """Declarative query from an agent.

    Fields:
        ask: natural-language question
        shape: JSON-compatible dict describing the desired response structure
        filters: entity IDs, date ranges, etc.
        contexts: which artifact_types to search (empty = all)
        ground: require citations in response
        budget: resource caps
    """

    ask: str
    shape: dict[str, Any]
    filters: dict[str, Any] = field(default_factory=dict)
    contexts: list[str] = field(default_factory=list)
    ground: bool = True
    budget: QueryBudget = field(default_factory=QueryBudget)


@dataclass
class RetrievalPlan:
    """Intermediate representation produced by the planner.

    Fields:
        artifact_types: which artifact types to query
        structured_filters: key-value filters passed directly to Postgres
        semantic_query: optional text for pgvector similarity search
        max_results: cap on fetched artifacts
    """

    artifact_types: list[str]
    structured_filters: dict[str, Any] = field(default_factory=dict)
    semantic_query: str | None = None
    max_results: int = 20


@dataclass
class Citation:
    """A lightweight citation threaded through from artifact field_citations."""

    field: str
    element_id: str
    verbatim_span: str
    doc_id: str
    confidence: float = 1.0


@dataclass
class QueryResult:
    """Shape-conformant, cited response returned by ``execute()``.

    Fields:
        data: conforms to ``query.shape``
        citations: flat list of citations for all populated fields
        artifacts_used: artifact_ids that contributed to this result
        latency_ms: wall time in milliseconds
        token_cost: total tokens consumed (composer only; planner may add LLM cost later)
    """

    data: dict[str, Any]
    citations: list[Citation]
    artifacts_used: list[str]
    latency_ms: int
    token_cost: int