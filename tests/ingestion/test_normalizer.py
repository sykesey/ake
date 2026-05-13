"""Unit tests for the normalizer — no unstructured dependency required.

Mock elements replicate the attribute interface of unstructured objects:
  .text, .metadata.page_number, .metadata.category_depth
The class name determines the element type mapping.
"""
from __future__ import annotations

from ake.ingestion.element import VALID_ELEMENT_TYPES, compute_doc_id
from ake.ingestion.normalizer import normalize

# ── Mock element helpers ──────────────────────────────────────────────────────


class _Meta:
    def __init__(self, page: int = 1, depth: int | None = None) -> None:
        self.page_number = page
        self.category_depth = depth


def _el(cls_name: str, text: str, page: int = 1, depth: int | None = None):
    """Dynamically create a mock element whose class name drives type mapping."""

    class _Elem:
        pass

    _Elem.__name__ = cls_name
    inst = _Elem()
    inst.text = text
    inst.metadata = _Meta(page=page, depth=depth)
    return inst


DOC_ID = "a" * 64
BASE_META = {"source_url": "https://example.com/doc.html", "acl_principals": ["group:finance"]}


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_element_type_mapping():
    raw = [
        _el("Title", "Section One"),
        _el("NarrativeText", "Body text here."),
        _el("Table", "Col A | Col B"),
        _el("ListItem", "• First item"),
        _el("Image", "[figure]"),
        _el("Header", "Page Header"),
    ]
    elements = normalize(raw, DOC_ID, BASE_META)
    types = [e.type for e in elements]
    assert types == ["title", "paragraph", "table", "list", "figure", "header"]


def test_all_types_are_valid():
    raw = [
        _el("Title", "T"),
        _el("NarrativeText", "P"),
        _el("Table", "T"),
        _el("ListItem", "L"),
        _el("Image", "F"),
        _el("Header", "H"),
    ]
    elements = normalize(raw, DOC_ID, BASE_META)
    for el in elements:
        assert el.type in VALID_ELEMENT_TYPES


def test_empty_text_elements_are_skipped():
    raw = [
        _el("Title", "Real Heading"),
        _el("NarrativeText", ""),
        _el("NarrativeText", "   "),
        _el("NarrativeText", "Real paragraph."),
    ]
    elements = normalize(raw, DOC_ID, BASE_META)
    assert len(elements) == 2
    assert elements[0].text == "Real Heading"
    assert elements[1].text == "Real paragraph."


def test_section_path_flat_headings():
    """All h1-equivalent headings reset section_path to a single entry."""
    raw = [
        _el("Title", "Section One", depth=0),
        _el("NarrativeText", "Para 1."),
        _el("Title", "Section Two", depth=0),
        _el("NarrativeText", "Para 2."),
    ]
    elements = normalize(raw, DOC_ID, BASE_META)
    # Heading elements get the new section_path
    assert elements[0].section_path == ["Section One"]
    assert elements[1].section_path == ["Section One"]
    assert elements[2].section_path == ["Section Two"]
    assert elements[3].section_path == ["Section Two"]


def test_section_path_nested_headings():
    """Deeper headings extend the path; shallower headings truncate it."""
    raw = [
        _el("Title", "Item 7", depth=0),
        _el("Title", "Capital Returns", depth=1),
        _el("Title", "Share Repurchases", depth=2),
        _el("NarrativeText", "12M shares bought back."),
        _el("Title", "Dividends", depth=2),
        _el("NarrativeText", "Q3 dividend increased."),
        _el("Title", "Liquidity", depth=1),
        _el("NarrativeText", "Cash flow $3.1B."),
        _el("Title", "Item 8", depth=0),
        _el("NarrativeText", "Financial statements below."),
    ]
    elements = normalize(raw, DOC_ID, BASE_META)
    paths = [e.section_path for e in elements]

    assert paths[0] == ["Item 7"]
    assert paths[1] == ["Item 7", "Capital Returns"]
    assert paths[2] == ["Item 7", "Capital Returns", "Share Repurchases"]
    assert paths[3] == ["Item 7", "Capital Returns", "Share Repurchases"]
    assert paths[4] == ["Item 7", "Capital Returns", "Dividends"]
    assert paths[5] == ["Item 7", "Capital Returns", "Dividends"]
    assert paths[6] == ["Item 7", "Liquidity"]
    assert paths[7] == ["Item 7", "Liquidity"]
    assert paths[8] == ["Item 8"]
    assert paths[9] == ["Item 8"]


def test_section_path_before_first_heading():
    """Elements before any heading have an empty section_path."""
    raw = [
        _el("NarrativeText", "Preamble text."),
        _el("Title", "First Section", depth=0),
        _el("NarrativeText", "Section content."),
    ]
    elements = normalize(raw, DOC_ID, BASE_META)
    assert elements[0].section_path == []
    assert elements[1].section_path == ["First Section"]
    assert elements[2].section_path == ["First Section"]


def test_metadata_base_merged_into_every_element():
    raw = [_el("NarrativeText", "Text.")]
    elements = normalize(raw, DOC_ID, BASE_META)
    assert elements[0].metadata["source_url"] == BASE_META["source_url"]
    assert elements[0].metadata["acl_principals"] == ["group:finance"]


def test_page_number_propagated():
    raw = [
        _el("NarrativeText", "Page one text.", page=1),
        _el("NarrativeText", "Page three text.", page=3),
    ]
    elements = normalize(raw, DOC_ID, BASE_META)
    assert elements[0].page == 1
    assert elements[1].page == 3


def test_element_ids_are_unique():
    raw = [_el("NarrativeText", f"Para {i}.") for i in range(20)]
    elements = normalize(raw, DOC_ID, BASE_META)
    ids = [e.element_id for e in elements]
    assert len(ids) == len(set(ids))


def test_doc_id_stability():
    content = b"the same document bytes"
    assert compute_doc_id(content) == compute_doc_id(content)


def test_doc_id_differs_for_different_content():
    assert compute_doc_id(b"doc A") != compute_doc_id(b"doc B")


def test_element_schema_compliance():
    raw = [
        _el("Title", "Section", page=2, depth=0),
        _el("NarrativeText", "Body paragraph.", page=2),
    ]
    elements = normalize(raw, DOC_ID, {**BASE_META})
    for el in elements:
        assert el.doc_id == DOC_ID
        assert el.element_id
        assert el.type in VALID_ELEMENT_TYPES
        assert isinstance(el.section_path, list)
        assert el.metadata.get("source_url") is not None


def test_unknown_unstructured_type_falls_back_to_paragraph():
    raw = [_el("SomeObscureType", "content")]
    elements = normalize(raw, DOC_ID, BASE_META)
    assert elements[0].type == "paragraph"
