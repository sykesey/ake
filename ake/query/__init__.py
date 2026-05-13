"""Declarative Query Interface (F004).

The query layer provides a single typed ``execute(query, principal)`` function
that agents call to retrieve knowledge. Agents declare *what* they want and
receive a typed, cited response — no agent-side loops over raw data.
"""

from ake.query.interface import (
    Citation as QueryCitation,
    Query,
    QueryBudget,
    QueryResult,
    RetrievalPlan,
)
from ake.query.execute import execute

__all__ = [
    "execute",
    "Query",
    "QueryBudget",
    "QueryResult",
    "QueryCitation",
    "RetrievalPlan",
]