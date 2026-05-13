# ADR-005 — Promote Filter Fields from JSONB to Real Columns

**Status:** Accepted  
**Date:** 2026-05-13

## Context

Artifact domain payloads are stored as JSONB because their shape varies by domain and evolves as schemas are refined. JSONB is flexible and avoids schema migrations for every new domain field. However, Postgres cannot use standard B-tree or GIN indexes on arbitrary JSONB paths — queries that filter inside JSONB are sequential scans unless a partial index is built for each specific path. Fields used frequently in filters (entity, type, year) would degrade query latency at scale.

## Decision

Any artifact field that the query planner will filter, sort, or join on must be stored as a dedicated Postgres column — not inside the `payload` JSONB blob. The initial set of promoted columns is:

- `entity_id TEXT NOT NULL` — always filtered; every query targets one or more entities
- `artifact_type TEXT NOT NULL` — always filtered; determines which domain schema to apply
- `fiscal_year INT` — frequently filtered; nullable for non-periodic artifacts
- `acl_principals TEXT[]` — filtered on every query for row-level security

Standard B-tree and GIN indexes are built on these columns. JSONB paths are never indexed. If a future access pattern requires filtering on a field currently in JSONB, it is promoted — the JSONB field is kept for backward compatibility during the migration window, then removed.

## Consequences

**Positive**
- All hot filter paths use indexed columns; query plans are predictable and fast
- The database decision checklist trigger ("graph query latency > 200ms") is less likely to fire prematurely due to JSONB scan overhead
- Promoted columns are strongly typed; type errors are caught at write time, not at query time

**Negative**
- Adding a new promoted column requires a schema migration and re-ingestion of affected artifacts
- Discipline is required to avoid "just putting it in JSONB for now and filtering later" — that pattern creates hidden slow paths

**Mitigations**
- The domain schema design step explicitly identifies promoted-column candidates before the first ingest run for each domain
- The compiler loop's failure analysis includes query latency; a latency spike on a new domain triggers promotion review before the domain goes to production
