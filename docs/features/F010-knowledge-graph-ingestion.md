# F010 — Knowledge Graph Ingestion

**Status:** Defined  
**Layer:** 1 — Ingestion & Parsing (extended)

## Statement

The system ingests knowledge-graph sources (RDF/SPARQL endpoints, labeled property graphs via Bolt/Gremlin, exported graph files) and maps nodes and edges to `Element` records — so that graph-structured knowledge participates in artifact compilation and query retrieval alongside document and tabular sources.

## Acceptance Criteria

- RDF (Turtle, N-Triples, JSON-LD) and property graph exports (GraphML, GEXF, Cypher dump) ingest without error
- Each node produces one `Element` of type `"node"`; each edge produces one `Element` of type `"edge"`
- Node elements: `text` contains `property_name: value` pairs; `metadata.node_labels` holds the node type labels; `metadata.node_id` holds the source graph's node identifier
- Edge elements: `text` contains `subject_node_id → predicate → object_node_id`; `metadata.edge_type` holds the relationship type; `metadata.source_node_id` and `metadata.target_node_id` are populated
- `section_path` reflects `[graph_name, node_label]` for nodes and `[graph_name, edge_type]` for edges
- `doc_id` is a stable hash of `(graph_source_uri, graph_version_or_snapshot_timestamp)`
- Re-ingesting an unchanged graph snapshot produces an identical element set (idempotency)
- Entity resolution links graph nodes to canonical `entity_id` values via the `resolve_entity` skill at ingest time where a registry match exists

## Key Behaviours

- **Entity identity preservation** — the source graph's node identifier is preserved in `metadata.node_id` and used as the basis for `entity_id` in compiled artifacts; this allows artifacts compiled from graph sources to be merged with artifacts from document sources for the same real-world entity
- **Relationship artifacts** — edges compile to a distinct `artifact_type` of `"relationship"` carrying `(subject_entity_id, predicate, object_entity_id, properties, citation)`, enabling the query layer to retrieve typed relationships without graph traversal at query time
- **Namespace normalisation for RDF** — predicate URIs are normalised to short-form labels (`schema:name` → `name`) during element generation; the full URI is preserved in `metadata.predicate_uri`
- **Incremental snapshot ingestion** — if the source provides a diff (added/removed nodes and edges since last snapshot), only the changed elements are re-processed; unchanged elements retain their existing `element_id` and compiled artifacts

## Differences from Document Ingestion

| Concern | Document (F001) | Knowledge Graph (F010) |
|---|---|---|
| `element.type` | `title`, `paragraph`, … | `node`, `edge` |
| `element.text` | prose / table text | property pairs or triple string |
| `section_path` | heading hierarchy | `[graph_name, label/edge_type]` |
| citation addressing | `char_start` / `char_end` | `node_id` + `property_name`, or `edge_id` (see ADR-008) |
| LLM extraction | required | minimal; used for entity resolution and summary embedding only (see ADR-009) |
| identity | `doc_id` per document | `doc_id` per graph snapshot; `entity_id` per node |

## Out of Scope

- Live graph traversal at query time — the query layer operates on pre-compiled relationship artifacts, not the graph database directly
- Graph algorithm computation (PageRank, community detection) at ingest time
- SPARQL or Cypher query passthrough — the declarative query interface (F004) is the only query surface
- Schema inference for untyped graphs — node labels and edge types must be present in the source
