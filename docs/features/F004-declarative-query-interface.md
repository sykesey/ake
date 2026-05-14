# F004 — Declarative Query Interface

**Status:** Implemented  
**Layer:** 3 — Query

## Statement

The system exposes a single typed `execute(query, principal)` function that accepts a declarative `Query` (natural-language ask, desired response shape, filters, budget) and returns a shape-conformant, cited `QueryResult` — so that calling agents never loop over raw data or construct retrieval logic themselves.

## Acceptance Criteria

- `execute()` returns a `QueryResult` conforming to `query.shape` for all queries
- Citations in `QueryResult` trace back to real elements in the document store (verbatim span verifiable)
- ACL enforcement: a principal without access to an artifact does not see it in results
- Latency ≤ 10 seconds for queries resolved from ≤ 20 artifacts (excluding cold start)
- `execute()` is idempotent: same query + same artifacts → same result
- The planner correctly routes queries to the right artifact types

## Key Behaviours

- **Planner** — translates `query.contexts`, `query.shape`, and `query.filters` into a `RetrievalPlan` specifying artifact types, structured filters, and an optional semantic query; starts simple (keyword matching) and upgrades to LLM-based only when eval recall targets are missed
- **Fetcher** — executes the plan against Postgres (structured lookup, ACL-filtered via RLS) and pgvector (semantic lookup); merges on `artifact_id`
- **Composer** — small LLM call that reshapes fetched artifact JSON into `query.shape`; does not infer or estimate — sets null for absent values
- **Budget enforcement** — `QueryBudget.max_artifacts` and `timeout_seconds` cap resource use per call

## Out of Scope

- Raw artifact CRUD (Layer 2 / artifact store)
- Schema design or extraction code for domains (Layer 4)
- Multi-turn conversational state (callers manage their own context)
