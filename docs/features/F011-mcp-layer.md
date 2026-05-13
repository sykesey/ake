# F011 — MCP Server Layer: Polymorphic Resources, Tools & Data Structures

**Status:** Defined  
**Layer:** Cross-cutting (sits above Layer 3 — Query)

## Statement

AKE exposes its entire capability surface as a standards-compliant MCP server using a polymorphic URI scheme and a self-describing schema registry — so that any MCP-capable agent can discover, query, and ingest knowledge without knowing in advance whether the underlying artifacts came from documents, tables, or knowledge graphs, or what domain schemas they carry.

## The Amorphous Design Goal

The MCP layer must be *source-agnostic* and *schema-agnostic*. A new domain, a new source type, or a new artifact shape should register once in `mcp/registry.py` and become immediately discoverable and queryable through the same MCP surface — with no changes to the MCP server code itself.

---

## MCP Resources

Resources expose read access to AKE's artifact store and schema registry. All URIs follow a consistent polymorphic scheme regardless of source type.

### URI Scheme

```
ake://domains                               # list all registered domains
ake://domains/{domain_name}                 # domain description + artifact types + eval status
ake://schema/{artifact_type}                # JSON Schema for a specific artifact type
ake://artifacts/{artifact_type}/{entity_id} # most recent compiled artifact for an entity
ake://artifacts/{artifact_type}/{entity_id}/{fiscal_year}  # version-specific artifact
ake://elements/{doc_id}/{element_id}        # raw source element (document, row, or node)
ake://citations/{artifact_id}              # all citations for an artifact
```

### Resource Contracts

- All resource responses are JSON and include a `_source_type` field (`"document"`, `"tabular"`, or `"graph"`) and a `_schema_version` field — callers can adapt rendering without hardcoded logic
- `ake://schema/{artifact_type}` returns the full JSON Schema for that type's `payload`, including field descriptions and whether each field may be null — this is the primary discovery mechanism for agents building queries
- `ake://domains` is always available and reflects the live registry; a new domain appears here as soon as its config is registered, before any artifacts are compiled

---

## MCP Tools

Tools map to the declarative query interface and the ingestion pipeline.

### Query Tools

```
ake_query(ask, shape, filters?, contexts?, ground?, budget?)
  → QueryResult (data, citations, artifacts_used, latency_ms, token_cost)
```
Direct mapping to `execute(Query, principal)`. `shape` is a JSON Schema dict; the composer conforms its output to it. `contexts` is a list of `artifact_type` strings — if omitted, the planner selects based on `ask` and `shape`.

```
ake_list_artifact_types(domain?)
  → [{artifact_type, description, fields, source_types, domain}]
```
Returns all registered artifact types, optionally filtered to a domain. The primary tool for agents that need to discover what knowledge is available before constructing a query.

```
ake_describe_schema(artifact_type)
  → {artifact_type, json_schema, nullable_fields, promoted_filters, source_types, example}
```
Returns the full schema for a type plus an annotated example artifact. Agents use this to construct `query.shape` conformant JSON.

```
ake_get_artifact(artifact_type, entity_id, fiscal_year?)
  → Artifact | null
```
Direct artifact retrieval by identity, bypassing the planner. For agents that already know the exact artifact they need.

### Ingestion Tools

```
ake_ingest_document(source_url, source_type, acl_principals?, domain?)
  → {job_id, status, doc_id?}
```
Triggers ingestion and compilation for a single source. `source_type` is `"document"`, `"tabular"`, or `"graph"`. Returns a job ID for polling; compilation is asynchronous.

```
ake_ingest_status(job_id)
  → {status, doc_id?, artifact_count?, citation_coverage?, errors?}
```
Polls an ingestion job. `status` is `"pending"`, `"parsing"`, `"compiling"`, `"complete"`, or `"failed"`.

### Discovery & Introspection Tools

```
ake_list_entities(artifact_type, filters?)
  → [{entity_id, entity_name, artifact_count, latest_compiled_at}]
```
Lists all entities with compiled artifacts of a given type. Useful for agents building enumeration queries.

```
ake_get_trace(query_id)
  → QueryTrace
```
Returns the full structured trace for a past query (planner output, artifacts fetched, token cost, citations). Supports agent-side debugging and audit.

---

## Polymorphic Data Envelope

Every tool response and resource payload is wrapped in a standard envelope so agent code that handles one source type handles all:

```json
{
  "_ake_version": "1.0",
  "_artifact_type": "financials_10k",
  "_source_type": "document",
  "_entity_id": "NVDA",
  "_compiled_at": "2026-05-13T12:00:00Z",
  "_citation_coverage": 0.97,
  "data": { ...domain fields... },
  "citations": [ ...Citation objects... ]
}
```

The `_source_type` field uses the same discriminator as the `Citation` union (ADR-008). Agents that need source-specific behaviour branch on `_source_type`; agents that only care about `data` fields ignore it entirely.

---

## Schema Registry

`mcp/registry.py` is the single source of truth for all registered artifact types. Each entry provides:

```python
@dataclass
class ArtifactTypeRegistration:
    artifact_type: str
    domain: str
    description: str
    json_schema: dict               # Pydantic model → JSON Schema
    source_types: list[str]         # which source types can produce this type
    promoted_filters: list[str]     # columns safe to filter on
    nullable_fields: list[str]      # fields that may be null
    example: dict                   # representative artifact payload
```

The MCP server iterates the registry at startup to register all resources and populate `ake_list_artifact_types`. Adding a new domain requires a registry entry — no MCP server code changes.

---

## Acceptance Criteria

- All MCP resources and tools are discoverable via standard MCP `list_resources` and `list_tools` calls
- `ake_query` returns a response conforming to `query.shape` for 10 hand-crafted queries across at least 2 domains
- `ake://schema/{artifact_type}` returns a valid JSON Schema for every registered type
- A new artifact type registered in `mcp/registry.py` becomes queryable via `ake_query` and discoverable via `ake_list_artifact_types` without restarting or modifying the MCP server code
- `_source_type` in the envelope is correct for artifacts compiled from document, tabular, and graph sources
- ACL enforcement from the query layer (F005) carries through — a caller without access to an artifact does not receive it in MCP tool responses
- Ingestion tools return a job ID synchronously; `ake_ingest_status` reflects accurate state transitions

## Out of Scope

- MCP sampling or model calls initiated by the AKE server (AKE is a resource/tool server only)
- Multi-server MCP federation or proxy routing
- Real-time push notifications on ingestion completion (polling via `ake_ingest_status` is sufficient for this phase)
