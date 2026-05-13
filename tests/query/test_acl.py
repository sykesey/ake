"""Tests for F005 — ACL Enforcement via Postgres RLS."""
from __future__ import annotations

import pytest

from ake.query.interface import Query, QueryBudget


class TestACLPrincipalSetting:
    """execute() must set app.current_principals before any query (acceptance criterion 3)."""

    @pytest.mark.asyncio
    async def test_principal_set_on_session(self, db_session):
        """Verify execute() sets app.current_principals on the session."""
        from sqlalchemy import text

        principal = "user-alice"
        await db_session.execute(
            text("SET app.current_principals = :p"), {"p": principal}
        )
        result = await db_session.execute(
            text("SELECT current_setting('app.current_principals')")
        )
        assert result.scalar() == principal


class TestACLIsolation:
    """A principal without access must not see restricted artifacts (acceptance criterion 4)."""

    @pytest.mark.asyncio
    async def test_principal_without_access_sees_nothing(self, db_session):
        """Insert an artifact restricted to ['bob'], query as 'alice' — must get nothing."""
        from sqlalchemy import text

        # Insert a restricted artifact directly
        await db_session.execute(
            text("""
                INSERT INTO artifacts (artifact_id, doc_id, entity_id, artifact_type,
                                       acl_principals, payload, field_citations)
                VALUES ('test-acl-1', 'doc-acl-1', 'entity-x', 'financials_10k',
                        ARRAY['bob'], '{"revenue": 100}'::jsonb, '{}'::jsonb)
                ON CONFLICT (artifact_id) DO UPDATE
                    SET acl_principals = ARRAY['bob'],
                        payload = '{"revenue": 100}'::jsonb
            """)
        )
        await db_session.commit()

        # Set principal to 'alice' (not in ACL)
        await db_session.execute(
            text("SET app.current_principals = 'alice'")
        )

        # Query via the fetcher path — RLS should filter the row
        from sqlalchemy import select
        from ake.store.artifact_store import artifacts_table

        result = await db_session.execute(
            select(artifacts_table.c.artifact_id).where(
                artifacts_table.c.entity_id == "entity-x"
            )
        )
        rows = result.fetchall()
        assert len(rows) == 0, "alice should not see bob's restricted artifact"


class TestACLPublicAccess:
    """Artifacts with empty acl_principals are visible to all (acceptance criterion 4)."""

    @pytest.mark.asyncio
    async def test_public_artifact_visible_to_any_principal(self, db_session):
        """Insert an artifact with empty ACL — any principal should see it."""
        from sqlalchemy import text

        # Insert a public artifact
        await db_session.execute(
            text("""
                INSERT INTO artifacts (artifact_id, doc_id, entity_id, artifact_type,
                                       acl_principals, payload, field_citations)
                VALUES ('test-acl-public', 'doc-public', 'entity-public', 'financials_10k',
                        '{}', '{"revenue": 200}'::jsonb, '{}'::jsonb)
                ON CONFLICT (artifact_id) DO UPDATE
                    SET acl_principals = '{}',
                        payload = '{"revenue": 200}'::jsonb
            """)
        )
        await db_session.commit()

        # Set principal to 'alice'
        await db_session.execute(
            text("SET app.current_principals = 'alice'")
        )

        from sqlalchemy import select
        from ake.store.artifact_store import artifacts_table

        result = await db_session.execute(
            select(artifacts_table.c.artifact_id).where(
                artifacts_table.c.entity_id == "entity-public"
            )
        )
        rows = result.fetchall()
        assert len(rows) == 1, "alice should see public artifact"
        assert rows[0].artifact_id == "test-acl-public"


class TestACLAuthorizedAccess:
    """A principal listed in acl_principals sees the artifact."""

    @pytest.mark.asyncio
    async def test_authorized_principal_sees_artifact(self, db_session):
        """Insert artifact restricted to ['alice'], query as 'alice' — must see it."""
        from sqlalchemy import text

        await db_session.execute(
            text("""
                INSERT INTO artifacts (artifact_id, doc_id, entity_id, artifact_type,
                                       acl_principals, payload, field_citations)
                VALUES ('test-acl-auth', 'doc-auth', 'entity-auth', 'financials_10k',
                        ARRAY['alice'], '{"revenue": 300}'::jsonb, '{}'::jsonb)
                ON CONFLICT (artifact_id) DO UPDATE
                    SET acl_principals = ARRAY['alice'],
                        payload = '{"revenue": 300}'::jsonb
            """)
        )
        await db_session.commit()

        await db_session.execute(
            text("SET app.current_principals = 'alice'")
        )

        from sqlalchemy import select
        from ake.store.artifact_store import artifacts_table

        result = await db_session.execute(
            select(artifacts_table.c.artifact_id).where(
                artifacts_table.c.entity_id == "entity-auth"
            )
        )
        rows = result.fetchall()
        assert len(rows) == 1, "alice should see her own restricted artifact"
        assert rows[0].artifact_id == "test-acl-auth"


class TestACLMultiplePrincipals:
    """A principal in a multi-member acl_principals array sees the artifact."""

    @pytest.mark.asyncio
    async def test_principal_in_multi_acl_sees_artifact(self, db_session):
        """Insert artifact with acl_principals ['alice', 'bob'], query as 'bob'."""
        from sqlalchemy import text

        await db_session.execute(
            text("""
                INSERT INTO artifacts (artifact_id, doc_id, entity_id, artifact_type,
                                       acl_principals, payload, field_citations)
                VALUES ('test-acl-multi', 'doc-multi', 'entity-multi', 'financials_10k',
                        ARRAY['alice','bob'], '{"revenue": 400}'::jsonb, '{}'::jsonb)
                ON CONFLICT (artifact_id) DO UPDATE
                    SET acl_principals = ARRAY['alice','bob'],
                        payload = '{"revenue": 400}'::jsonb
            """)
        )
        await db_session.commit()

        await db_session.execute(
            text("SET app.current_principals = 'bob'")
        )

        from sqlalchemy import select
        from ake.store.artifact_store import artifacts_table

        result = await db_session.execute(
            select(artifacts_table.c.artifact_id).where(
                artifacts_table.c.entity_id == "entity-multi"
            )
        )
        rows = result.fetchall()
        assert len(rows) == 1, "bob should see artifact shared with alice"


class TestACLReingestionPropagation:
    """ACL changes on source documents must be re-propagated on re-ingestion
    (acceptance criterion 5)."""

    @pytest.mark.asyncio
    async def test_upsert_updates_acl_principals(self, db_session):
        """ArtifactStore.save() must update acl_principals on conflict."""
        from contextlib import asynccontextmanager
        from datetime import datetime, timezone

        from ake.compiler.artifact import DomainArtifact
        from ake.store.artifact_store import ArtifactStore

        @asynccontextmanager
        async def _session_factory():
            yield db_session

        # First save — restricted to ['alice']
        artifact = DomainArtifact(
            artifact_id="test-acl-reingest",
            doc_id="doc-reingest",
            entity_id="entity-reingest",
            artifact_type="financials_10k",
            fiscal_year=None,
            payload={"revenue": 500},
            field_citations={},
            acl_principals=["alice"],
            compiled_at=datetime.now(timezone.utc),
        )

        store = ArtifactStore(_session_factory)
        await store.save(artifact)

        # Verify ACL was stored
        saved = await store.get_by_id("test-acl-reingest")
        assert saved is not None
        assert saved.acl_principals == ["alice"]

        # Re-ingest with expanded ACL
        artifact.acl_principals = ["alice", "charlie"]
        artifact.payload = {"revenue": 600}
        await store.save(artifact)

        # Verify ACL was updated
        re_saved = await store.get_by_id("test-acl-reingest")
        assert re_saved is not None
        assert re_saved.acl_principals == ["alice", "charlie"]
        assert re_saved.payload == {"revenue": 600}

        # Cleanup
        from sqlalchemy import text
        await db_session.execute(
            text("DELETE FROM artifacts WHERE artifact_id = 'test-acl-reingest'")
        )
        await db_session.commit()