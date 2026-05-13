# ADR-002 — Citations Are Mandatory for All Non-Null Fields

**Status:** Accepted  
**Date:** 2026-05-13

## Context

LLMs hallucinate. In a knowledge engine serving agents with business or analytical consequences, a confident but wrong value is worse than a null. Without a verifiable grounding mechanism, callers have no way to distinguish a correctly extracted value from a hallucinated one, and the compiler loop has no signal to distinguish extraction failures from data gaps.

## Decision

Every non-null field in every artifact must have a corresponding `Citation` entry in `field_citations`. A citation must specify the source `element_id`, character offsets `(char_start, char_end)`, and a `verbatim_span` — the exact text substring from which the value was extracted.

The citation verifier runs after every LLM extraction call and before any write to the artifact store. Fields that fail verification (element not found, or verbatim span not present in source text) are set to `null` in the stored artifact. Bad provenance is never persisted.

An unverified value is treated as a null. There is no "unverified but present" state.

## Consequences

**Positive**
- Every stored value is traceable to a specific character range in a specific source document
- Callers and downstream agents can display provenance to end users without additional lookups
- The citation verifier provides a structural signal to the compiler loop (`citation_gap` failure class) that drives prompt refinement
- Audit and compliance use cases are supported without extra instrumentation

**Negative**
- Extraction prompts are more complex — they must elicit verbatim spans, not just values
- Citation coverage < 100% is expected (target ≥ 95%); some legitimately extractable values will be nulled due to span mismatches from paraphrase or table reformatting
- Multi-hop or computed values (e.g. ratios derived from two cited numbers) cannot carry a single verbatim citation; these require explicit modelling as derived fields

**Mitigations**
- The extraction prompt template instructs the model to quote the exact span and include the element_id; schema validation catches missing citations before the verifier runs
- Derived fields are a first-class design pattern; the schema can flag a field as `derived: true` to exempt it from the verbatim citation requirement while still requiring source field references
