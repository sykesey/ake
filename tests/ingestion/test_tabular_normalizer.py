"""Tests for the tabular normalizer and compute_tabular_doc_id.

All tests use in-memory pyarrow RecordBatches so no fixture files are needed.
The entire module is skipped when pyarrow is not installed.
"""
from __future__ import annotations

import datetime

import pytest

pa = pytest.importorskip("pyarrow", reason="pyarrow not installed")

from ake.ingestion.element import VALID_ELEMENT_TYPES, compute_tabular_doc_id
from ake.ingestion.tabular_normalizer import normalize_tabular

# ── Helpers ───────────────────────────────────────────────────────────────────

DOC_ID = "b" * 64
BASE_META = {"source_url": "s3://bucket/dataset/table.parquet", "acl_principals": ["group:analysts"]}


def _batch(*cols: tuple[str, list]) -> pa.RecordBatch:
    """Build a RecordBatch from (name, values) pairs, inferring Arrow types."""
    arrays = {name: pa.array(values) for name, values in cols}
    return pa.record_batch(arrays)


def _normalize(batches, **kw) -> list:
    schema = batches[0].schema if batches else pa.schema([])
    return normalize_tabular(
        batches=iter(batches),
        schema=schema,
        doc_id=DOC_ID,
        dataset_name=kw.get("dataset_name", "my_dataset"),
        table_name=kw.get("table_name", "financials"),
        metadata_base=kw.get("metadata_base", BASE_META),
        partition=kw.get("partition"),
    )


# ── compute_tabular_doc_id ────────────────────────────────────────────────────


def test_tabular_doc_id_is_stable():
    did = compute_tabular_doc_id("s3://bucket/t.parquet", "fp1", "ch1")
    assert did == compute_tabular_doc_id("s3://bucket/t.parquet", "fp1", "ch1")


def test_tabular_doc_id_changes_on_schema_change():
    base = compute_tabular_doc_id("s3://bucket/t.parquet", "fp_v1", "ch1")
    changed = compute_tabular_doc_id("s3://bucket/t.parquet", "fp_v2", "ch1")
    assert base != changed


def test_tabular_doc_id_changes_on_content_change():
    base = compute_tabular_doc_id("s3://bucket/t.parquet", "fp1", "ch_v1")
    changed = compute_tabular_doc_id("s3://bucket/t.parquet", "fp1", "ch_v2")
    assert base != changed


def test_tabular_doc_id_changes_on_uri_change():
    base = compute_tabular_doc_id("s3://bucket/a.parquet", "fp", "ch")
    changed = compute_tabular_doc_id("s3://bucket/b.parquet", "fp", "ch")
    assert base != changed


# ── Element structure ─────────────────────────────────────────────────────────


def test_each_row_produces_one_element():
    batch = _batch(("name", ["Apple", "Google", "Meta"]), ("ticker", ["AAPL", "GOOG", "META"]))
    elements = _normalize([batch])
    assert len(elements) == 3


def test_element_type_is_row():
    batch = _batch(("col", [1, 2]))
    elements = _normalize([batch])
    for el in elements:
        assert el.type == "row"
        assert el.type in VALID_ELEMENT_TYPES


def test_section_path_is_dataset_and_table():
    batch = _batch(("x", [42]))
    elements = _normalize([batch], dataset_name="sec_filings", table_name="balance_sheet")
    assert elements[0].section_path == ["sec_filings", "balance_sheet"]


def test_element_ids_are_unique_across_rows():
    batch = _batch(("a", list(range(50))))
    elements = _normalize([batch])
    ids = [e.element_id for e in elements]
    assert len(ids) == len(set(ids))


def test_doc_id_propagated():
    batch = _batch(("a", [1]))
    elements = _normalize([batch])
    assert elements[0].doc_id == DOC_ID


# ── Text format ───────────────────────────────────────────────────────────────


def test_text_is_colon_separated_pairs():
    batch = _batch(("company", ["Acme"]), ("revenue", [100]))
    el = _normalize([batch])[0]
    assert "company: Acme" in el.text
    assert "revenue: 100" in el.text


def test_text_pairs_are_newline_separated():
    batch = _batch(("a", ["x"]), ("b", ["y"]))
    el = _normalize([batch])[0]
    lines = el.text.splitlines()
    assert len(lines) == 2


def test_null_values_omitted_from_text():
    batch = _batch(("name", ["Acme"]), ("optional", [None]))
    el = _normalize([batch])[0]
    assert "optional" not in el.text


# ── Sparse row handling ───────────────────────────────────────────────────────


def test_all_null_row_is_skipped():
    batch = _batch(("a", [None, "val", None]), ("b", [None, "x", None]))
    elements = _normalize([batch])
    assert len(elements) == 1
    assert "val" in elements[0].text


def test_partial_null_row_is_emitted():
    batch = _batch(("a", ["hello"]), ("b", [None]))
    elements = _normalize([batch])
    assert len(elements) == 1


def test_row_count_metadata_reflects_total_and_non_sparse():
    """Sparse rows are skipped; emitted count matches non-sparse rows."""
    batch = _batch(("x", [None, 1, None, 2, None]))
    elements = _normalize([batch])
    assert len(elements) == 2


# ── Multi-batch streaming ─────────────────────────────────────────────────────


def test_elements_accumulate_across_batches():
    b1 = _batch(("v", [1, 2, 3]))
    b2 = _batch(("v", [4, 5]))
    elements = _normalize([b1, b2])
    assert len(elements) == 5


def test_element_ids_unique_across_batches():
    b1 = _batch(("v", list(range(100))))
    b2 = _batch(("v", list(range(100, 200))))
    elements = _normalize([b1, b2])
    ids = [e.element_id for e in elements]
    assert len(ids) == len(set(ids))


# ── Column schema metadata ────────────────────────────────────────────────────


def test_column_schema_in_metadata():
    batch = _batch(("name", ["Acme"]), ("revenue", [1.5]))
    el = _normalize([batch])[0]
    col_schema = el.metadata["column_schema"]
    names = [c["name"] for c in col_schema]
    assert "name" in names
    assert "revenue" in names


def test_column_schema_includes_type_and_nullable():
    batch = _batch(("x", [1]))
    el = _normalize([batch])[0]
    for col in el.metadata["column_schema"]:
        assert "name" in col
        assert "type" in col
        assert "nullable" in col


# ── Partition metadata ────────────────────────────────────────────────────────


def test_partition_in_metadata_when_provided():
    batch = _batch(("val", [42]))
    elements = _normalize([batch], partition={"year": "2024", "month": "01"})
    assert elements[0].metadata["partition"] == {"year": "2024", "month": "01"}


def test_no_partition_key_when_not_provided():
    batch = _batch(("val", [1]))
    elements = _normalize([batch])
    assert "partition" not in elements[0].metadata


# ── metadata_base propagation ─────────────────────────────────────────────────


def test_base_metadata_merged_into_every_element():
    batch = _batch(("v", [1, 2, 3]))
    elements = _normalize([batch])
    for el in elements:
        assert el.metadata["source_url"] == BASE_META["source_url"]
        assert el.metadata["acl_principals"] == BASE_META["acl_principals"]


# ── Normalized values ─────────────────────────────────────────────────────────


def test_native_float_column_in_normalized_values():
    batch = pa.record_batch({"revenue": pa.array([1.5, 2.75], type=pa.float64())})
    elements = _normalize([batch])
    for el in elements:
        assert "revenue" in el.metadata.get("normalized_values", {})


def test_native_date_column_in_normalized_values():
    batch = pa.record_batch(
        {"report_date": pa.array([datetime.date(2024, 1, 1)], type=pa.date32())}
    )
    el = _normalize([batch])[0]
    assert "report_date" in el.metadata.get("normalized_values", {})


def test_string_date_column_normalized_by_name_hint():
    batch = _batch(("fiscal_year", ["FY2023"]))
    el = _normalize([batch])[0]
    nv = el.metadata.get("normalized_values", {})
    assert "fiscal_year" in nv
    assert nv["fiscal_year"] == "2023-01-01"


def test_string_currency_column_normalized_by_name_hint():
    batch = _batch(("revenue", ["$1.2B"]))
    el = _normalize([batch])[0]
    nv = el.metadata.get("normalized_values", {})
    assert "revenue" in nv
    assert float(nv["revenue"]) == pytest.approx(1200.0)


def test_unrecognized_string_column_not_in_normalized_values():
    batch = _batch(("description", ["some text"]))
    el = _normalize([batch])[0]
    nv = el.metadata.get("normalized_values", {})
    assert "description" not in nv


# ── row_id in metadata ────────────────────────────────────────────────────────


def test_row_id_present_in_metadata():
    batch = _batch(("x", [1]))
    el = _normalize([batch])[0]
    assert "row_id" in el.metadata


def test_row_ids_stable_for_same_doc_and_position():
    batch = _batch(("x", [99]))
    elements_a = _normalize([batch])
    elements_b = _normalize([batch])
    assert elements_a[0].metadata["row_id"] == elements_b[0].metadata["row_id"]


def test_row_ids_differ_across_rows():
    batch = _batch(("x", [1, 2, 3]))
    elements = _normalize([batch])
    row_ids = [e.metadata["row_id"] for e in elements]
    assert len(row_ids) == len(set(row_ids))


# ── Empty input ───────────────────────────────────────────────────────────────


def test_empty_batch_list_returns_empty():
    schema = pa.schema([pa.field("x", pa.int64())])
    elements = normalize_tabular(
        batches=iter([]),
        schema=schema,
        doc_id=DOC_ID,
        dataset_name="ds",
        table_name="tbl",
        metadata_base=BASE_META,
    )
    assert elements == []
