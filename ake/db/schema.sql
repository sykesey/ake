-- Canonical DDL — kept in sync with Alembic revisions.
-- Run `alembic upgrade head` to apply; never execute this file directly.

-- revision: 0001_baseline
CREATE EXTENSION IF NOT EXISTS vector;

-- revision: 0002_elements (F001 — document ingestion)
CREATE TABLE elements (
    doc_id       TEXT NOT NULL,
    element_id   TEXT NOT NULL,
    type         TEXT NOT NULL,
    text         TEXT NOT NULL,
    page         INTEGER NOT NULL DEFAULT 0,
    section_path TEXT[] NOT NULL DEFAULT '{}',
    metadata     JSONB NOT NULL DEFAULT '{}',
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (doc_id, element_id)
);
CREATE INDEX elements_doc_id_idx ON elements (doc_id);

-- revision: 0003_artifacts (F002 — artifact compilation)
CREATE TABLE artifacts (
    artifact_id     TEXT NOT NULL,
    doc_id          TEXT NOT NULL,
    entity_id       TEXT NOT NULL,
    artifact_type   TEXT NOT NULL,
    fiscal_year     INTEGER,
    payload         JSONB NOT NULL DEFAULT '{}',
    field_citations JSONB NOT NULL DEFAULT '{}',
    acl_principals  TEXT[] NOT NULL DEFAULT '{}',
    compiled_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (artifact_id)
);
CREATE INDEX artifacts_doc_id_idx ON artifacts (doc_id);
CREATE INDEX artifacts_entity_type_idx ON artifacts (entity_id, artifact_type);
CREATE INDEX artifacts_fiscal_year_idx ON artifacts (fiscal_year)
    WHERE fiscal_year IS NOT NULL;

-- revision: 0004_acl_gin_index (F005 — ACL enforcement)
CREATE INDEX artifacts_acl_gin ON artifacts USING GIN (acl_principals);

-- Row-level security (F005 — ACL enforcement).
-- A row is visible only if acl_principals is empty (public) or overlaps
-- with the session's app.current_principals setting.
ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE artifacts FORCE ROW LEVEL SECURITY;
CREATE POLICY artifacts_acl ON artifacts
    FOR SELECT
    USING (
        acl_principals = '{}'::text[]
        OR acl_principals && string_to_array(
            current_setting('app.current_principals', true), ','
        )
    );