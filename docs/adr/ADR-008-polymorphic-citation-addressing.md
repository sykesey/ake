# ADR-008 — Polymorphic Citation Addressing for Non-Document Sources

**Status:** Accepted  
**Date:** 2026-05-13

## Context

The existing `Citation` model (ADR-002) uses `(element_id, char_start, char_end, verbatim_span)`. This addressing scheme assumes source text with character offsets — it is correct for prose documents but cannot express provenance for:

- **Tabular data** — the meaningful reference is `(dataset, table, row_id, column_name)`, not a character range within a concatenated string
- **Knowledge graph data** — the meaningful reference is `(graph_id, node_id, property_name)` for node properties, or `(graph_id, edge_id)` for relationships

Storing these as fake `char_start`/`char_end` values in a text rendering of the row/node would technically pass the citation verifier but would be semantically misleading and brittle — the char offsets would change if the text rendering format changed, even though the underlying data did not.

## Decision

Extend `Citation` with a `source_type` discriminator and a `source_ref` union that is interpreted according to `source_type`. The existing document citation fields become one variant of the union.

```python
from typing import Literal, Union
from pydantic import BaseModel

class DocumentRef(BaseModel):
    source_type: Literal["document"] = "document"
    element_id: str
    char_start: int
    char_end: int
    verbatim_span: str

class TabularRef(BaseModel):
    source_type: Literal["tabular"] = "tabular"
    element_id: str          # element_id of the row Element
    dataset: str
    table: str
    row_id: str              # stable row identifier (primary key or content hash)
    column_name: str
    verbatim_value: str      # the raw cell value as a string

class GraphRef(BaseModel):
    source_type: Literal["graph"] = "graph"
    element_id: str          # element_id of the node or edge Element
    graph_id: str
    node_id: str | None      # set for node property citations
    edge_id: str | None      # set for edge citations
    property_name: str | None

Citation = Union[DocumentRef, TabularRef, GraphRef]
```

The citation verifier is extended with a handler per `source_type`:
- `document` — existing verbatim span check (unchanged)
- `tabular` — verify `row_id` exists in the element store and `verbatim_value` matches the cell at `column_name`
- `graph` — verify `node_id` or `edge_id` exists in the element store and `property_name` is present in the node/edge element

The `field_citations` dict on `DomainArtifact` continues to map `field_name → Citation`; no artifact schema changes are needed beyond the citation type widening.

Storage: `field_citations` is JSONB; the `source_type` discriminator is the first key in each citation object, enabling cheap filtering in the artifact store if needed (`payload->'source_type'`).

## Consequences

**Positive**
- Provenance for tabular and graph sources is semantically correct and survives format changes (a change to how row text is rendered does not invalidate citations)
- The existing citation verifier is extended, not replaced; document source behaviour is unchanged
- Callers that display citations to end users can render the appropriate UI (e.g. "row 42, column `revenue`" vs "paragraph 3, page 7") without special-casing in the query layer

**Negative**
- The `Citation` type is now a union; any code that pattern-matches on citation fields must handle all three variants — this is a breaking change for callers that assumed `char_start` / `char_end` always exist
- The citation verifier becomes more complex: three handlers instead of one
- New source types in the future (audio timestamps, API responses) require another variant and another verifier handler

**Mitigations**
- A compatibility shim on `DocumentRef` exposes `char_start` / `char_end` at the top level; code that reads these fields without checking `source_type` continues to work for document citations
- The three verifier handlers are tested independently; a failure in the tabular handler does not affect document citation verification
- The `source_type` discriminator makes future variant additions additive (no modification to existing variants)
