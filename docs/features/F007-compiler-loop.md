# F007 — Eval-Driven Compiler Loop

**Status:** Defined  
**Layer:** 4 — Compiler Loop

## Statement

The system provides an agentic harness that, given a domain description, an eval set, and a parsed corpus, autonomously produces artifact schemas and extraction code that achieve a target accuracy threshold — without requiring a retrieval engineer to author domain-specific extraction logic from scratch.

## Acceptance Criteria

- The compiler converges to ≥ 0.85 overall eval score for the first target domain
- Per-difficulty thresholds: `single_fact` ≥ 0.90, `multi_company` ≥ 0.75
- The loop runs end-to-end without human intervention after the eval set and corpus are provided
- Convergence is achieved in ≤ 15 iterations on average across domains
- The compiler for domain 2 reuses ≥ 3 skills from the skill library

## Inputs Required

- **Eval set** — 50–200 `(question, ground_truth)` pairs in JSONL, each tagged with `difficulty` and `entities`
- **Source corpus** — Layer 1 parsed documents for the domain
- **Domain description** — 1–3 sentence description
- **Skill library** — available extraction primitives (Python module)

## Key Behaviours

- **Bootstrap → curate → evaluate → grade → refine loop** — the LLM proposes an initial schema and extraction code from the domain description and 5 seed eval items; each iteration re-compiles artifacts, runs all eval queries, grades results, and refines code based on structured failure analysis
- **Failure classification** — failures are bucketed into structural classes (`missing_artifact`, `wrong_granularity`, `citation_gap`, `multi_entity_miss`, `section_miss`, `unit_error`) so the refine prompt sees patterns, not individual failures
- **Best-achieved fallback** — if the threshold is not met within `max_iters`, the harness returns the best context achieved and surfaces the score to the operator
- **Grading** — exact match where applicable; LLM-as-judge otherwise; score is `max(exact, llm_score)`; per-difficulty breakdown tracked separately

## Out of Scope

- Fully unsupervised operation (an eval set authored by a domain expert is required)
- Generating parsers or normalizers (Layer 1 output is a prerequisite)
- Runtime query execution (the compiled context is handed to Layer 3)
