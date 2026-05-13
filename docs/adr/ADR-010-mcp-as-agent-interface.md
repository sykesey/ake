# ADR-010 — MCP as the Primary Agent-Facing Interface

**Status:** Accepted  
**Date:** 2026-05-13

## Context

The declarative query interface (`execute(Query, principal) → QueryResult`) defined in Layer 3 is a Python function. Agents that call AKE directly as a Python library share a process and import path, which couples them tightly to AKE's internal dependencies and deployment cycle. As AKE supports more source types and domains, the number of callers grows; a protocol boundary is needed so that:

1. AKE can evolve its internals without requiring all callers to update
2. Agents written in any language or framework can retrieve knowledge without embedding Python
3. Schema and capability discovery is self-describing — agents do not need out-of-band documentation to know what artifact types exist or what their fields are
4. The interface is amorphous-by-default: the same tool call and resource URI pattern work whether the underlying artifact came from a PDF, a Parquet file, or a knowledge graph

The Model Context Protocol (MCP) is an emerging standard for exactly this interface: a structured, self-describing protocol for exposing tools and resources to AI agents. It is language-agnostic, schema-aware (tools carry JSON Schema input/output specs), and already adopted by Claude, Gemini, and major agent frameworks.

## Decision

Implement AKE as an MCP server (`mcp/`) that exposes the full capability surface — query, artifact retrieval, schema discovery, and ingestion triggering — as MCP tools and resources. The Python `execute()` function remains the internal query implementation; the MCP layer is a thin translation shim over it.

The MCP layer uses a **schema registry** (`mcp/registry.py`) as its single source of truth. Each registered artifact type produces MCP resource URIs and is included in tool responses automatically. Adding a new domain requires a registry entry, not MCP server code changes.

The polymorphic URI scheme (`ake://artifacts/{type}/{entity_id}`) is intentionally source-type-agnostic. Callers address artifacts by domain type and entity identity, not by provenance. The `_source_type` field in the response envelope allows callers to adapt rendering if needed, but does not affect the addressing or query model.

The Python `execute()` API is retained for internal use (compiler loop, eval harness, integration tests) but is not the supported interface for external agent callers. External callers must use MCP.

## Consequences

**Positive**
- Any MCP-compatible agent framework can query AKE without a Python dependency — the interface is protocol-level
- Schema discovery (`ake://schema/{type}`, `ake_describe_schema`, `ake_list_artifact_types`) is self-describing; agents can introspect available knowledge before constructing queries
- New domains are immediately queryable by all callers once registered — no per-caller update cycle
- The polymorphic URI and envelope design means a caller that handles one source type handles all; it does not need to know whether an artifact came from a document or a Parquet file
- MCP's standardised tool-call model simplifies authentication and audit logging — all access flows through one boundary

**Negative**
- An MCP server is a network process; adding a network hop increases latency versus a direct function call. For the compiler loop (which calls `execute()` in a tight eval loop), the overhead is unacceptable — the Python API is retained for this reason
- MCP is a relatively young protocol; the specification may evolve in ways that require server updates
- The schema registry (`mcp/registry.py`) is a new artefact that must stay synchronised with domain artifact schemas; a schema change that is not reflected in the registry will cause tool responses to be inconsistent with `ake://schema/{type}`

**Mitigations**
- The compiler loop and eval harness call `execute()` directly, bypassing MCP entirely; MCP latency is not on the critical path for iteration speed
- Registry entries are generated from Pydantic models via `model_json_schema()`; the Pydantic model is the single source of truth, and the registry entry is derived — drift is detected by a test that compares the live registry schema against the Pydantic schema at CI time
- MCP version pinning and a compatibility shim layer buffer the server from spec changes until a deliberate upgrade cycle

## Alternatives Considered

**REST API** — simpler to implement, widely understood, but not self-describing at the tool/resource level and does not integrate with agent frameworks that expect MCP. Rejected in favour of MCP's native agent integration.

**gRPC** — strongly typed and efficient, but requires schema compilation and is not natively supported by agent frameworks. Rejected for the same integration reason.

**Keep Python API only** — works for tightly coupled callers but prevents language-agnostic agent access and forces all callers into AKE's deployment cycle. Rejected.
