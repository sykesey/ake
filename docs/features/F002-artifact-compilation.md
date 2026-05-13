# F002 — Artifact Compilation

**Status:** Defined  
**Layer:** 2 — Artifact Compilation

## Statement

The system extracts typed, structured, cited fact records (artifacts) from parsed elements using LLM extraction at ingest time — so that downstream query execution never pays per-query extraction costs and every returned value is grounded in a specific source span.

## Acceptance Criteria

- One artifact is produced per entity per document
- All domain fields are typed and conform to a domain-specific Pydantic schema
- Every non-null field has a corresponding entry in `field_citations`
- Re-compiling an unchanged document at `temperature=0` produces the same artifact (idempotency)
- Null fields are stored as `null`, never as empty strings or zero values
- Artifacts are retrievable from Postgres by `(entity_id, artifact_type, fiscal_year)`
- Row-level security is enabled and tested on the `artifacts` table

## Key Behaviours

- **Single extraction pass** — LLM extraction runs once per document at ingest; query time calls only the composer (small, cheap)
- **Deterministic artifact ID** — `artifact_id` is a deterministic hash of `(doc_id, entity_id, artifact_type)`, enabling idempotent re-compilation
- **Citation-gated storage** — any field that fails citation verification is nulled before storage; bad provenance is never persisted
- **Promoted filter columns** — `entity_id`, `artifact_type`, and `fiscal_year` are real Postgres columns, not JSONB paths, so the query planner can index them efficiently

## Out of Scope

- Schema design for specific domains (that is a domain configuration concern)
- Query-time composition of artifacts into shaped responses (Layer 3)
- Automated schema iteration (Layer 4 Compiler Loop)
