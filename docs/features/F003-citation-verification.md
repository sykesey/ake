# F003 — Citation Verification

**Status:** Defined  
**Layer:** 2 — Artifact Compilation

## Statement

Before any artifact is persisted, the system verifies that every non-null field citation resolves to a real element and that the declared verbatim span is present in the source text — ensuring no ungrounded values enter the knowledge store.

## Acceptance Criteria

- The citation verifier runs after every LLM extraction call and before any write to the artifact store
- A citation is valid only if: the `element_id` exists in the element store, and `verbatim_span` is a substring of `element.text[char_start:char_end]`
- Fields that fail verification are set to `null` in the stored artifact, not discarded silently — the failure is logged
- Citation coverage ≥ 95% across a 50-document sample per domain
- The verifier returns a list of failing field names, enabling the compiler loop to detect patterns

## Key Behaviours

- **Hard gate before storage** — verification is not advisory; a failed citation causes the field to be nulled, never stored with bad provenance
- **Verbatim grounding** — the `verbatim_span` must literally appear in the source text; paraphrase or semantic similarity is not accepted
- **Failure surfacing** — failure lists are structured by field name and propagated to the compiler loop's failure report for diagnosis

## Out of Scope

- Semantic plausibility checks on cited values (that is the grader's role in the eval loop)
- Re-extraction on citation failure (the compiler loop handles that through iteration)
