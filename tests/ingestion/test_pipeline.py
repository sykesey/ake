"""Tests for the IngestionPipeline.

Parser integration tests are marked with pytest.importorskip so they are
skipped when the 'ingestion' dependency group is not installed.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ake.ingestion.element import VALID_ELEMENT_TYPES, compute_doc_id
from ake.ingestion.pipeline import IngestionPipeline

FIXTURES = Path(__file__).parent.parent / "fixtures"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _fake_raw_elements(n: int = 3):
    """Return simple mock objects that satisfy the normalizer's interface."""

    class _Meta:
        page_number = 1
        category_depth = None

    elements = []
    for i in range(n):
        m = MagicMock()
        m.__class__.__name__ = "NarrativeText"
        m.text = f"Paragraph {i}."
        m.metadata = _Meta()
        elements.append(m)
    return elements


# ── Pipeline without a store (offline / unit mode) ───────────────────────────


@pytest.mark.asyncio
async def test_pipeline_no_store_returns_result(tmp_path):
    html = FIXTURES / "sample.html"
    pytest.importorskip("unstructured")

    pipeline = IngestionPipeline(store=None)
    result = await pipeline.ingest_file(html, metadata={"source_url": "https://example.com"})

    assert result.doc_id
    assert len(result.elements) > 0
    assert result.source_url == "https://example.com"
    for el in result.elements:
        assert el.type in VALID_ELEMENT_TYPES
        assert el.metadata.get("source_url") == "https://example.com"


@pytest.mark.asyncio
async def test_pipeline_idempotent_doc_id(tmp_path):
    html = FIXTURES / "sample.html"
    pytest.importorskip("unstructured")

    pipeline = IngestionPipeline(store=None)
    result_a = await pipeline.ingest_file(html)
    result_b = await pipeline.ingest_file(html)

    assert result_a.doc_id == result_b.doc_id
    assert len(result_a.elements) == len(result_b.elements)


@pytest.mark.asyncio
async def test_pipeline_ingest_bytes():
    pytest.importorskip("unstructured")

    html_bytes = (FIXTURES / "sample.html").read_bytes()
    pipeline = IngestionPipeline(store=None)
    result = await pipeline.ingest_bytes(
        content=html_bytes,
        content_type="text/html",
        metadata={"source_url": "https://example.com/report.html"},
    )

    assert result.doc_id == compute_doc_id(html_bytes)
    assert len(result.elements) > 0


# ── Idempotency with a mock store ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_skips_parse_when_already_stored(tmp_path):
    """When the store reports doc_id exists, we must not re-parse."""
    html = FIXTURES / "sample.html"
    content = html.read_bytes()
    doc_id = compute_doc_id(content)

    stored_elements = _fake_raw_elements(2)  # anything non-empty

    mock_store = AsyncMock()
    mock_store.exists = AsyncMock(return_value=True)
    mock_store.get_by_doc_id = AsyncMock(return_value=stored_elements)

    pipeline = IngestionPipeline(store=mock_store)

    with patch.object(pipeline, "_store", mock_store):
        # Need to also patch the parser so we can verify it's NOT called
        with patch("ake.ingestion.pipeline._parser_for_path") as mock_parser_fn:
            result = await pipeline.ingest_file(html)

    mock_store.exists.assert_awaited_once_with(doc_id)
    mock_parser_fn.assert_not_called()
    assert result.doc_id == doc_id


@pytest.mark.asyncio
async def test_pipeline_saves_to_store_on_first_ingest():
    pytest.importorskip("unstructured")
    html = FIXTURES / "sample.html"

    mock_store = AsyncMock()
    mock_store.exists = AsyncMock(return_value=False)
    mock_store.save = AsyncMock()

    pipeline = IngestionPipeline(store=mock_store)
    result = await pipeline.ingest_file(html)

    mock_store.save.assert_awaited_once()
    saved_elements = mock_store.save.call_args[0][0]
    assert len(saved_elements) == len(result.elements)


# ── ACL propagation ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_acl_principals_in_metadata():
    pytest.importorskip("unstructured")
    html = FIXTURES / "sample.html"

    pipeline = IngestionPipeline(store=None)
    result = await pipeline.ingest_file(
        html,
        metadata={"acl_principals": ["group:finance", "user:alice"]},
    )

    for el in result.elements:
        assert el.metadata["acl_principals"] == ["group:finance", "user:alice"]


# ── Unsupported format ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unsupported_extension_raises(tmp_path):
    txt_file = tmp_path / "doc.txt"
    txt_file.write_bytes(b"plain text")

    pipeline = IngestionPipeline(store=None)
    with pytest.raises(ValueError, match="No parser registered"):
        await pipeline.ingest_file(txt_file)


# ── Section path spot-check (HTML) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_html_section_paths_populated():
    pytest.importorskip("unstructured")
    html = FIXTURES / "sample.html"

    pipeline = IngestionPipeline(store=None)
    result = await pipeline.ingest_file(html, metadata={"source_url": str(html)})

    # At least some elements should have a non-empty section_path
    paths = [e.section_path for e in result.elements if e.section_path]
    assert len(paths) > 0, "No elements have a section_path — heading extraction failed"

    # The fixture has h1/h2/h3 hierarchy; check a nested path exists
    nested = [p for p in paths if len(p) >= 2]
    assert len(nested) > 0, (
        "No nested section paths found — h2/h3 hierarchy not extracted. "
        f"Paths seen: {paths[:10]}"
    )
