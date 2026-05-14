# F006 — Extraction Skill Library

**Status:** Implemented  
**Layer:** 2 — Artifact Compilation

## Statement

The system provides a library of tested, reusable extraction primitives that both hand-written domain compilers and the automated compiler loop can compose — so that domain-specific extraction code stays thin and cross-domain improvements flow from a single location.

## Acceptance Criteria

- Core skills are implemented, tested, and available before any domain-specific compiler is written
- Each skill has a typed signature and at least one unit test with a document fixture
- The compiler loop for domain 2 reuses ≥ 3 skills from the library (validating generalization)
- Skills handle edge cases for currency and date normalization (e.g. `"$1.2B"`, `"1,200 million"`, `"USD 1.2bn"` all → float millions)

## Core Skills

| Skill | Description |
|---|---|
| `extract_table(elements, heading)` | Locate a table by heading and return rows as dicts |
| `find_section(elements, path)` | Retrieve elements matching a `section_path` prefix |
| `extract_named_entities(text, types)` | Extract ORGs, dates, currencies from a text span |
| `normalize_currency(value_str)` | Normalize varied currency expressions → float millions |
| `normalize_date(date_str)` | Normalize date expressions → `datetime.date` |
| `locate_by_proximity(elements, anchor_text, window)` | Find elements near a known anchor phrase |
| `resolve_entity(name, entity_registry)` | Map an entity mention to a canonical ID |

## Key Behaviours

- **Composability** — the compiler loop's refine prompt lists available skill signatures; the LLM composes them rather than writing new extraction logic
- **No LLM calls inside skills** — skills are deterministic Python; LLM calls happen only in the extraction prompt and the composer
- **Tested against real document fixtures** — each skill is validated on actual parser output, not synthetic strings

## Out of Scope

- Domain-specific extraction logic (that belongs in each domain's `curate_code`)
- Skills that wrap LLM calls (those belong in the compiler prompt layer)
