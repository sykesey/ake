# ADR-012 — Zero-Declaration Schema Derivation for Amorphous Sources

**Status:** Accepted  
**Date:** 2026-05-14

## Context

ADR-001 states: *"domain schemas must be defined before ingestion; late schema additions require re-compilation of affected documents."* This is correct for the LLM-based document compilation path: a typed extraction prompt cannot produce structured artifacts unless the artifact schema is declared in advance.

The amorphous ingestion pipeline (F012) targets a different scenario: an operator drops a directory of files — CSVs from a data warehouse, exported tables from a third-party system, mixed formats from a partner — and wants immediate knowledge structure without a data-modelling phase. Requiring a schema declaration here would defeat the purpose.

The options considered were:

1. **Require a schema file alongside the data** — A YAML or JSON declaration mapping column names to artifact fields, provided by the operator before ingestion. This maintains ADR-001 strictly but breaks the amorphous promise: operators must do modelling work before the system can help them.

2. **LLM-derived schema** — Sample representative rows and ask a language model to infer field names, types, and semantic roles. Non-deterministic, expensive on wide schemas, and introduces hallucination risk at the point where the schema should be most trustworthy.

3. **Deterministic name-pattern heuristics for semantic roles + pyarrow type inference for data types** — Use the existing column names and pyarrow-inferred types directly. Semantic roles (entity key, foreign key, currency amount, date, etc.) are classified purely from naming conventions that are near-universal in real datasets.

4. **No classification; map everything as `xsd:string` with no roles** — Lose structural information entirely; downstream consumers cannot exploit relationship or type structure.

## Decision

Use **deterministic heuristics** (option 3) for all schema derivation in the amorphous pipeline. Concretely:

- **Data types** come from pyarrow's schema inference, which reads the actual file format. Column types are mapped to XSD datatypes and stored in `ColumnInfo.pa_type` and `OntologyProperty.datatype`. No LLM call is involved.

- **Semantic roles** are classified from column names alone using a fixed, ordered set of rules (see F012). The rules encode naming conventions that are broadly stable across organisations: `*_id` columns are keys, `*_date` / `*_at` columns are temporal, `price` / `salary` / `budget` columns are monetary, etc. No data sampling is needed for role assignment.

- **OWL class and property names** are mechanically derived from table and column names (snake_case → PascalCase for classes, snake_case → camelCase for properties). This is deterministic and reversible.

The heuristic approach is explicitly not about being right in every case. Its job is to produce a *useful starting schema* immediately — one that can be corrected by re-ingesting with `--dataset-name` overrides or by updating the heuristic rules — rather than producing a *perfect schema* through expensive offline analysis.

### Why this is not in tension with ADR-001

ADR-001's constraint ("compile at ingest, not at query time") applies to **LLM-based fact extraction**, whose cost and non-determinism must be front-loaded. Schema derivation under ADR-012 is **deterministic and cheap** — it runs in O(columns) time with no model calls. The spirit of ADR-001 (avoid expensive, non-deterministic work at query time) is satisfied. The letter (schemas declared before ingestion) is relaxed specifically for the amorphous pipeline, where the schema *is* the ingestion output, not a prerequisite to it.

## Consequences

**Positive**
- Zero onboarding friction: any directory of files can be ingested immediately. The derived schema is immediately useful for browsing, graph visualization, and downstream ontology consumption.
- The heuristics are fully transparent and auditable: the `semantic_role` for every column can be inspected in the YAML output, and the rules are in a single, short module (`ake/ingestion/amorphous_pipeline.py`).
- Determinism is complete: the same file always produces the same schema, roles, and ontology. Re-ingestion is idempotent at the element level (F009 `doc_id` stability carries through).

**Negative**
- Heuristics will misclassify edge cases: a column named `status_id` may be treated as a foreign key when it is actually an enumeration code; a column named `address` will be `unknown` rather than `text`. Classification quality degrades for non-English or heavily abbreviated column names.
- The derived schema is not validated against any business definition: a column named `amount` will be classified as `currency` even if it counts discrete units.
- There is no feedback loop between classification accuracy and downstream compilation quality without explicit eval harnesses.

**Mitigations**
- The `semantic_role` field is a metadata annotation, not an enforcement mechanism: downstream compilers can override it per-domain without re-ingesting. A future `role_overrides` config in `amorphous_pipeline.ingest_directory()` can accept caller-supplied corrections.
- The `unknown` role is a safe default: consumers that cannot use an unknown-role column simply ignore it, losing no information.
- Heuristic coverage is unit-tested against the known set of hint patterns; new patterns are added by test-driven extension, not prompt changes.
