# AKE — Amorphous Ingest Example

Auto-discover, model, and explore a directory of structured and unstructured data files with no schema declaration required.

![AKE Amorphous Ingest Viewer](assets/image.png)

## What it does

Point the pipeline at any directory containing CSV, Parquet, Arrow, HTML, PDF, or DOCX files and it will:

1. **Ingest** every file automatically — tabular files become row-level `Element` records; documents are parsed into typed elements (Title, NarrativeText, Table, etc.)
2. **Derive a schema** — column types, nullability, and semantic roles (`entity_id`, `foreign_key`, `label`, `currency`, `date`, `categorical`, `measure`, `boolean`, `text`) are classified from name patterns and pyarrow type inference alone
3. **Infer FK relationships** — a two-pass algorithm matches `*_id` columns to their target tables using naming conventions and value-overlap confirmation, producing confidence scores
4. **Build an OWL ontology** — classes, data properties, and object properties are generated in OWL 2 / RDF Turtle via rdflib
5. **Link documents to entities** — documents are matched to their related structured entities by scanning entity IDs out of filenames (e.g. `project_PR001_status.html` → `projects.project_id = PR001`)
6. **Export** to five persistent formats: YAML, OWL Turtle, Cytoscape-compatible graph JSON, hierarchical element tree JSON, and a flat JSONL element stream

## Example dataset

The included `data/` directory contains a small but realistic dataset representing a software organisation:

| File | Rows | Description |
|---|---|---|
| `data/employees.csv` | 15 | Staff records — employee_id, name, title, team, salary, hire date |
| `data/teams.csv` | 5 | Engineering and operations teams with a lead employee FK |
| `data/projects.csv` | 8 | Active and completed projects with team FK and budget |
| `data/assignments.csv` | 20 | Employee–project allocations with hours and role |
| `data/docs/team_T001_vision.html` | — | Core Platform team vision for 2025 |
| `data/docs/team_T002_vision.html` | — | Product Design team vision for 2025 |
| `data/docs/team_T003_meeting_notes.html` | — | Data Science team weekly meeting notes |
| `data/docs/project_PR001_status.html` | — | API v3 Migration status report |
| `data/docs/project_PR003_status.html` | — | Churn Prediction Model status report |
| `data/docs/project_PR006_status.html` | — | Enterprise Portal status report |
| `data/docs/assignment_A001_workspec.html` | — | Work specification for Jordan Kim on PR001 |
| `data/docs/assignment_A012_workspec.html` | — | Work specification for Taylor Nguyen on PR003 |
| `data/docs/assignment_A015_workspec.html` | — | Work specification for Taylor Nguyen on PR005 |

The pipeline infers five FK relationships automatically:

```
assignments.employee_id  →  employees.employee_id   95%  [both]
assignments.project_id   →  projects.project_id     95%  [both]
projects.team_id         →  teams.team_id           95%  [both]
employees.team_id        →  teams.team_id           95%  [both]
teams.lead_employee_id   →  employees.employee_id   95%  [both]
```

## Prerequisites

```bash
uv sync --group ingestion
```

This installs pyarrow, rdflib, networkx, unstructured, uvicorn, starlette, and the MCP SDK.

---

## Three entry points

### 1. Pipeline export — `run.py`

Ingests the data directory and writes five output files.

```bash
# Use the included example data
uv run python examples/amorphous-ingest/run.py

# Point at your own directory
uv run python examples/amorphous-ingest/run.py path/to/data/ --output out/ --dataset-name mydata
```

**Output files** (written to `output/` by default):

| File | Format | Description |
|---|---|---|
| `ontology.yaml` | YAML | Human-readable ontology: classes, properties, relationships |
| `ontology.owl` | OWL 2 / Turtle | Machine-readable RDF graph for use in Protégé or triple stores |
| `graph.json` | JSON | Cytoscape.js-compatible nodes and edges including document nodes |
| `element_tree.json` | JSON | Hierarchical tree: dataset → tables → rows / documents → elements |
| `elements.jsonl` | JSONL | Flat stream of all elements — one JSON object per line |

### 2. Interactive viewer — `view.py`

Launches a browser-based explorer. Ingests the data, builds the ontology, and opens the viewer automatically.

```bash
uv run python examples/amorphous-ingest/view.py

# Custom directory, port, or dataset name
uv run python examples/amorphous-ingest/view.py data/ --port 8080 --dataset-name acme
uv run python examples/amorphous-ingest/view.py data/ --no-browser
```

The viewer provides:

- **Graph canvas** — interactive Cytoscape.js/dagre layout showing table nodes (coloured rectangles), FK edges (dashed), and document nodes (diamonds) with dotted links to their related tables
- **Tables sidebar** — click any table to inspect its schema, rows, stats, and OWL class definition
- **Relationships sidebar** — all inferred FK relationships with confidence bars and evidence badges (`naming` vs `both`)
- **Documents sidebar** — all documents with doc-type colour tags and linked entity labels; click to view parsed elements
- **Schema tab** — column names, XSD types, and colour-coded semantic role badges
- **Rows tab** — paginated data table with column/value filtering and normalised values highlighted in green
- **Stats tab** — numeric (min / avg / max) and categorical (top-8 frequency bars) column statistics
- **Ontology tab** — OWL class definition, DatatypeProperty list, and ObjectProperty edges
- **Elements tab** — document view showing parsed element types and text with a linked-entity banner

### 3. Standalone MCP server — `mcp_server.py`

Exposes the dataset to any MCP-compatible agent with no database required. Ingests the data at startup and holds it in memory.

```bash
# SSE transport (default)
uv run python examples/amorphous-ingest/mcp_server.py

# stdio transport (for Claude Desktop or direct agent use)
uv run python examples/amorphous-ingest/mcp_server.py --stdio

# Custom data directory and host/port
uv run python examples/amorphous-ingest/mcp_server.py --data path/to/data/ --host 0.0.0.0 --port 8001
```

**MCP resources:**

```
ake://amorphous/{dataset}/tables           — all tables with row counts
ake://amorphous/{dataset}/schema/{table}   — column schema and semantic roles
ake://amorphous/{dataset}/relationships    — all inferred FK relationships
ake://amorphous/{dataset}/ontology         — full OWL class model
```

**MCP tools:**

| Tool | Description |
|---|---|
| `list_tables()` | Tables with row counts and column names |
| `get_schema(table_name)` | Column schema including XSD types, semantic roles, and relationships |
| `query_rows(table_name, column?, value?, limit?)` | Filter and paginate rows |
| `get_relationships()` | All FK relationships with confidence scores and OWL property names |
| `describe_ontology()` | Full OWL 2 class model with data and object properties |
| `describe_class(table_name)` | One class with inbound and outbound object properties |

**Suggested agent workflow:**

```
1. list_tables()           → discover what tables exist
2. get_schema("employees") → understand column types and roles
3. query_rows("employees", column="remote", value="true") → filter data
4. get_relationships()     → understand how tables connect
5. describe_ontology()     → explore the full OWL model
```

---

## Using your own data

Drop any CSV, Parquet, or Arrow files (and optionally HTML or PDF documents) into a directory and run:

```bash
uv run python examples/amorphous-ingest/view.py path/to/your/data/
```

**Document linking:** name documents using the pattern `{anything}_{ENTITY_ID}_{doc_type}.html`. For example, if your `customers` table has a `customer_id` column containing `C042`, a file named `customer_C042_contract.html` will be automatically linked to that entity and shown as a diamond node in the graph.

**Supported file types:**

| Type | Extensions |
|---|---|
| Tabular | `.csv`, `.parquet`, `.arrow`, `.feather`, `.arrows` |
| Documents | `.html`, `.htm`, `.pdf`, `.docx`, `.doc`, `.txt`, `.md` |

---

## Key source files

| Path | Role |
|---|---|
| `ake/ingestion/amorphous_pipeline.py` | Core pipeline: discovery, schema derivation, FK inference, document linking |
| `ake/ontology/builder.py` | OWL ontology construction from ingestion results |
| `ake/ontology/graph.py` | Cytoscape.js graph and element tree builders |
| `ake/ontology/serializers/` | YAML and OWL Turtle serialisers |
| `examples/amorphous-ingest/run.py` | CLI export entry point |
| `examples/amorphous-ingest/view.py` | Browser viewer entry point |
| `examples/amorphous-ingest/mcp_server.py` | Standalone MCP server |
| `examples/amorphous-ingest/viewer/app.py` | Starlette API routes |
| `examples/amorphous-ingest/viewer/static/index.html` | Single-page viewer frontend |
