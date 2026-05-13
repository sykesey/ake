# ADR-003 — Start with Postgres + pgvector; Defer Polyglot Persistence

**Status:** Accepted  
**Date:** 2026-05-13

## Context

The artifact store must support structured filtering (exact matches on `entity_id`, `artifact_type`, `fiscal_year`), semantic similarity search (vector nearest-neighbour on artifact embeddings), and row-level security enforcement. Several specialised databases exist for subsets of these needs: Qdrant/Weaviate for vectors, Kuzu for graph queries, Oso/Cerbos for policy enforcement.

Introducing multiple databases from the start adds operational complexity (separate deployments, connection pooling, consistency across stores), on-call burden, and cross-database transaction risk — before any evidence that Postgres cannot meet the access patterns.

## Decision

Use Postgres with the pgvector extension as the single persistence layer. Structured queries, vector search (HNSW index), and row-level security all run in one database. No additional databases are introduced at project start.

Polyglot persistence is added only when a specific trigger fires:

| Trigger | Action |
|---|---|
| Artifact count ≥ 5M | Evaluate Qdrant/Weaviate alongside pgvector |
| Vector recall@10 < 0.90 after HNSW tuning | Dedicated vector DB |
| Graph query latency > 200ms warm | Evaluate Kuzu (embedded) |
| ABAC / dynamic group ACL needed | Add Oso/Cerbos |

## Consequences

**Positive**
- One database to operate, monitor, back up, and reason about
- RLS, JSONB, and HNSW are well-supported by managed Postgres offerings (RDS, Cloud SQL, Neon)
- Team familiarity with Postgres reduces ramp-up; SQL is the common language across the stack
- Structured and semantic queries can be combined in a single query plan (no client-side merge)

**Negative**
- pgvector HNSW recall and throughput are lower than purpose-built vector databases at large scale; this becomes a constraint above ~5M artifacts or high concurrent vector query load
- Postgres RLS covers row-level access but not attribute-based policies with dynamic conditions; complex permission models will require migration to a policy engine
- JSONB `payload` access is less ergonomic than a document store for deeply nested domain schemas

**Mitigations**
- Filter fields are promoted to real columns (see ADR-005) so hot query paths never touch JSONB
- HNSW parameters (`m`, `ef_construction`) are tuned before declaring recall insufficient
- The database decision checklist is reviewed at each domain milestone, not deferred indefinitely
