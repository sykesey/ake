# ADR-001 — Compile Artifacts at Ingest, Not at Query Time

**Status:** Accepted  
**Date:** 2026-05-13

## Context

LLM extraction is expensive in latency and cost. A naive approach would run extraction on-demand — retrieving raw document elements and calling an LLM to pull out facts each time an agent asks a question. This is simpler to implement but means every agent call bears the full extraction cost, extraction results are non-deterministic across calls, and there is no stable artifact to index or cache.

## Decision

All LLM-based fact extraction runs once at ingest time. The output — typed, cited artifacts — is stored in Postgres. Query-time execution is limited to: a lightweight planner call (keyword-based by default), a Postgres/pgvector fetch, and a small composer call that reshapes already-extracted JSON into the requested shape. No extraction happens at query time.

## Consequences

**Positive**
- Query latency is bounded and predictable (fetch + small compose, not full extraction)
- Token cost per query is low; extraction cost is amortised across all future queries on that document
- Artifacts are stable, indexable, and auditable — the same fact always returns the same answer
- The compiler loop can iterate on extraction quality offline without affecting live queries

**Negative**
- Domain schemas must be defined before ingestion; late schema additions require re-compilation of affected documents
- The ingestion pipeline is more complex — it must orchestrate parse → extract → verify → store
- A corpus update (new doc version) requires re-ingestion, not just a query change

**Mitigations**
- Idempotent `artifact_id` (deterministic hash) means re-ingestion of unchanged documents is a no-op
- The compiler loop automates schema iteration; operators need not manually re-write and re-run extraction for each tuning cycle
