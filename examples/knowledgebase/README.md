# Example: Company-wide Knowledge Base

This example demonstrates the complete AKE pipeline on realistic company
documents — an engineering handbook, an HR handbook, and a security policy —
from raw HTML to a queryable MCP server agents can consume.

![Viewer](assets/image.png)


## What you'll see

| Step | Script | Concept demonstrated |
|------|--------|---------------------|
| 1 — Ingest | `ingest.py` | Parsing HTML → Element records with stable `doc_id` |
| 2 — Section filtering | `ingest.py` | `section_path` navigation without raw text search |
| 3 — Idempotency | `ingest.py` | Same file → same `doc_id`; store skips re-parse |
| 4 — ACL propagation | `ingest.py` | Box/SharePoint group IDs on every element |
| 5 — Element JSON | `ingest.py` | What actually lands in the `elements` table |
| 6 — Compile | `mcp_server.py` | LLM extraction → typed, cited `DomainArtifact` records |
| 7 — Register | `mcp_server.py` | Schema registry for agent discovery |
| 8 — Serve | `mcp_server.py` | MCP server over SSE with AKE tools + resources |

## Prerequisites

```bash
# Install the ingestion dependency group (adds unstructured[pdf,docx])
uv sync --group ingestion
```

## Run the ingestion walkthrough

```bash
# Parse documents in-memory, print results to stdout (no database needed):
uv run python examples/knowledgebase/ingest.py

# Also persist elements to Postgres:
export DATABASE_URL=postgresql+asyncpg://ake:ake@localhost/ake
alembic upgrade head
uv run python examples/knowledgebase/ingest.py --store
```

## Run the full pipeline + MCP server

This runs ingestion, artifact compilation, MCP registry registration, and starts
an MCP server over SSE.  Agents can then query the knowledge base through the
standard AKE MCP interface.

```bash
# Prerequisites
export DATABASE_URL=postgresql+asyncpg://ake:ake@localhost/ake
export LLM_API_KEY=your-api-key           # or set llm_api_key in .env.local
alembic upgrade head

# Full pipeline: ingest → compile → register → serve
uv run python examples/knowledgebase/mcp_server.py

# Custom host/port
uv run python examples/knowledgebase/mcp_server.py --port 8080 --host 0.0.0.0

# Skip ingestion + compilation (serve existing artifacts from the database)
uv run python examples/knowledgebase/mcp_server.py --no-compile
```

The server exposes:

**Tools**
| Tool | Description |
|------|-------------|
| `ake_list_artifact_types` | Discover the `knowledgebase` domain (`kb_policy`, `kb_procedure`) |
| `ake_describe_schema` | Get field-level schemas for artifact types |
| `ake_query` | Natural-language questions against compiled artifacts |
| `ake_get_artifact` | Direct retrieval by `entity_id` |
| `ake_list_entities` | Enumerate all compiled entities |

**Resources**
| Resource | Returns |
|----------|---------|
| `ake://domains` | All registered domains |
| `ake://domains/knowledgebase` | Knowledgebase domain details + schemas |
| `ake://schema/kb_policy` | JSON Schema for policy artifacts |
| `ake://schema/kb_procedure` | JSON Schema for procedure artifacts |
| `ake://artifacts/{type}/{entity_id}` | Most recent compiled artifact |
| `ake://citations/{artifact_id}` | All citations for an artifact |
| `ake://elements/{doc_id}/{element_id}` | Raw source element |

### Adding to Claude Desktop

Open your Claude Desktop config file:

| OS | Path |
| --- | --- |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

Add an entry under `mcpServers`. The server requires a running Postgres instance and an LLM API key — pass both via the `env` block:

```json
{
  "mcpServers": {
    "ake-knowledgebase": {
      "command": "/path/to/ake/.venv/bin/python",
      "args": [
        "/path/to/ake/examples/knowledgebase/mcp_server.py",
        "--stdio",
        "--no-compile"
      ],
      "cwd": "/path/to/ake",
      "env": {
        "DATABASE_URL": "postgresql+asyncpg://ake:ake@localhost/ake",
        "LLM_API_KEY": "your-api-key"
      }
    }
  }
}
```

Run the full pipeline once before connecting Claude (this performs ingestion and LLM extraction — it only needs to run once):

```bash
cd /absolute/path/to/ake
export DATABASE_URL=postgresql+asyncpg://ake:ake@localhost/ake
export LLM_API_KEY=your-api-key
alembic upgrade head
uv run python examples/knowledgebase/mcp_server.py
```

Then restart Claude Desktop. The `--no-compile` flag in the config means subsequent connections serve existing artifacts from the database without re-running LLM extraction. Remove it only if you want to re-ingest and recompile on every startup.

### Example MCP client usage

```python
# Agents issue queries like:
ake_query(
    ask="What is the incident response procedure?",
    shape={"procedure_name": "...", "summary": "...", "sla": None},
    contexts=["kb_procedure"]
)

ake_query(
    ask="List all data classification policies",
    shape={"policies": [{"policy_name": "...", "classification": "..."}]},
    contexts=["kb_policy"]
)
```

## Source documents

```
docs/
├── engineering-handbook.html   — code review, deployment, testing, on-call
├── hr-handbook.html            — leave policy, benefits, performance reviews
└── security-policy.html        — data classification, access control, incident response
```

Each document uses a three-level HTML heading hierarchy (`h1` → `h2` → `h3`).
The normaliser converts these into `section_path` lists on every element:

```
["Code Review Process", "Reviewer Responsibilities"]
["Leave Policy", "Parental Leave"]
["Data Classification", "Confidential and Restricted"]
```

## Expected output (excerpt)

```
╔══════════════════════════════════════════════════════════╗
║  Acme Corp Knowledge Base — AKE Ingestion Walkthrough    ║
╚══════════════════════════════════════════════════════════╝

  Persistence: none — pass --store to write elements to Postgres

══════════════════════════════════════════════════════════════
  STEP 1 — Ingest all knowledge-base documents
══════════════════════════════════════════════════════════════

┌─ engineering-handbook
│  doc_id  : a3f8c2d1e9b047f6a2c5...
│  elements: 47
│  types   : {'title': 12, 'paragraph': 23, 'list': 12}
│  sections:
│    • Engineering at Acme Corp
│    └─ Code Review Process
│        └─ Opening a Pull Request
│        └─ Reviewer Responsibilities
│        └─ Response Time SLA
│    └─ Deployment Standards
│        └─ Pre-Deployment Checklist
│        └─ Release Windows
│        └─ Rollback Procedure
│    └─ Testing Standards
│    └─ On-Call Rotation
└──────────────────────────────────────────────────────────

  ✓ 3 documents → 128 total elements
    type breakdown: {'title': 32, 'paragraph': 64, 'list': 32}

══════════════════════════════════════════════════════════════
  STEP 2 — Section-path filtering
══════════════════════════════════════════════════════════════

  Filtering elements where section_path contains 'Code Review Process':

  [title    ] Engineering at Acme Corp > Code Review Process
               Code Review Process

  [paragraph] Engineering at Acme Corp > Code Review Process
               Code review is a core quality practice at Acme. Every change merged…

  [title    ] Engineering at Acme Corp > Code Review Process > Opening a Pull Request
               Opening a Pull Request
```

## Key concepts

### doc_id — content-addressed stability

`doc_id` is `sha256(raw_file_bytes).hexdigest()`. Ingesting the same file
twice always produces the same `doc_id`, allowing the store to skip re-parsing.

```python
from ake.ingestion.element import compute_doc_id

doc_id = compute_doc_id(Path("docs/engineering-handbook.html").read_bytes())
```

### section_path — semantic navigation

The normaliser tracks the most recently seen heading at each HTML level and
attaches the resulting path to every element. The compiler (F002) uses this
to find the right passage without page-number guesswork:

```python
# All elements that fall under "Code Review Process"
elements = [
    el for el in result.elements
    if "Code Review Process" in el.section_path
]
```

### ACL propagation

Pass `acl_principals` in the metadata dict and it lands on every element.
F005 reads these principals to enforce Postgres row-level security:

```python
result = await pipeline.ingest_file(
    "docs/engineering-handbook.html",
    metadata={
        "source_url": "https://wiki.acme.com/engineering/handbook",
        "acl_principals": ["group:engineering", "group:product"],
    },
)
assert result.elements[0].metadata["acl_principals"] == ["group:engineering", "group:product"]
```

### Using ElementStore

```python
from ake.db.engine import AsyncSessionLocal
from ake.store.element_store import ElementStore
from ake.ingestion.pipeline import IngestionPipeline

store = ElementStore(AsyncSessionLocal)
pipeline = IngestionPipeline(store=store)

result = await pipeline.ingest_file("docs/engineering-handbook.html")

# Retrieve later — returns same elements without re-parsing
elements = await store.get_by_doc_id(result.doc_id)
```

## Next steps

- **F002** — Artifact compilation: run the LLMRouter over these elements to
  extract typed, cited facts (policies, entitlements, contact details, SLAs).
- **F004** — Declarative query: agents ask for `{doc_type: "policy", section: "Incident Response"}`
  and get back verified, cited answers — no LLM call at query time.
- **F005** — ACL enforcement: the `acl_principals` already in every element
  become the RLS policy that controls who can query what.
