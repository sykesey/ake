"""Postgres persistence for compiled DomainArtifact records (F002)."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timezone
from typing import Any

import sqlalchemy as sa
from pydantic import TypeAdapter
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ake.compiler.artifact import DomainArtifact, compute_artifact_id
from ake.compiler.citation import Citation

_metadata = sa.MetaData()

artifacts_table = sa.Table(
    "artifacts",
    _metadata,
    sa.Column("artifact_id", sa.Text, primary_key=True),
    sa.Column("doc_id", sa.Text, nullable=False),
    sa.Column("entity_id", sa.Text, nullable=False),
    sa.Column("artifact_type", sa.Text, nullable=False),
    sa.Column("fiscal_year", sa.Integer, nullable=True),
    sa.Column("payload", JSONB, nullable=False, server_default="{}"),
    sa.Column("field_citations", JSONB, nullable=False, server_default="{}"),
    sa.Column("acl_principals", ARRAY(sa.Text), nullable=False, server_default="{}"),
    sa.Column(
        "compiled_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("NOW()"),
    ),
)

_citation_adapter: TypeAdapter[Citation] = TypeAdapter(Citation)


def _citations_to_json(citations: dict[str, Citation]) -> dict:
    return {k: v.model_dump() for k, v in citations.items()}


def _citations_from_json(raw: dict) -> dict[str, Citation]:
    return {k: _citation_adapter.validate_python(v) for k, v in raw.items()}


def _row_to_artifact(row: Any) -> DomainArtifact:
    return DomainArtifact(
        artifact_id=row.artifact_id,
        doc_id=row.doc_id,
        entity_id=row.entity_id,
        artifact_type=row.artifact_type,
        fiscal_year=row.fiscal_year,
        payload=dict(row.payload),
        field_citations=_citations_from_json(dict(row.field_citations)),
        acl_principals=list(row.acl_principals),
        compiled_at=row.compiled_at.replace(tzinfo=timezone.utc),
    )


class ArtifactStore:
    """Thin async Postgres wrapper for artifact persistence."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    @asynccontextmanager
    async def _session(self) -> AsyncGenerator[AsyncSession, None]:
        async with self._session_factory() as session:
            yield session

    async def save(self, artifact: DomainArtifact) -> None:
        """Upsert artifact; updates payload, citations, and ACLs if artifact_id already exists."""
        async with self._session() as session:
            stmt = (
                pg_insert(artifacts_table)
                .values(
                    artifact_id=artifact.artifact_id,
                    doc_id=artifact.doc_id,
                    entity_id=artifact.entity_id,
                    artifact_type=artifact.artifact_type,
                    fiscal_year=artifact.fiscal_year,
                    payload=artifact.payload,
                    field_citations=_citations_to_json(artifact.field_citations),
                    acl_principals=artifact.acl_principals,
                    compiled_at=artifact.compiled_at,
                )
                .on_conflict_do_update(
                    index_elements=["artifact_id"],
                    set_={
                        "payload": artifact.payload,
                        "field_citations": _citations_to_json(artifact.field_citations),
                        "acl_principals": artifact.acl_principals,
                        "compiled_at": artifact.compiled_at,
                    },
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def get_by_id(self, artifact_id: str) -> DomainArtifact | None:
        async with self._session() as session:
            result = await session.execute(
                sa.select(artifacts_table).where(
                    artifacts_table.c.artifact_id == artifact_id
                )
            )
            row = result.fetchone()
            return _row_to_artifact(row) if row else None

    async def get_by_entity(
        self,
        entity_id: str,
        artifact_type: str,
        fiscal_year: int | None = None,
    ) -> list[DomainArtifact]:
        async with self._session() as session:
            cond = (artifacts_table.c.entity_id == entity_id) & (
                artifacts_table.c.artifact_type == artifact_type
            )
            if fiscal_year is not None:
                cond = cond & (artifacts_table.c.fiscal_year == fiscal_year)
            result = await session.execute(sa.select(artifacts_table).where(cond))
            return [_row_to_artifact(row) for row in result.fetchall()]

    async def get_by_doc_id(self, doc_id: str) -> list[DomainArtifact]:
        async with self._session() as session:
            result = await session.execute(
                sa.select(artifacts_table).where(artifacts_table.c.doc_id == doc_id)
            )
            return [_row_to_artifact(row) for row in result.fetchall()]

    async def exists(self, artifact_id: str) -> bool:
        async with self._session() as session:
            result = await session.execute(
                sa.select(sa.func.count())
                .select_from(artifacts_table)
                .where(artifacts_table.c.artifact_id == artifact_id)
            )
            return bool(result.scalar())