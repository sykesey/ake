"""Query executor — orchestrates plan → fetch → compose with budget enforcement.

This is the single entry point agents call. It:
1. Plans: translates ``Query`` → ``RetrievalPlan`` (keyword-based planner)
2. Fetches: executes the plan against the artifact store (ACL-filtered via RLS)
3. Composes: reshapes artifacts into ``query.shape`` via a small LLM call

Budget caps (``max_artifacts``, ``timeout_seconds``) are enforced per-call.
"""
from __future__ import annotations

import asyncio
import time
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
    """
    t0 = time.monotonic()

    # Set ACL principal on the session for RLS.
    await session.execute(
        text("SET app.current_principals = :principals"),
        {"principals": principal},
    )

    budget_seconds = query.budget.timeout_seconds

    try:
        # Phase 1: Plan
        retrieval_plan = plan(query)

        # Phase 2: Fetch (with timeout enforcement)
        try:
            artifacts = await asyncio.wait_for(
                fetch(retrieval_plan, session),
                timeout=budget_seconds,
            )
        except asyncio.TimeoutError:
            elapsed = int((time.monotonic() - t0) * 1000)
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
            )

        # Enforce max_artifacts budget
        if len(artifacts) > query.budget.max_artifacts:
            artifacts = artifacts[: query.budget.max_artifacts]

        # Phase 3: Compose (LLM call with remaining time budget)
        remaining = budget_seconds - (time.monotonic() - t0)
        if remaining <= 0:
            elapsed = int((time.monotonic() - t0) * 1000)
            return QueryResult(
                data={},
                citations=[],
                artifacts_used=[a.artifact_id for a in artifacts],
                latency_ms=elapsed,
                token_cost=0,
            )

        try:
            return await asyncio.wait_for(
                compose(query, artifacts, settings),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            elapsed = int((time.monotonic() - t0) * 1000)
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
            )

    except Exception:
        elapsed = int((time.monotonic() - t0) * 1000)
        logger.exception("query_execution_failed", ask=query.ask[:80])
        return QueryResult(
            data=_null_shape(query.shape),
            citations=[],
            artifacts_used=[],
            latency_ms=elapsed,
            token_cost=0,
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