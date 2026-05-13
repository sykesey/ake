"""Polymorphic citation model (ADR-008).

A Citation records where in a source a particular artifact field value came from.
The discriminator field ``source_type`` determines which addressing scheme applies:

  document — character-offset span within an element's text
  tabular  — cell reference by dataset / table / row_id / column_name
  graph    — node or edge property reference
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class DocumentRef(BaseModel):
    source_type: Literal["document"] = "document"
    element_id: str
    char_start: int
    char_end: int
    verbatim_span: str


class TabularRef(BaseModel):
    source_type: Literal["tabular"] = "tabular"
    element_id: str
    dataset: str
    table: str
    row_id: str
    column_name: str
    verbatim_value: str


class GraphRef(BaseModel):
    source_type: Literal["graph"] = "graph"
    element_id: str
    graph_id: str
    node_id: str | None = None
    edge_id: str | None = None
    property_name: str | None = None


Citation = Annotated[
    Union[DocumentRef, TabularRef, GraphRef],
    Field(discriminator="source_type"),
]
