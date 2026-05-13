# F008 — Structured Query Observability

**Status:** Defined  
**Layer:** 3 — Query / Cross-cutting

## Statement

Every query execution emits a structured `QueryTrace` capturing the plan, artifacts fetched, token costs, citations, and latency — providing the data needed to debug the compiler loop, audit agent behaviour in production, and track accuracy as the corpus evolves.

## Acceptance Criteria

- Every call to `execute()` emits a `QueryTrace`, regardless of success or failure
- Traces are stored and queryable by `(query_id, principal_id, timestamp)`
- The following metrics are tracked per domain context: completion rate, citation coverage, planner hit rate, token cost per query, and accuracy on the eval set (run on a schedule)
- Traces produced during compiler loop eval runs include a `score` field populated by the grader

## Key Behaviours

- **Mandatory emission** — `QueryTrace` is not optional instrumentation; it is required infrastructure for the compiler loop and production audit
- **Token cost attribution** — only composer tokens are tracked per-query; compilation cost is amortised and tracked separately at ingest time
- **Eval set scheduling** — the eval set is run on a regular schedule against the live corpus so accuracy regressions surface before they reach production callers
- **Planner hit rate** — tracks the fraction of planned artifact types that returned actual results, surfacing retrieval gaps without waiting for end-to-end eval runs

## Trace Schema

```python
@dataclass
class QueryTrace:
    query_id: str
    principal_id: str
    ask: str
    plan: RetrievalPlan
    artifacts_fetched: list[str]
    composer_input_tokens: int
    composer_output_tokens: int
    citations: list[Citation]
    score: float | None          # populated by grader in eval runs
    latency_ms: int
    timestamp: datetime
```

## Out of Scope

- Distributed tracing across upstream callers (traces cover the engine boundary only)
- Real-time alerting (traces are inputs to monitoring; alerting is an operational concern)
