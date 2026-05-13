# ADR-004 — Nulls Are First-Class; "Not Disclosed" Must Be Representable

**Status:** Accepted  
**Date:** 2026-05-13

## Context

Source documents frequently omit fields. A financial filing may not disclose a specific metric. A contract may leave a clause blank. When an LLM cannot find a value, there is pressure — from model training and prompt design — to fill in a plausible default (zero, an empty string, or an estimate). Downstream agents that receive a zero when the field was absent may treat it as a real value and draw incorrect conclusions.

## Decision

`null` is the only valid representation for an absent or undisclosed field. Empty strings, zero values, placeholder strings (e.g. `"N/A"`, `"not provided"`), and estimated values are not permitted for domain fact fields.

The extraction prompt explicitly instructs the model: "If a field is not disclosed or cannot be found, set it to null. Do not infer or estimate." Citation verification enforces this indirectly — a field the model filled in without a verifiable source span will be nulled by the verifier before storage.

Pydantic schemas use `int | None`, `str | None`, etc. for all domain fields. No field defaults to `0` or `""`.

## Consequences

**Positive**
- Callers can distinguish "value is zero" from "value was not found" with a simple null check
- The compiler loop can count null fields as a retrieval failure and drive extraction improvements
- Agents that aggregate across entities treat null as "exclude from calculation" rather than "zero", preventing silent arithmetic errors

**Negative**
- Higher null rates in early iterations make accuracy metrics look worse before the extraction prompts are tuned; operators must interpret per-field null rates alongside accuracy scores
- Some downstream tools or BI layers may not handle nulls gracefully; callers are responsible for null-handling in their own UIs

**Mitigations**
- The eval set includes questions where the correct answer is "not disclosed"; graders must score null correctly for those questions
- The `QueryResult.data` shape can include a `_null_fields` list so agents have an explicit enumeration of absent fields without inspecting every key
