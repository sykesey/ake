# ADR-007 — Hybrid Exact-Match + LLM-as-Judge Grading for the Compiler Loop

**Status:** Accepted  
**Date:** 2026-05-13

## Context

The compiler loop must automatically grade query results against ground-truth answers to drive iterative refinement. Two obvious grading strategies have complementary weaknesses:

- **Exact match** is fast, cheap, and deterministic but fails on semantically correct answers that differ in formatting, rounding, or phrasing (e.g. `10040` vs `"$10.04B"`)
- **LLM-as-judge** handles semantic equivalence but is expensive, introduces non-determinism, and can be inconsistent on borderline cases

The compiler loop runs up to 20 iterations per domain, grading 50–200 questions each iteration. Grading cost and latency compound quickly.

## Decision

Use exact match as the primary grader and LLM-as-judge as the fallback. For each `(result, ground_truth)` pair:

1. Run exact match. If it passes, score is 1.0 — no LLM call.
2. If exact match fails, call the LLM judge. Score is the LLM judge's output (0.0–1.0).
3. Final score is `max(exact_match_score, llm_judge_score)`.

Threshold for passing is 0.7 per question; questions below this threshold are added to the failure report. Overall domain score is the mean across all questions. The grader also groups failures by `difficulty` tier so the refine prompt receives structured patterns, not raw failure lists.

## Consequences

**Positive**
- Most single-fact questions (numeric values, dates) pass exact match — LLM judge is only called for the minority that fail, keeping grading cost proportional to difficulty
- Deterministic exact match anchors scoring for the cases where it applies; LLM drift only affects the tail
- Failure classification by difficulty tier lets the refiner target `multi_company` failures specifically rather than over-correcting based on easy single-fact failures

**Negative**
- LLM judge scores for borderline cases may vary across iterations even with the same inputs, introducing noise into the convergence signal; this can cause the "best score" to fluctuate
- Exact match requires ground-truth answers to be normalised (e.g. currency to a consistent unit); un-normalised eval sets produce false failures

**Mitigations**
- Eval set format specifies `unit` for numeric answers (e.g. `{"value": 10040, "unit": "usd_millions"}`); the grader normalises before comparison
- LLM judge calls use `temperature=0` and a structured scoring rubric to reduce variance
- The compiler loop tracks `best_score` across iterations and returns the best context even if the final iteration regresses, so judge noise does not discard a good intermediate state
