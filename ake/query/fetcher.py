"""Fetcher — executes a ``RetrievalPlan`` against the artifact store.

Structured lookup uses promoted columns (entity_id, artifact_type, fiscal_year)
via Postgres. Semantic lookup via pgvector is wired as future capability —
the current artifacts table does not have an embedding column (per 0003_artifacts).

All queries are ACL-filtered by Postgres RLS; the caller must set
``app.current_principals`` on the session before calling this fetcher.
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from ake.compiler.artifact import DomainArtifact
from ake.query.interface import RetrievalPlan
from ake.store.artifact_store import artifacts_table, _row_to_artifact


async def fetch(
    plan: RetrievalPlan,
    session: AsyncSession,
) -> list[DomainArtifact]:
    """Execute ``plan`` against the artifact store.

    Returns artifacts matching the plan, ACL-filtered by Postgres RLS.
    If ``plan.semantic_query`` is set and no structured filters are usable,
    a keyword text search over ``payload`` is used as a fallback.
    """
    artifacts: list[DomainArtifact] = []

    if plan.structured_filters:
        artifacts = await _structured_fetch(plan, session)
    elif plan.semantic_query:
        artifacts = await _text_search_fetch(plan, session)
    else:
        artifacts = await _broad_fetch(plan, session)

    return artifacts


async def _structured_fetch(
    plan: RetrievalPlan,
    session: AsyncSession,
) -> list[DomainArtifact]:
    """Lookup by promoted columns."""
    conditions: list[sa.ColumnElement[bool]] = []

    if "entity_id" in plan.structured_filters:
        conditions.append(
            artifacts_table.c.entity_id == plan.structured_filters["entity_id"]
        )
    if "artifact_type" in plan.structured_filters:
        conditions.append(
            artifacts_table.c.artifact_type == plan.structured_filters["artifact_type"]
        )
    if "fiscal_year" in plan.structured_filters:
        conditions.append(
            artifacts_table.c.fiscal_year == plan.structured_filters["fiscal_year"]
        )

    # Also filter by plan.artifact_types if specified.
    if plan.artifact_types:
        conditions.append(
            artifacts_table.c.artifact_type.in_(plan.artifact_types)
        )

    query = (
        sa.select(artifacts_table)
        .where(sa.and_(*conditions))
        .limit(plan.max_results)
    )
    result = await session.execute(query)
    return [_row_to_artifact(row) for row in result.fetchall()]


async def _text_search_fetch(
    plan: RetrievalPlan,
    session: AsyncSession,
) -> list[DomainArtifact]:
    """Fallback text search over JSONB payload when no structured filters exist."""
    conditions: list[sa.ColumnElement[bool]] = []

    if plan.semantic_query:
        # Basic ILIKE against payload text representation.
        # Upgraded to pgvector similarity when embedding column is added.
        pattern = f"%{plan.semantic_query}%"
        conditions.append(
            sa.cast(artifacts_table.c.payload, sa.Text).ilike(pattern)
        )

    if plan.artifact_types:
        conditions.append(
            artifacts_table.c.artifact_type.in_(plan.artifact_types)
        )

    query = (
        sa.select(artifacts_table)
        .where(sa.and_(*conditions) if conditions else sa.true())
        .limit(plan.max_results)
    )
    result = await session.execute(query)
    return [_row_to_artifact(row) for row in result.fetchall()]


async def _broad_fetch(
    plan: RetrievalPlan,
    session: AsyncSession,
) -> list[DomainArtifact]:
    """Fetch with only artifact_type filtering (no filters, no semantic query)."""
    conditions: list[sa.ColumnElement[bool]] = []

    if plan.artifact_types:
        conditions.append(
            artifacts_table.c.artifact_type.in_(plan.artifact_types)
        )

    query = (
        sa.select(artifacts_table)
        .where(sa.and_(*conditions) if conditions else sa.true())
        .limit(plan.max_results)
    )
    result = await session.execute(query)
    return [_row_to_artifact(row) for row in result.fetchall()]