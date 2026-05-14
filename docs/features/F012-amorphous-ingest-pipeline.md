# F012 — Amorphous Ingest Pipeline: Schema Discovery, Ontology Derivation & Multi-format Export

**Status:** Implemented  
**Layer:** 1 — Ingestion & Parsing (extended) + Schema Discovery

## Statement

The system accepts a directory of heterogeneous data files — any mix of tabular (CSV, Parquet, Arrow IPC) and document formats — and automatically produces a typed data model, a semantic ontology, a persistable relationship graph, and a complete element tree, requiring no schema declaration from the caller. Inferred FK relationships, semantic column roles, and OWL 2 class definitions are exported to YAML, OWL Turtle, graph JSON, and element JSONL formats for storage, versioning, and downstream consumption by agents and knowledge compilers.

## The Amorphous Design Goal

The pipeline must make no assumption about the shape, domain, or relationships of the data it receives. Schema, hierarchy, relationships, and semantic roles are *derived* from the data itself — so that any directory of files can be onboarded as a first-class AKE knowledge source without a data-modelling step.

---

## Capabilities

### 1. Directory Auto-Discovery

`ingest_directory(source_dir, dataset_name?)` recursively scans a directory and routes each file to the appropriate existing ingestion path:

- **Tabular files** (`.csv`, `.parquet`, `.arrow`, `.feather`, `.arrows`) → `IngestionPipeline.ingest_tabular_file()` (F009)
- **Document files** (`.pdf`, `.docx`, `.html`, `.txt`, `.md`) → `IngestionPipeline.ingest_file()` (F001)
- Unsupported extensions are silently skipped; file order is deterministic (sorted)

Each ingested table becomes a `TableInfo` with its `IngestionResult`, full column schema (`ColumnInfo` per column), row count, and partition keys. Each document becomes a `DocumentInfo` with its `IngestionResult` and element count. The unified `AmorphousIngestionResult` provides a single `.all_elements` view across all sources.

### 2. Semantic Role Classification

Every column in every ingested table is classified into one of nine semantic roles based on its name alone — no data sampling required for the initial role assignment:

| Role | Detection rule |
|---|---|
| `entity_id` | Column is `id` or matches `{singular_table_name}_id` |
| `foreign_key` | Column ends with `_id` and is not the table's own PK |
| `label` | Column is `name`, `title`, `label`, `display_name`, `full_name` |
| `currency` | Name contains `amount`, `price`, `cost`, `revenue`, `salary`, `budget`, `fee`, `wage` |
| `date` | Name contains `date`, `_at`, `_on`, `timestamp`, `_time` |
| `categorical` | Name contains `status`, `category`, `department`, `region`, `type`, `role`, `kind` |
| `measure` | Name contains `count`, `quantity`, `qty`, `hours`, `headcount`, `score` |
| `boolean` | Name starts with `is_` or `has_` |
| `text` | Name contains `description`, `notes`, `comment`, `summary`, `bio` |

Roles are stored in `ColumnInfo.semantic_role` and promoted into the ontology as `ake:semanticRole` annotations on OWL data properties.

### 3. FK Relationship Inference

After all tables are ingested, the pipeline infers foreign-key relationships in two passes:

**Pass 1 — Naming convention.** For every `foreign_key`-role column (e.g. `team_id`, `lead_employee_id`):
1. Strip the `_id` suffix to get a base name (`team`, `lead_employee`)
2. Split on `_` and check all suffix subsequences (`lead_employee`, `employee`) against known table names in singular and plural form
3. If a matching table is found, locate the best target column (exact name match → `id` column → table's `entity_id` column)

This handles both simple FKs (`team_id → teams.team_id`) and compound FKs (`lead_employee_id → employees.employee_id`) from the same algorithm.

**Pass 2 — Value overlap confirmation.** For each naming-convention match, the element store is sampled: source column values are compared against target column values. Overlap ≥ 50 % upgrades evidence to `"both"` and increases confidence toward 0.95; partial overlap adjusts confidence proportionally. A naming-only match without any value data defaults to 0.70 confidence.

Each `InferredRelationship` carries `source_table`, `source_column`, `target_table`, `target_column`, `confidence` (float), and `evidence` (`"naming"`, `"values"`, or `"both"`).

### 4. Ontology Derivation

`build_ontology(AmorphousIngestionResult)` converts the ingestion result into a formal `Ontology` object:

- **`OntologyClass`** — one per table; name in PascalCase (e.g. `Employee`); properties list one `OntologyProperty` per column
- **`OntologyProperty`** — maps column name (snake_case) to OWL name (camelCase); pyarrow types map to XSD datatypes (`double → xsd:decimal`, `date32[day] → xsd:date`, etc.)
- **`OntologyRelationship`** — one per `InferredRelationship`; name derived from source column in camelCase (e.g. `leadEmployee`); carries domain and range class names, confidence, and evidence

### 5. Persistable Graph

`build_graph(Ontology, AmorphousIngestionResult)` returns a Cytoscape.js-compatible JSON graph:

- **Dataset node** — root, coloured `#334155`
- **Table nodes** — one per class, colour-cycled across 8 accent colours, carrying row count and property count
- **Containment edges** — dataset → table
- **FK edges** — table → table, dashed, labelled with source column name, carrying confidence and evidence

`build_element_tree(AmorphousIngestionResult)` returns a hierarchical JSON tree keyed by table name, with each row as a parsed `{fields, normalized_values}` dict — a human-navigable representation of the full element corpus.

### 6. Multi-format Export

| File | Format | Contents |
|---|---|---|
| `ontology.yaml` | YAML | Dataset metadata, per-table column schema with semantic roles, inferred relationships, OWL class/property model |
| `ontology.owl` | OWL 2 / RDF Turtle | Standards-compliant ontology via rdflib: `owl:Class`, `owl:DatatypeProperty`, `owl:ObjectProperty`; custom `ake:inferenceConfidence` and `ake:semanticRole` annotations |
| `graph.json` | JSON (Cytoscape.js) | Nodes + edges for the dataset/table/relationship graph; ready for visualization without transformation |
| `element_tree.json` | JSON | Full hierarchical element tree: `{dataset → tables → rows → {fields, normalized_values}}` |
| `elements.jsonl` | JSONL | One element per line with `element_id`, `doc_id`, `section_path`, `text`, `normalized_values`; importable by any downstream pipeline |

---

## Acceptance Criteria

- `ingest_directory()` ingests a mixed directory of CSV, Parquet, and document files without error; each file's element count matches direct ingestion via F001 / F009
- FK relationships are inferred with ≥ 0.70 confidence for all `{X}_id` columns where a table named `{X}` or `{X}s` exists and contains an `{X}_id` column
- Compound FK column names (e.g. `lead_employee_id`, `assigned_user_id`) resolve to the correct target table via the suffix-matching algorithm
- Semantic roles are assigned correctly for the full set of hint patterns; columns not matching any hint receive `"unknown"`
- `ontology.owl` parses without error in any standards-compliant RDF/OWL tool (rdflib round-trip, Protégé import)
- `ontology.yaml` is valid YAML; all scalars with special characters are quoted; structure matches the documented schema
- `graph.json` conforms to Cytoscape.js element format: every node has `data.id`, `data.label`, `data.type`; every edge has `data.source`, `data.target`
- Re-running `ingest_directory()` on unchanged data produces the same `doc_id` values for all tables (idempotency inherited from F009)
- The pipeline runs without a database store (`IngestionPipeline(store=None)`) and produces all outputs in-memory

## Differences from File-level Ingestion

| Concern | File-level (F001 / F009) | Amorphous (F012) |
|---|---|---|
| Input | Single file | Directory (any mix of formats) |
| Schema | Declared by caller | Derived automatically |
| Relationships | Not modelled | Inferred from column naming + value overlap |
| Semantic roles | Not classified | Classified per column |
| Output | `IngestionResult` (elements) | `AmorphousIngestionResult` + `Ontology` + 5 export formats |
| OWL model | None | Full OWL 2 class/property hierarchy |

## Key Files

| Concern | Location |
|---|---|
| Directory ingestion, FK inference, role classification | `ake/ingestion/amorphous_pipeline.py` |
| Ontology data model | `ake/ontology/model.py` |
| Ontology builder | `ake/ontology/builder.py` |
| Graph and element tree builders | `ake/ontology/graph.py` |
| YAML serializer | `ake/ontology/serializers/yaml_serializer.py` |
| OWL Turtle serializer (rdflib) | `ake/ontology/serializers/owl_serializer.py` |
| Example dataset and runner | `examples/amorphous-ingest/` |

## Out of Scope

- LLM-assisted entity resolution across tables — relationships are structural only in this phase; semantic equivalence (e.g. `customer_id` and `client_id` referring to the same real-world entity) requires the compiler skill library (F006)
- Live schema drift detection — re-running on modified data produces new `doc_id` values via F009's content-hash mechanism, but the pipeline does not diff ontology versions or emit change events
- Automated OWL reasoning or SPARQL query generation against the exported ontology
- Document-to-table FK inference — cross-source-type relationship detection is deferred to the knowledge graph layer (F010)
