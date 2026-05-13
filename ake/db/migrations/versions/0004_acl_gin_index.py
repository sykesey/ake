"""Add GIN index on acl_principals for array overlap checks (F005 — ACL enforcement).

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-13
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX artifacts_acl_gin ON artifacts USING GIN (acl_principals)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS artifacts_acl_gin")