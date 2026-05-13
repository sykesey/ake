# Example: Company-wide Knowledge Base

This example walks through the AKE ingestion pipeline (F001) on a set of
realistic company documents — an engineering handbook, an HR handbook, and
a security policy — showing how raw HTML is turned into normalised, citable
Element records ready for semantic search and artifact compilation.

## What you'll see

| Step | Concept demonstrated |
|------|---------------------|
| 1 — Ingest | Parsing HTML → Element records with stable `doc_id` |
| 2 — Section filtering | `section_path` navigation without raw text search |
| 3 — Idempotency | Same file → same `doc_id`; store skips re-parse |
| 4 — ACL propagation | Box/SharePoint group IDs on every element |
| 5 — Element JSON | What actually lands in the `elements` table |

## Prerequisites

```bash
# Install the ingestion dependency group (adds unstructured[pdf,docx])
uv sync --group ingestion
```

## Run it

```bash
# Parse documents in-memory, print results to stdout (no database needed):
uv run python examples/knowledgebase/ingest.py

# Also persist elements to Postgres:
export DATABASE_URL=postgresql+asyncpg://ake:ake@localhost/ake
alembic upgrade head
uv run python examples/knowledgebase/ingest.py --store
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
