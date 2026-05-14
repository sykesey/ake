# ADR-009 — Direct Mapping for Structured Sources; LLM Extraction Only Where Needed

**Status:** Accepted  
**Date:** 2026-05-13

## Context

The document ingestion pipeline (Layer 2) relies on LLM extraction to pull structured facts from unstructured prose. This is necessary for documents because the facts are implicit in natural language — the LLM must read "revenue grew to $1.2 billion" and produce `{"revenue": 1200, "unit": "usd_millions"}`.

Tabular data (Parquet, CSV) and knowledge graph data already carry explicit structure: column names are field names, cell values are field values, node properties are named, edge types are typed. Running LLM extraction over pre-structured data would be expensive, non-deterministic, and would introduce a hallucination risk that does not exist in the source — the model might "helpfully" reinterpret a cell value rather than copying it.

However, structured sources still benefit from LLM involvement in limited, specific roles.

## Decision

For tabular and knowledge graph sources, use **direct column/property mapping** as the primary compilation path. LLM calls are permitted only for the following roles, and must be justified per-domain:

| Role | Justification | LLM call scope |
|---|---|---|
| **Entity resolution** | Map a name string (`"Apple Inc."`) to a canonical `entity_id` via the `resolve_entity` skill — when the entity registry does not have an exact-match rule | Small, targeted call on the entity name only |
| **Unit normalisation** | When `normalize_currency` / `normalize_date` skills cannot parse a cell value deterministically (ambiguous format) | Single-cell call; result is cached by value |
| **Summary embedding generation** | Produce a natural-language summary of a row or node for the vector embedding (used in semantic search) | One call per artifact; not per field |
| **Type coercion for mixed-type columns** | When a column's schema type is `OBJECT` or `VARIANT` and the domain schema expects a typed field | Constrained extraction on the cell value only |

Direct mapping rules:
- Column name → artifact field name (with normalisation: lowercase, underscores, strip units suffix)
- Cell value → artifact field value (type-cast according to column schema)
- Null cell → null artifact field (ADR-004 applies; no filling)
- Every directly mapped field gets a `TabularRef` or `GraphRef` citation (ADR-008); no LLM citation generation needed

The compiler loop (Layer 4) still applies to structured domains — it iterates on the **mapping rules and entity resolution logic**, not on the extraction prompt. The `refine` step produces updated column-to-field mapping configs rather than new extraction prompts.

## Consequences

**Positive**
- Compilation of structured sources is fast and cheap: no per-field LLM call, no citation gap risk from model paraphrase
- Determinism is near-complete for direct-mapped fields (the only non-determinism is in entity resolution and summary generation)
- Citation coverage for direct-mapped fields is 100% by construction — every value has a cell-level citation before any LLM is involved
- The skill library's normalisation functions (`normalize_currency`, `normalize_date`) get exercised at scale on structured data, improving their coverage before they are called in document extraction

**Negative**
- The compiler loop's refine logic must distinguish between "extraction prompt failure" (document sources) and "mapping rule failure" (structured sources) — the failure taxonomy in ADR-007 partially applies but needs a `mapping_error` class added
- Domain schemas designed for document extraction may not align with tabular column names without a mapping config layer; this adds a per-domain configuration artefact that the compiler loop must manage
- Schemas with denormalised or repeated columns (wide tables with 200+ columns) produce very large `Element.text` strings; the compiler must select which columns to map rather than mapping all of them

**Mitigations**
- The compiler loop bootstrap step for structured domains outputs a column-selection config (which columns map to which artifact fields) rather than an extraction prompt; the same eval-driven iteration applies
- A `mapping_error` failure class is added to the grader (alongside the six classes in the dev guide) to capture cases where a column exists but the mapping produced the wrong artifact field value
- Column selection is bounded by the artifact schema: only columns that correspond to a schema field are mapped; unmapped columns are stored in `metadata.unmapped_columns` for future schema iteration

## Extension — Amorphous Schema Derivation (F012)

ADR-012 extends this principle to schema discovery itself: the semantic role of a column (entity key, foreign key, currency amount, date, etc.) is classified deterministically from its name rather than via LLM analysis. This is the direct-mapping principle applied one level higher — not "use the column value directly" but "use the column name directly to classify the column's role". The same rationale applies: real-world column naming conventions are strong enough signals that LLM involvement adds cost and non-determinism without meaningfully improving accuracy.
