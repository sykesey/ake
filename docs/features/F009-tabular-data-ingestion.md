# F009 — Tabular & Columnar Data Ingestion

**Status:** Defined  
**Layer:** 1 — Ingestion & Parsing (extended)

## Statement

The system ingests structured tabular sources (Parquet files, CSV, Arrow datasets, database table extracts) and produces normalized `Element` records using a row-oriented mapping — so that tabular data participates in the same artifact compilation and query pipeline as document-derived elements without special-casing downstream.

## Acceptance Criteria

- Parquet, CSV, and Arrow IPC formats ingest without error
- Each row produces one `Element` of type `"row"`; each cell value is represented in `text` as `"column_name: value"` pairs (newline-separated) to preserve column identity for the extraction skill library
- `section_path` reflects the dataset hierarchy: `[dataset_name, table_name]` for flat files; `[schema, table]` for database extracts
- `doc_id` is a stable hash of `(source_uri, schema_fingerprint, content_hash)` — schema changes and data changes both invalidate it
- Column schema (names, types, nullability) is stored in `metadata.column_schema` for downstream use by the compiler and skill library
- Row-level source ACLs are propagated where the source system supports them (e.g. BigQuery column-level policy tags, Snowflake row access policies)
- Re-ingesting an unchanged table produces an identical `doc_id` and element set (idempotency)
- Large tables (> 1M rows) are ingested in streaming batches without loading the full dataset into memory

## Key Behaviours

- **Schema-aware normalizer** — the tabular normalizer reads column types from the source schema and applies `normalize_currency` / `normalize_date` skills at ingest time for known numeric and date columns, rather than relying on the LLM extraction prompt to do this
- **Partition-aware ingestion** — partitioned Parquet datasets (e.g. Hive-style `year=2024/month=01/`) propagate partition key-values into `element.metadata.partition` and optionally into promoted artifact columns
- **Sparse row handling** — rows where all value cells are null are not emitted as `Element` records; the row count in `metadata` reflects both total and non-sparse rows
- **Column fingerprint in `doc_id`** — a schema change (column added, renamed, or retyped) produces a new `doc_id`, triggering re-ingestion and re-compilation; this prevents artifacts compiled against an old schema from being silently served against new data

## Differences from Document Ingestion

| Concern | Document (F001) | Tabular (F009) |
|---|---|---|
| `element.type` | `title`, `paragraph`, `table`, … | `row` |
| `element.text` | raw prose / table cell text | `col: val\ncol: val` pairs |
| `section_path` | heading hierarchy | `[dataset, table]` |
| citation addressing | `char_start` / `char_end` | `row_id` + `column_name` (see ADR-008) |
| LLM extraction | required for fact extraction | optional; direct mapping preferred (see ADR-009) |

## Out of Scope

- Real-time / streaming ingestion (change-data-capture, Kafka topics) — batch snapshots only in this phase
- Multi-table joins resolved at ingest time — cross-table relationships are expressed in the knowledge graph layer (F010)
- Columnar aggregation or pre-aggregated materialized views
