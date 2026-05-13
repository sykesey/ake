"""Add artifacts table with RLS (F002 — artifact compilation).

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("doc_id", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=False),
        sa.Column("artifact_type", sa.Text(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "field_citations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "acl_principals",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "compiled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("artifact_id"),
    )

    # Indexes on promoted filter columns (ADR-005)
    op.create_index("artifacts_doc_id_idx", "artifacts", ["doc_id"])
    op.create_index(
        "artifacts_entity_type_idx", "artifacts", ["entity_id", "artifact_type"]
    )
    op.create_index(
        "artifacts_fiscal_year_idx",
        "artifacts",
        ["fiscal_year"],
        postgresql_where=sa.text("fiscal_year IS NOT NULL"),
    )

    # Row-level security (F005 will own the principal-check policy;
    # this migration enables RLS and adds a permissive bootstrap policy
    # that is replaced by F005 once app.current_principals is in use).
    op.execute("ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE artifacts FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY artifacts_acl ON artifacts
            FOR SELECT
            USING (
                acl_principals = '{}'::text[]
                OR acl_principals && string_to_array(
                    current_setting('app.current_principals', true), ','
                )
            )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS artifacts_acl ON artifacts")
    op.drop_index("artifacts_fiscal_year_idx", table_name="artifacts")
    op.drop_index("artifacts_entity_type_idx", table_name="artifacts")
    op.drop_index("artifacts_doc_id_idx", table_name="artifacts")
    op.drop_table("artifacts")
