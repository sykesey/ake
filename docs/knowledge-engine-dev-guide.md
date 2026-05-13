# Domain-Agnostic Knowledge Engine: Agentic Development Guide

> A structured guide for building a pre-compiled artifact retrieval system for agentic workflows, inspired by the Nexus Knowledge Engine architecture.

---

## Overview

This guide drives an agentic development process across four layers:

1. **Ingestion & Parsing** — normalize raw sources into structured elements
2. **Artifact Compilation** — extract typed, cited facts into a queryable store
3. **Declarative Query Layer** — agents declare what they want; the engine retrieves it
4. **Compiler Loop** — LLM-driven harness that auto-tunes extraction per domain

Each layer has a definition of done, a test contract, and explicit handoff criteria to the next layer. Build and validate them in order.

---

## Guiding Principles

- **Compile at ingest, not at query time.** Expensive LLM extraction happens once per document, not on every agent call.
- **Citations are non-negotiable.** Every artifact field carries a source reference. An ungrounded value is a null.
- **Nulls are first-class.** "Not disclosed" must be representable. Never force a model to fill a field.
- **Separation of concerns.** Structured filtering (Postgres), semantic lookup (pgvector/Qdrant), and permission enforcement are distinct subsystems with thin interfaces.
- **Boring stack first.** Don't introduce polyglot persistence until a specific access pattern demands it.

---

## Repository Structure

```
knowledge-engine/
├── ingestion/
│   ├── parsers/          # Document parsers (PDF, DOCX, HTML, etc.)
│   ├── normalizer.py     # Normalize parser output → Element schema
│   └── pipeline.py       # Ingestion orchestration
├── compiler/
│   ├── skills/           # Reusable extraction primitives
│   ├── prompts/          # Extraction prompt templates
│   ├── artifact_compiler.py
│   └── compiler_loop.py  # Eval-driven auto-tuning harness
├── store/
│   ├── artifact_store.py # Postgres CRUD + vector index
│   ├── schema.sql        # Table definitions
│   └── acl.py            # Row-level security helpers
├── query/
│   ├── planner.py        # Translate Query → retrieval plan
│   ├── composer.py       # Shape artifacts into typed response
│   └── interface.py      # Public Query / QueryResult models
├── evals/
│   ├── grader.py         # LLM-as-judge + exact match
│   └── sets/             # Per-domain eval JSONL files
├── tests/
└── docs/
```

---

## Layer 1: Ingestion & Parsing

### Goal

Transform raw source documents into normalized, element-tagged JSON that downstream compilation can consume without knowing the original format.

### Output Schema

Every parser must produce `Element` records:

```python
@dataclass
class Element:
    doc_id: str               # stable hash of source content
    element_id: str           # unique within doc
    type: Literal["title", "paragraph", "table", "list", "figure", "header"]
    text: str
    page: int
    section_path: list[str]   # e.g. ["Item 7", "Capital Returns", "Share Repurchases"]
    metadata: dict            # ACLs, source URL, ingest timestamp, file permissions
```

The `section_path` is critical — it lets the compiler navigate documents by semantic location rather than raw search.

### Implementation Tasks

- [ ] Select and wrap a parser library (Unstructured, Docling, or LlamaParse)
- [ ] Implement `normalizer.py` to map parser-specific output → `Element`
- [ ] Implement `section_path` extraction from heading hierarchy
- [ ] Store parsed output keyed by `(source_id, content_hash)` for incremental re-ingestion
- [ ] Propagate source ACLs (e.g. from Box, SharePoint) into `metadata.acl_principals`
- [ ] Write `pipeline.py` orchestrating fetch → parse → normalize → store

### Definition of Done

- [ ] 10 documents from each target source type (PDF, DOCX, HTML) parse without error
- [ ] `section_path` is populated and accurate on a manual spot-check of 20 elements per doc type
- [ ] Re-ingesting an unchanged document produces an identical `doc_id` and element set
- [ ] Parser output is stored and retrievable by `doc_id`

### Test Contract

```python
def test_element_schema_compliance(parsed_output):
    for element in parsed_output:
        assert element.doc_id and element.element_id
        assert element.type in VALID_ELEMENT_TYPES
        assert isinstance(element.section_path, list)
        assert element.metadata.get("source_url") is not None

def test_idempotent_ingestion(doc_path):
    result_a = ingest(doc_path)
    result_b = ingest(doc_path)
    assert result_a.doc_id == result_b.doc_id
    assert len(result_a.elements) == len(result_b.elements)
```

### Handoff Criteria to Layer 2

All documents for the first target domain are parsed, normalized, and stored. `section_path` accuracy is validated manually. Ingestion pipeline runs end-to-end without errors.

---

## Layer 2: Artifact Compilation

### Goal

Extract typed, structured, cited fact records (artifacts) from parsed elements. One artifact per entity per document. LLM extraction happens here, once, at ingest time.

### Artifact Schema Pattern

Define one Pydantic model per domain. All domain schemas follow this contract:

```python
from pydantic import BaseModel
from dataclasses import dataclass

@dataclass
class Citation:
    element_id: str
    char_start: int
    char_end: int
    verbatim_span: str  # the actual text this value was grounded in

class DomainArtifact(BaseModel):
    # --- identity ---
    artifact_id: str          # deterministic hash of (doc_id, entity_id, artifact_type)
    doc_id: str
    entity_id: str            # e.g. company CIK, contract ID
    artifact_type: str        # e.g. "financials_10k", "contract_terms"
    fiscal_year: int | None   # promoted to column, not buried in JSONB

    # --- typed domain fields ---
    # ... domain-specific fields here ...

    # --- provenance (always present) ---
    field_citations: dict[str, Citation]   # field_name → Citation
    compiled_at: datetime
    compiler_version: str
```

**Rule:** every non-null domain field must have a corresponding entry in `field_citations`. The citation verifier enforces this before storage.

### Extraction Prompt Pattern

```python
EXTRACTION_PROMPT = """
You are extracting structured facts from the following document sections.

Document ID: {doc_id}
Entity: {entity_name}
Sections provided:
{elements_text}

Extract the following fields according to the schema. For each field you populate:
- Provide the value
- Provide the exact verbatim span from the text that supports it
- Provide the element_id it came from

If a field is not disclosed or cannot be found, set it to null. Do not infer or estimate.

Respond only with valid JSON matching the schema. No preamble.

Schema:
{schema_json}
"""
```

### Citation Verification

Run this after every extraction before persisting:

```python
def verify_citations(artifact: DomainArtifact, elements: list[Element]) -> list[str]:
    """Returns list of field names where citation verification failed."""
    element_map = {e.element_id: e for e in elements}
    failures = []
    for field_name, citation in artifact.field_citations.items():
        element = element_map.get(citation.element_id)
        if element is None:
            failures.append(field_name)
            continue
        span = element.text[citation.char_start:citation.char_end]
        if citation.verbatim_span not in span:
            failures.append(field_name)
    return failures
```

Fields that fail citation verification are nulled before storage, not stored with bad provenance.

### Skill Library

Build these reusable extraction primitives before writing domain-specific code. The compiler loop draws on them:

| Skill | Description |
|---|---|
| `extract_table(elements, heading)` | Locate a table by its heading and return rows as dicts |
| `find_section(elements, path)` | Retrieve elements matching a `section_path` prefix |
| `extract_named_entities(text, types)` | Extract ORGs, dates, currencies from a text span |
| `normalize_currency(value_str)` | Normalize "$1.2B", "1,200 million", "USD 1.2bn" → float millions |
| `normalize_date(date_str)` | Normalize date expressions → `datetime.date` |
| `locate_by_proximity(elements, anchor_text, window)` | Find elements near a known anchor phrase |
| `resolve_entity(name, entity_registry)` | Map an entity mention to a canonical ID |

### Storage (Postgres)

```sql
CREATE TABLE artifacts (
    artifact_id       TEXT PRIMARY KEY,
    doc_id            TEXT NOT NULL REFERENCES documents(doc_id),
    entity_id         TEXT NOT NULL,
    artifact_type     TEXT NOT NULL,
    fiscal_year       INT,                    -- promoted column, not in JSONB
    acl_principals    TEXT[],                 -- for row-level security
    payload           JSONB NOT NULL,         -- typed domain fields
    field_citations   JSONB NOT NULL,         -- field_name → citation
    embedding         VECTOR(1536),           -- on artifact summary text
    compiled_at       TIMESTAMPTZ NOT NULL,
    compiler_version  TEXT NOT NULL
);

-- Always index promoted columns, not JSONB paths
CREATE INDEX ON artifacts (entity_id, artifact_type, fiscal_year);
CREATE INDEX ON artifacts USING GIN (acl_principals);
CREATE INDEX ON artifacts USING hnsw (embedding vector_cosine_ops);

-- Row-level security
ALTER TABLE artifacts ENABLE ROW LEVEL SECURITY;
CREATE POLICY acl_policy ON artifacts
    USING (acl_principals && current_setting('app.current_principals')::text[]);
```

**Critical:** any field the query planner will filter on (`entity_id`, `artifact_type`, `fiscal_year`) must be a real column. Never bury a filter field inside JSONB.

### Definition of Done

- [ ] Citation verifier passes on ≥95% of fields across a 50-document sample
- [ ] Re-compiling an unchanged document produces the same artifact (temperature=0, seeded)
- [ ] Null fields are correctly represented as `null`, not empty strings or zeros
- [ ] Artifacts are retrievable from Postgres by `(entity_id, artifact_type, fiscal_year)`
- [ ] Storage schema has row-level security enabled and tested

### Test Contract

```python
def test_citation_coverage(artifact):
    for field, value in artifact.payload.items():
        if value is not None:
            assert field in artifact.field_citations, \
                f"Non-null field '{field}' has no citation"

def test_no_hallucinated_citations(artifact, elements):
    failures = verify_citations(artifact, elements)
    assert len(failures) == 0, f"Citation failures: {failures}"

def test_artifact_idempotency(doc, entity):
    a1 = compile_artifact(doc, entity)
    a2 = compile_artifact(doc, entity)
    assert a1.artifact_id == a2.artifact_id
    assert a1.payload == a2.payload
```

### Handoff Criteria to Layer 3

Artifacts for the first domain are compiled, verified, and stored. Citation coverage ≥95%. Postgres schema is deployed with RLS enabled.

---

## Layer 3: Declarative Query Interface

### Goal

Provide a single typed interface that agents call to retrieve knowledge. Agents declare *what* they want and receive a typed, cited response. No agent-side loops over raw data.

### Public Interface

```python
class Query(BaseModel):
    ask: str                          # natural language question
    shape: dict                       # JSON schema of the desired response
    filters: dict = {}                # entity IDs, date ranges, etc.
    contexts: list[str] = []          # which artifact_types to search
    ground: bool = True               # require citations in response
    budget: QueryBudget = QueryBudget(max_artifacts=20, timeout_seconds=30)

class Citation(BaseModel):
    field: str
    element_id: str
    verbatim_span: str
    doc_id: str

class QueryResult(BaseModel):
    data: dict                         # conforms to query.shape
    citations: list[Citation]
    artifacts_used: list[str]          # artifact_ids
    latency_ms: int
    token_cost: int

def execute(query: Query, principal: User) -> QueryResult:
    ...
```

### Query Execution Pipeline

```
Query
  │
  ▼
Planner ──────────────────────────────────────────────┐
  │  Emits: RetrievalPlan                             │
  │  {artifact_types, structured_filters,             │
  │   semantic_query, max_results}                    │
  ▼                                                   │
Fetcher                                               │
  │  - Structured lookup (Postgres, ACL-filtered)     │
  │  - Semantic lookup (pgvector) if needed           │
  │  - Merge on artifact_id                           │
  ▼                                                   │
Composer                                              │
  │  - Small LLM call                                 │
  │  - Input: artifacts JSON + query.ask              │
  │  - Output: query.shape-conformant JSON            │
  │  - Citations threaded through from artifacts      │
  ▼                                                   │
QueryResult ◄─────────────────────────────────────────┘
```

### Planner Implementation

Start with the simple version. Upgrade only when it misses retrieval targets on evals.

**Simple planner (keyword matching):**
```python
def plan(query: Query) -> RetrievalPlan:
    # match query.contexts and field names in query.shape
    # against artifact_type descriptions to pick collections
    artifact_types = match_contexts(query.contexts, query.shape, ARTIFACT_TYPE_REGISTRY)
    structured_filters = extract_filters(query.filters)
    semantic_query = query.ask if not structured_filters else None
    return RetrievalPlan(
        artifact_types=artifact_types,
        structured_filters=structured_filters,
        semantic_query=semantic_query,
        max_results=query.budget.max_artifacts
    )
```

**Upgraded planner (LLM-based, add when needed):**
```python
PLANNER_PROMPT = """
Given this query and available artifact types, emit a retrieval plan as JSON.

Query: {ask}
Desired shape: {shape}
Available artifact types: {artifact_registry}
Explicit filters: {filters}

Return: {"artifact_types": [...], "structured_filters": {...}, "semantic_query": "..."}
"""
```

### Composer Implementation

The composer is deliberately small. By the time it runs, the artifacts already contain the answer — it's just reshaping JSON into `query.shape`.

```python
COMPOSER_PROMPT = """
You are composing a response from pre-retrieved knowledge artifacts.

Question: {ask}
Required response shape: {shape}

Artifacts:
{artifacts_json}

Instructions:
- Populate every field in the shape from the artifact data
- Do not infer or estimate. If a value is not present in the artifacts, set it to null
- Return only valid JSON conforming to the shape. No preamble.
"""
```

### Definition of Done

- [ ] `execute()` returns a `QueryResult` conforming to `query.shape` for 10 hand-crafted queries per domain
- [ ] ACL enforcement verified: a principal without access to an artifact does not see it in results
- [ ] Citations in `QueryResult` trace back to real elements in the document store
- [ ] Latency under 10 seconds for queries resolved from ≤20 artifacts (excluding first cold start)
- [ ] `execute()` is idempotent: same query + same artifacts → same result

### Test Contract

```python
def test_acl_enforcement(query, unauthorized_principal):
    result = execute(query, principal=unauthorized_principal)
    for artifact_id in result.artifacts_used:
        artifact = store.get(artifact_id)
        assert unauthorized_principal.id not in artifact.acl_principals

def test_shape_conformance(query):
    result = execute(query, principal=test_principal)
    validate(result.data, query.shape)   # JSON schema validation

def test_citations_are_grounded(result):
    for citation in result.citations:
        element = document_store.get_element(citation.element_id)
        assert citation.verbatim_span in element.text
```

### Handoff Criteria to Layer 4

The query layer returns correct, cited, shape-conformant results on all hand-crafted test queries. Planner correctly routes to the right artifact types. ACL enforcement is validated.

---

## Layer 4: Compiler Loop

### Goal

Automate the artifact schema design and extraction code for new domains using an eval-driven agentic harness. A domain expert provides representative questions and ground-truth answers; the compiler produces a working Context without requiring retrieval expertise.

### Inputs Required Per Domain

| Input | Description | Format |
|---|---|---|
| Eval set | 50–200 `(question, ground_truth)` pairs | JSONL |
| Source corpus | Parsed documents for this domain | Layer 1 output |
| Domain description | 1–3 sentence description of the domain | Plain text |
| Skill library | Available extraction primitives | Python module |

### Eval Set Format

```jsonl
{"id": "q001", "question": "What was NVIDIA's total share repurchase amount in FY2022?", "answer": {"value": 10040, "unit": "usd_millions"}, "difficulty": "single_fact", "entities": ["NVDA"]}
{"id": "q002", "question": "Compare share repurchases across NVIDIA, MSFT, WMT in FY2022.", "answer": {"NVDA": 10040, "MSFT": 32696, "WMT": 9760}, "difficulty": "multi_company", "entities": ["NVDA", "MSFT", "WMT"]}
```

Tag each question with `difficulty`: `single_fact`, `multi_fact`, `multi_company`, or `multi_step`. The compiler loop should track pass rates per difficulty tier, not just overall.

### Compiler Loop

```python
def compile_context(
    domain_description: str,
    eval_set: list[EvalItem],
    corpus: list[ParsedDoc],
    skills: SkillLibrary,
    max_iters: int = 20,
    threshold: float = 0.85
) -> CompiledContext:

    # bootstrap: LLM proposes initial schema and extraction code
    schema_code = bootstrap_schema(domain_description, eval_set[:5], skills)
    curate_code = bootstrap_curate(schema_code, skills)
    query_code  = bootstrap_query(schema_code)

    best_score = 0.0
    best_context = None

    for iteration in range(max_iters):
        # curate: compile artifacts from corpus using current code
        artifacts = run_curate(curate_code, corpus)

        # evaluate: run all eval queries against compiled artifacts
        results = [run_query(query_code, item.question, artifacts)
                   for item in eval_set]

        # grade: LLM judge + exact match where applicable
        score, failure_report = grade(results, eval_set)

        if score > best_score:
            best_score = score
            best_context = CompiledContext(schema_code, curate_code, query_code)

        if score >= threshold:
            return best_context

        # refine: LLM reads failures and proposes code diffs
        schema_code, curate_code, query_code = refine(
            schema_code, curate_code, query_code,
            failure_report, skills,
            iteration=iteration
        )

    # return best achieved even if threshold not met; surface score to operator
    return best_context
```

### Grader

```python
def grade(results, eval_set) -> tuple[float, FailureReport]:
    scores = []
    failures = []
    for result, item in zip(results, eval_set):
        exact = exact_match(result.data, item.answer)
        if not exact:
            llm_score = llm_judge(result.data, item.answer, item.question)
        else:
            llm_score = 1.0
        score = max(exact, llm_score)
        scores.append(score)
        if score < 0.7:
            failures.append(FailureCase(
                question_id=item.id,
                difficulty=item.difficulty,
                expected=item.answer,
                got=result.data,
                artifacts_used=result.artifacts_used,
                failure_class=classify_failure(result, item)
            ))
    return mean(scores), FailureReport(failures=failures, by_difficulty=group_by_difficulty(failures))
```

### Failure Classification

Track failure modes structurally so the refine step sees patterns:

| Failure Class | Description | Compiler Action |
|---|---|---|
| `missing_artifact` | Required entity/field not in any artifact | Broaden extraction scope |
| `wrong_granularity` | Artifact too coarse or too fine for the question | Adjust artifact shape |
| `citation_gap` | Value present but citation failed verification | Tighten extraction prompt |
| `multi_entity_miss` | Single-entity artifacts, multi-entity question | Add cross-artifact composition |
| `section_miss` | Section path didn't locate the right elements | Revise `find_section` logic |
| `unit_error` | Currency / date normalization wrong | Update skill call |

### Refine Prompt Pattern

```python
REFINE_PROMPT = """
You are improving artifact extraction code for the domain: {domain_description}

Current schema:
{schema_code}

Current curate() function:
{curate_code}

Current query() function:
{query_code}

Failure report from this iteration (iteration {iteration}):
{failure_report_json}

Available skills:
{skill_signatures}

Instructions:
- Analyze the failure patterns, especially by difficulty tier
- Propose minimal diffs to schema_code, curate_code, and/or query_code
- Prefer composing existing skills over writing new logic
- Do not change the Citation or field_citations contract
- Return three code blocks: schema, curate, query
"""
```

### Definition of Done

- [ ] Compiler converges to ≥0.85 overall score on the eval set for the first target domain
- [ ] Per-difficulty scores are tracked; `single_fact` ≥0.90, `multi_company` ≥0.75
- [ ] Compiler runs end-to-end without human intervention after providing eval set + corpus
- [ ] Compiled context for domain 2 reuses ≥3 skills from the library (validating generalization)
- [ ] Convergence achieved in ≤15 iterations on average across domains

### Test Contract

```python
def test_compiler_convergence(domain_config):
    context = compile_context(**domain_config)
    assert context is not None
    score, _ = grade_context(context, domain_config["eval_set"])
    assert score >= 0.80, f"Compiler did not converge: score={score}"

def test_skill_reuse(domain_1_context, domain_2_context):
    d1_skills = extract_skill_calls(domain_1_context.curate_code)
    d2_skills = extract_skill_calls(domain_2_context.curate_code)
    shared = d1_skills & d2_skills
    assert len(shared) >= 3, "Insufficient skill reuse between domains"
```

---

## Incremental Build Order

Build in this sequence. Do not start a layer until the previous layer's handoff criteria are met.

```
Phase 1 (1–2 days)
  Layer 1: Ingestion pipeline for one doc type
  ↓ Handoff: 50 docs parsed, section paths validated

Phase 2 (3–5 days)
  Layer 2: Hand-written artifact schema for Domain 1
           Extraction prompt + citation verifier
           Postgres schema deployed
  ↓ Handoff: Citations ≥95%, idempotency verified

Phase 3 (2–3 days)
  Layer 3: Planner + fetcher + composer
           Query interface end-to-end on Domain 1
  ↓ Handoff: 10 test queries pass, ACL tested

Phase 4 (5–7 days)
  Layer 2 (again): Hand-write Domain 2 artifact schema
  → Extract reusable pieces into skill library
  ↓ Skill library has ≥5 production-validated skills

Phase 5 (1–2 weeks)
  Layer 4: Compiler loop bootstrapping + refine
           Eval sets authored for Domain 1 and Domain 2
           Validate compiler reproduces hand-written schemas
```

The reason for Phase 4 before Phase 5: the compiler needs to know what good artifacts look like before it can produce them autonomously. Two hand-written domains gives you the ground truth the compiler loop optimizes toward.

---

## Observability Requirements

Every query execution must emit structured traces. These are not optional — they're how you debug the compiler loop and audit agent behavior in production.

```python
@dataclass
class QueryTrace:
    query_id: str
    principal_id: str
    ask: str
    plan: RetrievalPlan
    artifacts_fetched: list[str]
    composer_input_tokens: int
    composer_output_tokens: int
    citations: list[Citation]
    score: float | None          # populated by grader in eval runs
    latency_ms: int
    timestamp: datetime
```

Track these metrics per domain context:

- **Completion rate** — queries answered within budget vs. timed out
- **Citation coverage** — % of response fields with verified citations
- **Planner hit rate** — % of planned artifact types that returned results
- **Token cost per query** — composer tokens only (compilation cost is amortized)
- **Accuracy on eval set** — run eval set on a schedule as corpus evolves

---

## Database Decision Checklist

Reassess the storage layer when any of these thresholds are hit:

| Metric | Postgres + pgvector | Trigger to reassess |
|---|---|---|
| Artifact count | < 5M | ≥ 5M → evaluate Qdrant/Weaviate alongside |
| Vector recall@10 | > 0.90 | < 0.90 after HNSW tuning → dedicated vector DB |
| Graph query latency | < 200ms | > 200ms warm → evaluate Kuzu (embedded) |
| Permission model | RLS sufficient | ABAC / dynamic groups needed → add Oso/Cerbos |
| Team familiarity | Postgres already operated | New DB = new on-call burden |

Do not add a second database until one of these triggers fires and you can name the query it unblocks.

---

## Glossary

| Term | Definition |
|---|---|
| **Artifact** | A typed, cited fact record about one entity, compiled from source documents at ingest time |
| **Context** | A curated collection of artifacts for a specific domain or workflow |
| **Context Compiler** | The eval-driven LLM harness that produces artifact schemas and extraction code for a domain |
| **Skill** | A reusable, tested extraction primitive the compiler can compose (e.g. `extract_table`, `normalize_currency`) |
| **KnowQL-style query** | A declarative query specifying `ask`, `shape`, `filters`, and `budget`; the engine plans execution |
| **Citation** | A `(element_id, char_start, char_end, verbatim_span)` tuple grounding an artifact field in source text |
| **Promotion** | Moving a JSONB field to a real Postgres column because it is used in filters or indexes |
| **Eval set** | A domain-specific set of `(question, ground_truth)` pairs used to score and drive the compiler loop |
