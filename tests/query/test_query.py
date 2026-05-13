"""Tests for F004 — Declarative Query Interface."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from ake.query.interface import Citation, Query, QueryBudget, QueryResult, RetrievalPlan
from ake.query.planner import plan


class TestQueryBudget:
    def test_default_max_artifacts(self):
        b = QueryBudget()
        assert b.max_artifacts == 20

    def test_default_timeout(self):
        b = QueryBudget()
        assert b.timeout_seconds == 10.0

    def test_custom_values(self):
        b = QueryBudget(max_artifacts=5, timeout_seconds=3.0)
        assert b.max_artifacts == 5
        assert b.timeout_seconds == 3.0


class TestQueryConstruction:
    def test_minimal_query(self):
        q = Query(ask="test", shape={"value": "int"})
        assert q.ask == "test"
        assert q.shape == {"value": "int"}
        assert q.filters == {}
        assert q.contexts == []
        assert q.ground is True
        assert isinstance(q.budget, QueryBudget)

    def test_with_filters_and_contexts(self):
        q = Query(
            ask="revenue for NVDA",
            shape={"revenue": "float"},
            filters={"entity_id": "NVDA", "fiscal_year": 2022},
            contexts=["financials_10k"],
            ground=False,
        )
        assert q.filters["entity_id"] == "NVDA"
        assert q.contexts == ["financials_10k"]
        assert q.ground is False


class TestRetrievalPlan:
    def test_minimal_plan(self):
        rp = RetrievalPlan(artifact_types=["financials_10k"])
        assert rp.artifact_types == ["financials_10k"]
        assert rp.structured_filters == {}
        assert rp.semantic_query is None
        assert rp.max_results == 20

    def test_with_filters_and_semantic(self):
        rp = RetrievalPlan(
            artifact_types=["financials_10k"],
            structured_filters={"entity_id": "NVDA", "fiscal_year": 2022},
            semantic_query="What was revenue?",
            max_results=10,
        )
        assert rp.structured_filters["entity_id"] == "NVDA"
        assert rp.semantic_query == "What was revenue?"
        assert rp.max_results == 10


class TestCitation:
    def test_citation_fields(self):
        c = Citation(
            field="revenue",
            element_id="doc1_elem3",
            verbatim_span="$12.5B",
            doc_id="abc123",
        )
        assert c.field == "revenue"
        assert c.confidence == 1.0


class TestQueryResult:
    def test_result_construction(self):
        result = QueryResult(
            data={"revenue": 12500},
            citations=[
                Citation(
                    field="revenue",
                    element_id="e1",
                    verbatim_span="$12.5B",
                    doc_id="d1",
                )
            ],
            artifacts_used=["art1"],
            latency_ms=300,
            token_cost=1500,
        )
        assert result.data["revenue"] == 12500
        assert len(result.citations) == 1


class TestPlanner:
    def test_uses_explicit_contexts(self):
        """When query.contexts is non-empty, use them directly."""
        q = Query(
            ask="revenue",
            shape={"value": "float"},
            contexts=["financials_10k"],
        )
        rp = plan(q)
        assert rp.artifact_types == ["financials_10k"]

    def test_infers_from_ask_keywords(self):
        """Keyword 'revenue' → financials_10k."""
        q = Query(ask="What was the total revenue?", shape={"value": "float"})
        rp = plan(q)
        assert "financials_10k" in rp.artifact_types

    def test_infers_from_shape_keys(self):
        """Shape key 'compensation' → executive_comp."""
        q = Query(ask="Tell me about the CEO", shape={"compensation": "float"})
        rp = plan(q)
        assert "executive_comp" in rp.artifact_types

    def test_infers_contract_type(self):
        q = Query(ask="What are the contract terms?", shape={"obligation": "str"})
        rp = plan(q)
        assert "contract_terms" in rp.artifact_types

    def test_no_match_returns_empty(self):
        q = Query(ask="xyzzy foobar blarg", shape={"nothing": "str"})
        rp = plan(q)
        assert rp.artifact_types == []

    def test_promotes_structured_filters(self):
        q = Query(
            ask="revenue",
            shape={"value": "float"},
            filters={"entity_id": "AAPL", "fiscal_year": 2023},
        )
        rp = plan(q)
        assert rp.structured_filters["entity_id"] == "AAPL"
        assert rp.structured_filters["fiscal_year"] == 2023

    def test_semantic_when_no_structured_filters(self):
        q = Query(ask="What is the total revenue?", shape={"value": "float"})
        rp = plan(q)
        assert rp.semantic_query == "What is the total revenue?"

    def test_no_semantic_when_structured_filters_present(self):
        q = Query(
            ask="revenue?",
            shape={"value": "float"},
            filters={"entity_id": "NVDA"},
        )
        rp = plan(q)
        assert rp.semantic_query is None


class TestPlannerIntegration:
    """Planner must produce valid output even when shape contains complex nested dicts."""

    def test_plan_always_returns_retrieval_plan(self):
        q = Query(
            ask="Complex nested query",
            shape={
                "company": {
                    "revenue": "float",
                    "profit": "float",
                }
            },
            filters={"entity_id": "MSFT", "fiscal_year": 2022},
        )
        rp = plan(q)
        assert isinstance(rp, RetrievalPlan)
        assert rp.max_results == 20