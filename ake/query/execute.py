"""Query executor — orchestrates plan → fetch → compose with budget enforcement.

This is the single entry point agents call. It:
1. Plans: translates ``Query`` → ``RetrievalPlan`` (keyword-based planner)
2. Fetches: executes the plan against the artifact store (ACL-filtered via RLS)
3. Composes: reshapes artifacts into ``query.shape`` via direct mapping + optional LLM

Budget caps (``max_artifacts``, ``timeout_seconds``) are enforced per-call.
Each call receives a unique ``query_id``; the full execution trace is stored in
``_TRACE_STORE`` and retrievable via ``get_trace(query_id)``.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ake.config import Settings
from ake.config import settings as _default_settings
from ake.query.composer import compose
from ake.query.fetcher import fetch
from ake.query.interface import Query, QueryResult
from ake.query.planner import plan

logger = structlog.get_logger()

# In-memory trace store (keyed by query_id).  Production would persist to Postgres.
_TRACE_STORE: dict[str, dict[str, Any]] = {}

_MAX_TRACE_ENTRIES = 500  # evict oldest entries beyond this limit


def get_trace(query_id: str) -> dict[str, Any] | None:
    """Return the stored trace for a past query, or None if not found."""
    return _TRACE_STORE.get(query_id)


def _store_trace(entry: dict[str, Any]) -> None:
    qid = entry["query_id"]
    _TRACE_STORE[qid] = entry
    # Evict oldest entries to keep memory bounded.
    if len(_TRACE_STORE) > _MAX_TRACE_ENTRIES:
        oldest = next(iter(_TRACE_STORE))
        del _TRACE_STORE[oldest]


async def execute(
    query: Query,
    principal: str,
    session: AsyncSession,
    settings: Settings = _default_settings,
) -> QueryResult:
    """Execute a declarative query, returning a shape-conformant, cited result.

    ACL enforcement is handled by Postgres RLS: the caller's identity is set on
    ``app.current_principals`` before any query hits the artifacts table.

    Args:
        query: Declarative query (ask, shape, filters, budget).
        principal: Caller identity (e.g. user ID or role) — set as the sole
                   element of ``app.current_principals`` for RLS.
        session: Active async Postgres session. RLS principal is set on this
                 session before any query.
        settings: Application settings.

    Returns:
        A ``QueryResult`` with shape-conformant data, citations, and metadata.
        ``result.query_id`` can be passed to ``ake_get_trace`` for full details.
    """
    query_id = str(uuid.uuid4())
    t0 = time.monotonic()

    trace: dict[str, Any] = {
        "query_id": query_id,
        "ask": query.ask,
        "contexts": query.contexts,
        "filters": query.filters,
        "principal": principal,
        "status": "started",
        "artifacts_retrieved": 0,
        "elapsed_ms": 0,
    }

    # Set ACL principal on the session for RLS.
    # NOTE: SET does not support parameterised bind parameters;
    # we interpolate the controlled internal value directly.
    await session.execute(
        text(f"SET app.current_principals = '{principal}'"),
    )

    budget_seconds = query.budget.timeout_seconds

    try:
        # Phase 1: Plan
        retrieval_plan = plan(query)
        trace["artifact_types_planned"] = retrieval_plan.artifact_types

        # Phase 2: Fetch (with timeout enforcement)
        try:
            artifacts = await asyncio.wait_for(
                fetch(retrieval_plan, session),
                timeout=budget_seconds,
            )
        except asyncio.TimeoutError:
            elapsed = int((time.monotonic() - t0) * 1000)
            trace.update(status="timeout_fetch", elapsed_ms=elapsed)
            _store_trace(trace)
            logger.warning(
                "query_timeout",
                ask=query.ask[:80],
                principal=principal,
                timeout_s=budget_seconds,
                elapsed_ms=elapsed,
            )
            return QueryResult(
                data={},
                citations=[],
                artifacts_used=[],
                latency_ms=elapsed,
                token_cost=0,
                query_id=query_id,
            )

        trace["artifacts_retrieved"] = len(artifacts)

        # Enforce max_artifacts budget
        if len(artifacts) > query.budget.max_artifacts:
            artifacts = artifacts[: query.budget.max_artifacts]

        # Phase 3: Compose (direct mapping + optional LLM with remaining time budget)
        remaining = budget_seconds - (time.monotonic() - t0)
        if remaining <= 0:
            elapsed = int((time.monotonic() - t0) * 1000)
            trace.update(status="timeout_precompose", elapsed_ms=elapsed)
            _store_trace(trace)
            return QueryResult(
                data={},
                citations=[],
                artifacts_used=[a.artifact_id for a in artifacts],
                latency_ms=elapsed,
                token_cost=0,
                query_id=query_id,
            )

        try:
            result = await asyncio.wait_for(
                compose(query, artifacts, settings),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            elapsed = int((time.monotonic() - t0) * 1000)
            trace.update(status="timeout_compose", elapsed_ms=elapsed)
            _store_trace(trace)
            logger.warning(
                "composer_timeout",
                ask=query.ask[:80],
                artifacts_count=len(artifacts),
            )
            return QueryResult(
                data=_null_shape(query.shape),
                citations=[],
                artifacts_used=[a.artifact_id for a in artifacts],
                latency_ms=elapsed,
                token_cost=0,
                query_id=query_id,
            )

        elapsed = int((time.monotonic() - t0) * 1000)
        trace.update(
            status="ok",
            elapsed_ms=elapsed,
            token_cost=result.token_cost,
            citations_count=len(result.citations),
        )
        _store_trace(trace)

        result.query_id = query_id
        return result

    except Exception:
        elapsed = int((time.monotonic() - t0) * 1000)
        trace.update(status="error", elapsed_ms=elapsed)
        _store_trace(trace)
        logger.exception("query_execution_failed", ask=query.ask[:80])
        return QueryResult(
            data=_null_shape(query.shape),
            citations=[],
            artifacts_used=[],
            latency_ms=elapsed,
            token_cost=0,
            query_id=query_id,
        )


def _null_shape(shape: dict[str, Any]) -> dict[str, Any]:
    """Produce a null-filled dict matching the shape structure."""
    result: dict[str, Any] = {}
    for key, val in shape.items():
        if isinstance(val, dict):
            result[key] = _null_shape(val)
        elif isinstance(val, list):
            result[key] = []
        else:
            result[key] = None
    return result
