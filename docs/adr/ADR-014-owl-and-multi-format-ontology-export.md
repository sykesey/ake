# ADR-014 — OWL 2 / RDF as Canonical Ontology Representation; Multi-Format Export

**Status:** Accepted  
**Date:** 2026-05-14

## Context

The amorphous pipeline (F012) produces a derived ontology: entity classes, typed data properties, and object properties representing FK relationships. This ontology must be persisted in a form that:

- Is consumable by external tools (ontology editors, SPARQL endpoints, reasoning engines)
- Is human-readable and version-controllable
- Can be loaded by downstream AKE pipelines without re-running ingestion
- Can be visualised without additional processing

A single format cannot satisfy all of these requirements equally. The ontology representation decision and the persistence model decision are therefore coupled.

### Representation options considered

1. **Custom YAML schema only** — A project-specific YAML format describing tables, columns, and relationships. Human-readable and easy to write, but not interoperable: no external tool can reason over it, and any consumer must implement its own parser.

2. **JSON Schema per table** — Widely supported for validation; describes data shapes well. Has no standard mechanism for expressing object properties (inter-table relationships), class hierarchies, or semantic annotations like confidence scores. Cannot express an OWL ontology.

3. **JSON-LD** — A W3C standard that is technically sufficient, but its context file management is complex to get right, and the resulting documents are verbose and difficult to read or edit manually.

4. **SKOS** — Designed for thesauri and controlled vocabularies. Poor fit for a relational data model: SKOS has no concept of data properties, domain/range constraints, or cardinality.

5. **OWL 2 / RDF (Turtle serialisation)** — W3C standard for ontology representation. Supports `owl:Class`, `owl:DatatypeProperty`, `owl:ObjectProperty`, cardinality constraints, annotation properties, and SPARQL querying. Can be loaded by Protégé, OWL API, rdflib, and any SPARQL endpoint. `rdflib` is already in the `ingestion` dependency group — no new dependency.

### Persistence options considered

1. **Store in Postgres as a new artifact type** — Consistent with the rest of AKE's persistence model; queryable via the declarative query interface. Requires a live database connection; cannot be used in the database-free mode that the amorphous pipeline explicitly supports (the pipeline runs with `IngestionPipeline(store=None)`).

2. **Single file, one canonical format** — Simple, but forces every consumer (human reviewer, visualization tool, downstream pipeline) to parse the same format regardless of their needs.

3. **Multiple files, format-per-consumer** — Each format is optimised for a different consumer. More files to manage, but no consumer bears the cost of parsing an unsuitable format.

## Decision

### Canonical ontology representation: OWL 2 / RDF Turtle

OWL 2 via `rdflib` is the canonical semantic representation. The choice is driven by:

- **Standards compliance**: downstream consumers (Protégé, OWL API, SPARQL endpoints, other AKE pipeline instances) can consume the output without a project-specific parser.
- **Annotation properties**: OWL supports arbitrary annotation properties on any entity. The `ake:inferenceConfidence` and `ake:semanticRole` annotations required by F012 fit naturally as annotation properties; no schema extension is needed.
- **No new dependency**: `rdflib>=7` is already in the `ingestion` group.
- **Round-trip fidelity**: the Turtle format is human-readable, diff-friendly in version control, and re-parsable without loss.

Custom `ake:` annotations (confidence, semantic role, source column, row count) are defined in a stable vocabulary namespace (`http://ake.local/vocab#`) rather than hacking existing OWL/RDFS properties.

### Persistence model: file-based multi-format export

Rather than storing the ontology in Postgres, the pipeline writes five files per run:

| File | Consumer |
|---|---|
| `ontology.owl` (OWL Turtle) | External ontology tools, SPARQL endpoints, AKE pipeline re-ingestion |
| `ontology.yaml` | Human review, version control diff, operator configuration |
| `graph.json` (Cytoscape.js format) | Graph visualization without additional transformation |
| `element_tree.json` | Hierarchical row-level inspection; agent browsing |
| `elements.jsonl` | Pipeline re-import; flat streaming by any JSONL consumer |

This is not a departure from AKE's primary persistence model (Postgres + pgvector for compiled artifacts). It is a complementary export path for a specific use case — amorphous onboarding — where the database is intentionally not required.

The five-file set is the atomic output of a single `ingest_directory()` run. Together they form a **dataset snapshot**: everything needed to reconstruct the ontology, graph, and element corpus without re-running ingestion. Snapshots are suited to versioning in a git repository alongside the source data.

### What is stored in Postgres

Nothing from the amorphous pipeline is written to Postgres by default. If the caller constructs `IngestionPipeline(store=element_store)` and passes it explicitly, the raw elements are stored (F009 behaviour). The derived ontology, graph, and relationships are file-only outputs.

A future integration (not in scope for F012) could register the derived ontology as a `DomainSchema` in `mcp/registry.py`, making it queryable through the MCP tool surface (F011). The file exports are designed to be that integration's input.

## Consequences

**Positive**
- The OWL output is immediately usable by any W3C-compliant ontology tool with no project-specific adapter.
- The YAML output gives operators a human-readable schema they can edit, commit, and diff — functioning as a lightweight "data contract" for a dataset.
- Multi-format export avoids forcing a single format on consumers with incompatible needs (a graph visualization tool and a SPARQL endpoint should not both have to parse each other's format).
- No Postgres connection is required; the pipeline works in database-free mode for offline data exploration.

**Negative**
- Five output files per run increases the surface area of the output; operators must manage a directory of artefacts rather than a single record.
- The OWL file is not automatically registered in the MCP layer; an operator who wants the derived ontology to be queryable via `ake_query` must perform a separate registration step.
- File-based snapshots do not have the transactional guarantees of Postgres; a partial write (power failure mid-run) can leave an inconsistent snapshot.

**Mitigations**
- The runner writes to a caller-specified `--output` directory; the caller controls whether this is a versioned git path, a cloud storage bucket, or a temporary directory.
- Files are written in full before the next file begins (no interleaved writes); a partial failure leaves all previously completed files intact and the failed file absent, making the inconsistency detectable.
- A future `ake_register_ontology(owl_path)` MCP tool can read the `ontology.owl` file and register its classes as domain schemas, bridging the file-based and database-backed paths without modifying the core pipeline.
