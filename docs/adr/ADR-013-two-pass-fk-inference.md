# ADR-013 — Two-Pass FK Inference: Naming Convention Followed by Value Overlap

**Status:** Accepted  
**Date:** 2026-05-14

## Context

The amorphous pipeline (F012) must detect foreign-key relationships between tables without being told about them. This is the step that turns a flat collection of tables into a connected graph, and it is the highest-risk part of zero-declaration ingestion: a false positive (claiming a relationship that does not exist) is more harmful than a false negative (missing a relationship), because a false positive can propagate incorrect graph structure into downstream ontology exports and agent queries.

The approaches considered:

1. **No automatic detection** — Relationships must be declared by the caller, either in a config file or as arguments to `ingest_directory()`. Safe, accurate, but breaks the amorphous promise: operators must perform data-modelling work before the pipeline adds value.

2. **Pure naming convention** — For every column ending in `_id`, strip the suffix and look for a table whose name matches the base. Fast and requires no data reading, but:
   - Misclassifies coincidental naming: a `type_id` column might reference a `types` table that doesn't exist, or exist but carry unrelated data.
   - Cannot handle compound names: `lead_employee_id` doesn't have a direct base match against a table called `lead_employee`; the FK target is `employees`.

3. **Pure value overlap** — For every `*_id` column, compare its values against every `*_id` column in every other table. Declare a relationship where overlap exceeds a threshold. Accurate for clean datasets, but:
   - O(columns²) data reads; expensive for wide schemas and large tables.
   - False positives are possible when ID spaces happen to collide (e.g. both `product_id` and `order_id` starting from `1`).
   - Without a naming signal, the algorithm cannot distinguish intentional FKs from coincidental value overlap.

4. **ML / embedding-based column matching** — Embed column names and sample values; find high-similarity pairs. Requires a model, training data, and introduces non-determinism.

5. **Two-pass: naming first, value overlap as confirmation** — Use naming as the candidate-generation step (cheap, no data reading) and value overlap as the scoring / confirmation step (data reading, bounded to the named candidates). Chosen approach.

## Decision

FK inference runs in two passes per FK-candidate column:

### Pass 1 — Candidate generation from naming

For each column classified as `foreign_key` (ends with `_id`, is not the table's own PK):

1. Strip the `_id` suffix to get a **base name** (e.g. `lead_employee_id` → `lead_employee`)
2. Split on `_` to get **tokens** (`["lead", "employee"]`)
3. For each suffix subsequence of tokens (longest first: `lead_employee`, then `employee`), check both the literal form and the form with `s` appended against the set of known table names
4. The first match determines the **candidate target table**

This suffix-matching extension is what handles compound FK names like `lead_employee_id → employees` and `assigned_project_id → projects`. Without it, only simple one-segment names (`team_id → teams`) would be detected.

Within the target table, the **target column** is resolved by priority:
1. Exact match: the FK column name exists in the target (e.g. `team_id` in `teams`)
2. The column named `id`
3. The first column whose semantic role is `entity_id`

A naming-convention match alone yields **confidence 0.70** with evidence `"naming"`.

### Pass 2 — Value overlap confirmation

For each naming-convention candidate, extract the distinct non-null values from both the source FK column and the target column (via element text parsing — no re-reading of the source file). Compute:

```
overlap = |source_values ∩ target_values| / |source_values|
```

If `overlap ≥ 0.50`: evidence becomes `"both"`, confidence is scaled to `min(0.95, 0.70 + overlap × 0.25)`.  
If `0 < overlap < 0.50`: evidence becomes `"both"`, confidence is `0.60 + overlap × 0.15`.  
If `overlap = 0` (or either value set is empty): evidence remains `"naming"`, confidence stays 0.70.

Value sampling is bounded to the already-ingested elements — it reads from in-memory element text, not from disk. This avoids the O(n) file re-read cost of a pure value-overlap approach.

### What is not detected

- **Cross-type relationships**: a string `entity_id` in one table matched against an integer `id` in another — the naming pass may catch this, but the value overlap will be 0 (string vs numeric comparison), keeping confidence at 0.70.
- **Non-`_id` foreign references**: columns like `parent_code`, `category_slug`, or `iso_country` that reference another table's non-ID primary key. These are not classified as `foreign_key` by the role heuristic and are not examined.
- **Self-referential FKs within the same table**: e.g. `manager_id → employees.employee_id`. These *are* detected (the target table matches), and should work correctly.
- **Composite FKs** (referencing by multiple columns together): not modelled; relationships are always one source column to one target column.

### Confidence score interpretation

| Evidence | Confidence range | Meaning |
|---|---|---|
| `"naming"` | 0.70 | Structural match only — treat as a candidate for review |
| `"both"` (partial overlap) | 0.60–0.85 | Naming match + partial value evidence — likely correct |
| `"both"` (high overlap ≥ 50%) | 0.83–0.95 | Naming match + strong value evidence — high confidence |

Consumers may filter relationships by confidence. The YAML and OWL exports include the confidence score as a first-class annotation; the graph JSON includes it on FK edges.

## Consequences

**Positive**
- Compound FK names (the common real-world case of prefixed keys) are handled correctly without any special-case configuration.
- Value overlap acts as a guard against pure-naming false positives without requiring full table scans.
- Confidence scores give downstream consumers a principled way to filter or flag low-confidence relationships for human review.
- The algorithm runs entirely on in-memory element text (already ingested); no second pass over source files.

**Negative**
- The algorithm only examines `foreign_key`-role columns; relationships expressed through non-`_id` columns are invisible.
- Suffix matching produces candidates in suffix-longest-first order, which means the first match wins; two plausible matches for the same FK column (e.g. `employee_id` could match both `employees` and `employee_roles` if both exist) resolve to the longer suffix match. This can be wrong.
- Value overlap is computed over the full ingested element set, not a sample; for very large tables this may be slow in-memory.

**Mitigations**
- The `seen` set prevents duplicate relationships; the first match for a given `(source_table, source_column, target_table, target_column)` tuple wins and subsequent attempts are skipped.
- A future `relationship_hints` parameter to `ingest_directory()` can provide caller-supplied relationship declarations that override or supplement the inferred set.
- A `min_confidence` filter argument can be added without changing the algorithm; callers that want only high-confidence relationships pass `0.85`.
