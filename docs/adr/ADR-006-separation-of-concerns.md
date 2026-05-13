# ADR-006 — Structured Filtering, Semantic Search, and ACL Enforcement Are Distinct Subsystems

**Status:** Accepted  
**Date:** 2026-05-13

## Context

A retrieval system that conflates filtering, semantic similarity, and permission enforcement in a single component is hard to test, hard to debug, and hard to evolve. Mixing concerns means a change to the similarity algorithm can accidentally affect ACL behaviour, or ACL logic can short-circuit filter optimisations in unpredictable ways.

## Decision

The query layer is decomposed into three distinct subsystems with thin, typed interfaces between them:

1. **Planner** — translates a declarative `Query` into a `RetrievalPlan` (artifact types, structured filters, optional semantic query, result budget). The planner has no knowledge of permissions or vector math.

2. **Fetcher** — executes the `RetrievalPlan` against Postgres (structured lookup, ACL enforcement via RLS) and pgvector (semantic lookup if `semantic_query` is set). ACL enforcement happens here, inside the database, not in application code. Results are merged on `artifact_id` before returning to the caller.

3. **Composer** — receives a flat list of `Artifact` objects and reshapes their JSON into the response shape specified by `query.shape`. The composer has no knowledge of how artifacts were retrieved or who requested them.

The Planner calls the Fetcher; the Fetcher calls the database; the Composer receives Fetcher output. No component skips a layer.

## Consequences

**Positive**
- Each subsystem can be tested independently with mocked inputs
- ACL enforcement is centralised in the Fetcher / database RLS; no code path can accidentally bypass it
- Planner upgrades (keyword → LLM-based) do not touch Fetcher or Composer code
- Composer prompt changes do not risk changing retrieval behaviour

**Negative**
- A strict layer boundary means some optimisations that span layers (e.g. re-ranking based on composer confidence) require explicit interfaces to pass signals back
- Three components to deploy and observe instead of one

**Mitigations**
- `QueryTrace` captures the `RetrievalPlan` and `artifacts_fetched` alongside the final result, giving full visibility into each layer's contribution without coupling them
- Cross-layer optimisations are deferred until eval data shows they are needed; premature coupling has caused more debugging cost than the optimisations have saved
