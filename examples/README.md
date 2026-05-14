# AKE Examples

Three self-contained examples that demonstrate different parts of the AKE pipeline — from simple document ingestion through to a fully database-free amorphous explorer with an interactive viewer and a standalone MCP server.

---

## [amorphous-ingest](amorphous-ingest/README.md)

**Zero-declaration schema derivation — the fastest way to get started.**

Drop any directory of CSV, Parquet, or document files in and AKE automatically:
- discovers the schema and semantic roles of every column
- infers FK relationships between tables from column naming + value overlap
- links unstructured documents (HTML, PDF, DOCX) to their related structured entities via filename
- builds an OWL 2 ontology and exports to YAML, Turtle, graph JSON, and JSONL
- serves everything through an interactive browser viewer and a standalone MCP server (no database required)

```bash
uv sync --group ingestion

# Interactive viewer
uv run python examples/amorphous-ingest/view.py

# Export pipeline
uv run python examples/amorphous-ingest/run.py

# Standalone MCP server
uv run python examples/amorphous-ingest/mcp_server.py --stdio
```

→ [Full instructions](amorphous-ingest/README.md)

---

## [knowledgebase](knowledgebase/README.md)

**Full AKE pipeline — ingestion → LLM extraction → MCP server.**

Demonstrates the complete lifecycle on three company policy documents (engineering handbook, HR handbook, security policy). Uses LLM extraction to compile typed, cited `DomainArtifact` records into Postgres, then serves them through the standard AKE MCP interface.

Requires: Postgres + pgvector, an LLM API key.

```bash
uv sync --group ingestion

export DATABASE_URL=postgresql+asyncpg://ake:ake@localhost/ake
export LLM_API_KEY=your-api-key
alembic upgrade head

uv run python examples/knowledgebase/mcp_server.py
```

→ [Full instructions](knowledgebase/README.md)

---

## [outdoor_retail](outdoor_retail/README.md)

**Tabular ingestion with direct column mapping — no LLM calls.**

A simulated outdoor-retail business (5 store locations, 20 employees, 30 products, 110 sales transactions). Demonstrates how structured data compiles to typed artifacts using direct CSV column→field mapping (ADR-009) with no LLM extraction at compile time.

Requires: Postgres + pgvector.

```bash
uv sync --group ingestion

export DATABASE_URL=postgresql+asyncpg://ake:ake@localhost/ake
alembic upgrade head

uv run python examples/outdoor_retail/mcp_server.py
```

→ [Full instructions](outdoor_retail/README.md)

---

## Choosing an example

| | amorphous-ingest | knowledgebase | outdoor_retail |
|---|---|---|---|
| Database required | No | Yes (Postgres) | Yes (Postgres) |
| LLM API key required | No | Yes | No |
| Source type | CSV + HTML/PDF | HTML documents | CSV |
| Schema declaration | None — auto-derived | Manual domain config | Manual domain config |
| MCP server | Standalone (in-memory) | Full AKE stack | Full AKE stack |
| Best for | Quick exploration, any dataset | Document Q&A agents | Structured data agents |
