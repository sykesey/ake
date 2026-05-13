"""Add elements table (F001 — document ingestion).

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "elements",
        sa.Column("doc_id", sa.Text(), nullable=False),
        sa.Column("element_id", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "section_path",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("doc_id", "element_id"),
    )
    op.create_index("elements_doc_id_idx", "elements", ["doc_id"])


def downgrade() -> None:
    op.drop_index("elements_doc_id_idx", table_name="elements")
    op.drop_table("elements")
