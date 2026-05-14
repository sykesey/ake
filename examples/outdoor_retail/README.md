# Example: Summit & Trail Outdoor Co. — Tabular Ingestion

This example demonstrates the AKE **tabular ingestion pipeline (F009)** on a
simulated outdoor-retail business with five structured CSV datasets. Unlike the
[knowledgebase example](../knowledgebase/) which uses LLM extraction, this
example uses **direct column→field mapping (ADR-009)** — every artifact field
is read directly from a CSV column, with no LLM calls at compilation time.


![Viewer](assets/image.png)

## Dataset

Summit & Trail Outdoor Co. operates five retail stores across the Pacific
Northwest. The data spans 20 employees, 30 products, and 110 sales
transactions from Q1 2025.

| Table | Rows | Domain | ACL | Description |
|-------|------|--------|-----|-------------|
| `locations` | 5 | operations | `group:all-employees` | Store locations with city, region, type, sq. footage |
| `employees` | 20 | hr | `group:hr`, `group:management` | Staff records with department, title, hire date, salary |
| `products` | 30 | merchandising | `group:all-employees` | Outdoor catalog: brand, category, cost, price, weight |
| `inventory` | 150 | operations | `group:all-employees` | Stock levels per location per product with reorder points |
| `sales` | 110 | finance | `group:finance`, `group:management` | Transactions with location, product, employee, revenue |

## What you'll see

| Step | Script | Concept demonstrated |
|------|--------|---------------------|
| 1 — Ingest | `ingest.py` | CSV rows → `Element(type="row")` with `col: val` text |
| 2 — Column filtering | `ingest.py` | Text-based `col: val` format enables citation verification |
| 3 — Normalization | `ingest.py` | Dates/numerics pre-normalized in `metadata.normalized_values` |
| 4 — Idempotency | `ingest.py` | Same file → same `doc_id`; schema change triggers re-ingestion |
| 5 — Element JSON | `ingest.py` | What actually lands in the `elements` table |
| 6 — Compile | `mcp_server.py` | Direct column mapping → typed, cited `DomainArtifact` records |
| 7 — Register | `mcp_server.py` | Schema registry for five `retail_*` artifact types |
| 8 — Serve | `mcp_server.py` | MCP server over SSE with AKE tools + resources |
| 9 — Visualize | `view.py` | In-browser tabular element graph viewer |

## Prerequisites

```bash
# Install the ingestion dependency group (adds pyarrow, uvicorn, starlette)
uv sync --group ingestion
```

## Run the ingestion walkthrough

```bash
# Parse CSVs in-memory, print results to stdout (no database needed):
uv run python examples/outdoor_retail/ingest.py

# Also persist elements to Postgres:
export DATABASE_URL=postgresql+asyncpg://ake:ake@localhost/ake
alembic upgrade head
uv run python examples/outdoor_retail/ingest.py --store
```

## Run the data viewer

Launches a local web server with an interactive graph view of all five tables
and their row elements. No database required — everything runs in-memory.

```bash
uv run python examples/outdoor_retail/view.py

# Custom port, headless mode:
uv run python examples/outdoor_retail/view.py --port 8080 --no-browser
```

## Run the full pipeline + MCP server

This runs ingestion, direct tabular compilation, MCP registry registration,
and starts an MCP server over SSE. Agents can then query the outdoor retail
data through the standard AKE MCP interface.

```bash
# Prerequisites
export DATABASE_URL=postgresql+asyncpg://ake:ake@localhost/ake
alembic upgrade head

# Full pipeline: ingest → compile → register → serve
uv run python examples/outdoor_retail/mcp_server.py

# Custom host/port
uv run python examples/outdoor_retail/mcp_server.py --port 8080 --host 0.0.0.0

# Stdio transport (for MCP client subprocess integration)
uv run python examples/outdoor_retail/mcp_server.py --stdio

# Skip ingestion + compilation (serve existing artifacts from the database)
uv run python examples/outdoor_retail/mcp_server.py --no-compile

# Purge and re-ingest all outdoor_retail artifacts
uv run python examples/outdoor_retail/mcp_server.py --force-reingest
```

The server exposes:

**Tools**
| Tool | Description |
|------|-------------|
| `ake_list_artifact_types` | Discover the `outdoor_retail` domain and its five artifact types |
| `ake_describe_schema` | Get field-level schemas for `retail_location`, `retail_employee`, etc. |
| `ake_query` | Natural-language questions against compiled artifacts |
| `ake_get_artifact` | Direct retrieval by `entity_id` |
| `ake_list_entities` | Enumerate all compiled entities |

**Resources**
| Resource | Returns |
|----------|---------|
| `ake://domains` | All registered domains |
| `ake://domains/outdoor_retail` | Domain details + schemas for all five artifact types |
| `ake://schema/retail_location` | JSON Schema for location artifacts |
| `ake://schema/retail_employee` | JSON Schema for employee artifacts |
| `ake://schema/retail_product` | JSON Schema for product artifacts |
| `ake://schema/retail_inventory` | JSON Schema for inventory artifacts |
| `ake://schema/retail_sale` | JSON Schema for sale artifacts |
| `ake://artifacts/{type}/{entity_id}` | Most recent compiled artifact |
| `ake://citations/{artifact_id}` | All `TabularRef` citations for an artifact |
| `ake://elements/{doc_id}/{element_id}` | Raw source row element |

### Adding to Claude Desktop

Open your Claude Desktop config file:

| OS | Path |
| --- | --- |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

Add an entry under `mcpServers`. The server requires a running Postgres instance — pass `DATABASE_URL` via the `env` block:

```json
{
  "mcpServers": {
    "ake-outdoor-retail": {
      "command": "uv",
      "args": [
        "run",
        "python",
        "examples/outdoor_retail/mcp_server.py",
        "--stdio"
      ],
      "cwd": "/absolute/path/to/ake",
      "env": {
        "DATABASE_URL": "postgresql+asyncpg://ake:ake@localhost/ake"
      }
    }
  }
}
```

Run the ingestion and compilation once before connecting Claude:

```bash
cd /absolute/path/to/ake
export DATABASE_URL=postgresql+asyncpg://ake:ake@localhost/ake
alembic upgrade head
uv run python examples/outdoor_retail/mcp_server.py --no-compile  # or omit to re-compile
```

Then restart Claude Desktop. On the next connection the server will detect existing artifacts and serve them immediately. Use `--no-compile` in the `args` list above if you want Claude to connect without re-running ingestion on every startup.

### Example MCP client usage

```python
# Agents issue queries like:
ake_query(
    ask="Which products in the Hiking category have a unit price over $150?",
    shape={"products": [{"product_id": "...", "name": "...", "unit_price": 0.0}]},
    contexts=["retail_product"]
)

ake_query(
    ask="Show me all sales at the Portland location with revenue over $500",
    shape={"sales": [{"sale_id": "...", "sale_date": "...", "revenue": 0.0}]},
    contexts=["retail_sale"]
)

ake_query(
    ask="What is the inventory status for Hiking products at the Bend store?",
    shape={"inventory": [{"product_id": "...", "quantity_on_hand": 0, "reorder_point": 0}]},
    contexts=["retail_inventory"]
)
```

## Artifact types

| Artifact type | Entity ID | Key fields |
|---------------|-----------|------------|
| `retail_location` | `location_id` | name, city, state, region, store_type, opened_date, sq_ft |
| `retail_employee` | `employee_id` | first_name, last_name, department, title, location_id, hire_date, salary, status |
| `retail_product` | `product_id` | sku, name, category, brand, unit_cost, unit_price, weight_lbs |
| `retail_inventory` | `inventory_id` | location_id, product_id, quantity_on_hand, quantity_allocated, reorder_point, last_restocked |
| `retail_sale` | `sale_id` | location_id, product_id, employee_id, sale_date, quantity, unit_price, revenue, discount_pct |

## Source data

```
data/
├── locations.csv   — 5 stores (Portland, Bend, Seattle, Boise, Spokane)
├── employees.csv   — 20 staff across departments (Retail, Outdoor Education, Finance, HR, Operations)
├── products.csv    — 30 products in 5 categories (Hiking, Camping, Climbing, Water Sports, Winter Sports)
├── inventory.csv   — 150 stock positions (every product × every location)
└── sales.csv       — 110 transactions from Q1 2025 (Jan–Mar)
```

## Key concepts

### `col: val` row text format

Each CSV row becomes one `Element(type="row")` with text in `col: val` format.
This is the foundation for `TabularRef` citation verification (ADR-008):

```
location_id: L001
name: Summit & Trail - Portland
city: Portland
state: OR
region: Pacific Northwest
store_type: flagship
opened_date: 2018-03-15
sq_ft: 18500
```

### Schema-aware normalization (ADR-009)

Numeric columns (`salary`, `revenue`, `unit_price`) and date columns
(`hire_date`, `sale_date`, `last_restocked`) are pre-normalized at ingest
time and stored in `metadata.normalized_values`. The compiler's direct-mapping
path reads these without an LLM call:

```python
element.metadata["normalized_values"]
# → {"hire_date": "2023-06-01", "salary": "72500.00"}
```

### Direct column mapping (ADR-009)

Unlike the knowledgebase example which invokes an LLM to extract facts from
prose, this example maps CSV columns directly to artifact fields. Column
names match field names exactly — `unit_price` in the CSV maps to
`unit_price` in `retail_product`. Each mapping produces a `TabularRef`
citation pointing to the source cell.

### `doc_id` — content-addressed stability

`doc_id` is computed from `sha256(source_uri + schema_fingerprint + content_hash)`.
Ingesting the same CSV twice produces the same `doc_id`. A schema change
(column added, renamed, or reordered) produces a different `doc_id`,
triggering full re-ingestion and re-compilation.

### ACL propagation

Each table entry in `DATASETS` includes `acl_principals` that propagate to
every row element. F005 reads these to enforce Postgres row-level security —
for example, `group:finance` can see sales data while `group:all-employees`
cannot.

## How this differs from the knowledgebase example

| | Knowledgebase | Outdoor Retail |
|---|---|---|
| **Source type** | HTML documents (prose) | CSV files (tabular) |
| **Element type** | `title`, `paragraph`, `list` | `row` |
| **Text format** | Natural language paragraphs | `col: val` pairs |
| **Compilation** | LLM extraction (F002) | Direct column mapping (ADR-009) |
| **Citation type** | `DocumentRef` (char spans) | `TabularRef` (column + row) |
| **Normalizer** | HTML heading → section_path | CSV schema → normalized_values |
| **LLM calls** | Yes (extraction + entity resolution) | None (direct mapping only) |

## Next steps

- **F009** — Tabular data ingestion: explore the parquet-backed pipeline and
  Arrow IPC integration in `ake/ingestion/`.
- **ADR-009** — Direct mapping vs LLM extraction: understand when to use
  column mapping versus extraction prompts.
- **F005** — ACL enforcement: the `acl_principals` already on every element
  become the RLS policy that controls who can query which table.
- **F004** — Declarative query: agents ask structured questions and get back
  verified, cited answers with no LLM call at query time.