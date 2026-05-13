"""Postgres persistence for parsed Element records."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from ake.ingestion.element import Element

_metadata = sa.MetaData()

elements_table = sa.Table(
    "elements",
    _metadata,
    sa.Column("doc_id", sa.Text, primary_key=True),
    sa.Column("element_id", sa.Text, primary_key=True),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("page", sa.Integer, nullable=False, server_default="0"),
    sa.Column("section_path", ARRAY(sa.Text), nullable=False, server_default="{}"),
    sa.Column("metadata", JSONB, nullable=False, server_default="{}"),
    sa.Column(
        "ingested_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("NOW()"),
    ),
)


def _row_to_element(row: Any) -> Element:
    return Element(
        doc_id=row.doc_id,
        element_id=row.element_id,
        type=row.type,
        text=row.text,
        page=row.page,
        section_path=list(row.section_path),
        metadata=dict(row.metadata),
    )


class ElementStore:
    """Thin async Postgres wrapper for element persistence."""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    @asynccontextmanager
    async def _session(self) -> AsyncGenerator[AsyncSession, None]:
        async with self._session_factory() as session:
            yield session

    async def save(self, elements: list[Element]) -> None:
        """Upsert elements; silently skips rows that already exist (idempotent)."""
        if not elements:
            return
        async with self._session() as session:
            for el in elements:
                stmt = (
                    sa.insert(elements_table)
                    .values(
                        doc_id=el.doc_id,
                        element_id=el.element_id,
                        type=el.type,
                        text=el.text,
                        page=el.page,
                        section_path=el.section_path,
                        metadata=el.metadata,
                    )
                    .on_conflict_do_nothing(
                        index_elements=["doc_id", "element_id"]
                    )
                )
                await session.execute(stmt)
            await session.commit()

    async def get_by_doc_id(self, doc_id: str) -> list[Element]:
        async with self._session() as session:
            result = await session.execute(
                sa.select(elements_table)
                .where(elements_table.c.doc_id == doc_id)
                .order_by(elements_table.c.element_id)
            )
            return [_row_to_element(row) for row in result.fetchall()]

    async def exists(self, doc_id: str) -> bool:
        async with self._session() as session:
            result = await session.execute(
                sa.select(sa.func.count())
                .select_from(elements_table)
                .where(elements_table.c.doc_id == doc_id)
            )
            return bool(result.scalar())
