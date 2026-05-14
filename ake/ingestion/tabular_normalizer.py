"""Tabular normalizer — converts pyarrow RecordBatches into AKE Element records.

Each non-sparse row becomes one Element(type="row"). Element.text is a newline-
separated sequence of "column_name: raw_value" pairs (verbatim, so TabularRef
citation verification can find the raw value in the text). Normalized versions
of date and currency columns are stored in metadata.normalized_values so the
compiler's direct-mapping path can use them without LLM calls (ADR-009).

Sparse rows (all cells null) are never emitted.
"""
from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Iterable

from ake.ingestion.element import Element, compute_element_id

if TYPE_CHECKING:
    import pyarrow as pa

logger = logging.getLogger(__name__)

# Column name substrings that suggest a currency value in a string column.
_CURRENCY_HINTS: frozenset[str] = frozenset(
    {
        "revenue", "amount", "value", "price", "cost", "income", "earnings",
        "profit", "sales", "assets", "liabilities", "equity", "capex", "opex",
        "expense", "fee", "salary", "compensation",
    }
)

# Column name substrings that suggest a date value in a string column.
_DATE_HINTS: frozenset[str] = frozenset(
    {"date", "period", "year", "quarter", "month", "fiscal", "timestamp"}
)


def _column_schema(schema: "pa.Schema") -> list[dict]:
    return [
        {"name": f.name, "type": str(f.type), "nullable": f.nullable}
        for f in schema
    ]


def _row_text(col_names: list[str], row_values: list) -> str:
    """Build the verbatim text for a row element."""
    return "\n".join(
        f"{name}: {val}"
        for name, val in zip(col_names, row_values)
        if val is not None
    )


def _normalize_values(
    col_names: list[str],
    col_types: list["pa.DataType"],
    row_values: list,
) -> dict[str, str]:
    """Apply normalize_currency / normalize_date to appropriate columns.

    Returns a dict mapping column_name -> normalized_string for every column
    that produced a non-None normalized result. Columns that are natively typed
    (Arrow date/timestamp/numeric) are converted to canonical string form.
    String columns whose names suggest dates or currencies are attempted.
    """
    import pyarrow.types as pat

    from ake.compiler.skills.normalize_currency import normalize_currency
    from ake.compiler.skills.normalize_date import normalize_date

    out: dict[str, str] = {}
    for name, arrow_type, raw in zip(col_names, col_types, row_values):
        if raw is None:
            continue

        col_lower = name.lower()

        # Native Arrow date / timestamp types → ISO string (no skill call needed).
        if pat.is_date(arrow_type) or pat.is_timestamp(arrow_type):
            out[name] = str(raw)
            continue

        # Native Arrow numeric types → canonical string (already structured).
        if pat.is_floating(arrow_type) or pat.is_integer(arrow_type) or pat.is_decimal(arrow_type):
            out[name] = str(raw)
            continue

        # String column: try currency then date based on column name hints.
        raw_str = str(raw)
        if any(h in col_lower for h in _CURRENCY_HINTS):
            result = normalize_currency(raw_str)
            if result is not None:
                out[name] = str(result)
                continue

        if any(h in col_lower for h in _DATE_HINTS):
            result = normalize_date(raw_str)
            if result is not None:
                out[name] = result.isoformat()
                continue

    return out


def normalize_tabular(
    batches: Iterable["pa.RecordBatch"],
    schema: "pa.Schema",
    doc_id: str,
    dataset_name: str,
    table_name: str,
    metadata_base: dict,
    partition: dict[str, str] | None = None,
) -> list[Element]:
    """Convert an iterable of RecordBatches into AKE Element records.

    Args:
        batches:       Iterator of pyarrow RecordBatch objects (streaming-safe).
        schema:        Full pyarrow Schema for the table (used for metadata).
        doc_id:        Tabular doc_id (from compute_tabular_doc_id).
        dataset_name:  Top-level dataset name (e.g. parent directory or dataset label).
        table_name:    Table / file name within the dataset.
        metadata_base: Dict merged into every element's metadata.
        partition:     Hive-style partition key-values, if any.

    Returns:
        List of Element records, one per non-sparse row.
    """
    col_schema = _column_schema(schema)
    section_path = [dataset_name, table_name]
    global_row_idx = 0
    total_rows = 0
    emitted_rows = 0
    elements: list[Element] = []

    for batch in batches:
        col_names = batch.schema.names
        col_types = [batch.schema.field(n).type for n in col_names]
        col_arrays = [batch.column(n) for n in col_names]
        num_rows = batch.num_rows
        total_rows += num_rows

        for row_i in range(num_rows):
            row_values = [arr[row_i].as_py() for arr in col_arrays]

            # Skip sparse rows: all cells null.
            if all(v is None for v in row_values):
                global_row_idx += 1
                continue

            text = _row_text(col_names, row_values)
            element_id = compute_element_id(doc_id, global_row_idx, "row", text)
            row_id = hashlib.sha256(
                f"{doc_id}:{global_row_idx}".encode()
            ).hexdigest()[:16]

            normalized = _normalize_values(col_names, col_types, row_values)

            meta: dict = {
                **metadata_base,
                "column_schema": col_schema,
                "row_id": row_id,
            }
            if normalized:
                meta["normalized_values"] = normalized
            if partition:
                meta["partition"] = partition

            elements.append(
                Element(
                    doc_id=doc_id,
                    element_id=element_id,
                    type="row",
                    text=text,
                    page=0,
                    section_path=section_path,
                    metadata=meta,
                )
            )
            emitted_rows += 1
            global_row_idx += 1

    logger.debug(
        "tabular_normalize dataset=%s table=%s total_rows=%d emitted=%d sparse_skipped=%d",
        dataset_name,
        table_name,
        total_rows,
        emitted_rows,
        total_rows - emitted_rows,
    )

    return elements
