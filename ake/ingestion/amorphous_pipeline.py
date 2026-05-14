"""Amorphous ingestion pipeline — auto-discovers and ingests all data files in a directory.

Entry point: ``ingest_directory(source_dir, dataset_name)``

Produces an :class:`AmorphousIngestionResult` containing:
- One :class:`TableInfo` per tabular file (CSV / Parquet / Arrow IPC)
- One :class:`DocumentInfo` per document file (PDF / DOCX / HTML / etc.)
- FK :class:`InferredRelationship` objects discovered from column naming + value overlap
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ake.ingestion.element import Element
from ake.ingestion.pipeline import IngestionPipeline, IngestionResult

_TABULAR_SUFFIXES = frozenset({".parquet", ".csv", ".arrow", ".feather", ".arrows"})
_DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".doc", ".html", ".htm", ".txt", ".md"})

# Semantic role detection hints (col name substrings)
_CURRENCY_HINTS = frozenset({"amount", "price", "cost", "revenue", "salary", "budget", "fee", "wage"})
_DATE_HINTS = frozenset({"date", "_at", "_on", "timestamp", "_time"})
_CAT_HINTS = frozenset({"status", "category", "department", "region", "type", "remote", "gender", "kind", "role"})
_MEASURE_HINTS = frozenset({"count", "quantity", "qty", "hours", "headcount", "score", "num_", "total_"})
_TEXT_HINTS = frozenset({"description", "notes", "comment", "text", "summary", "bio"})
_LABEL_SET = frozenset({"name", "title", "label", "display_name", "full_name"})


@dataclass
class ColumnInfo:
    name: str
    pa_type: str
    nullable: bool
    semantic_role: str  # entity_id | foreign_key | label | currency | date | categorical | measure | boolean | text | unknown


@dataclass
class TableInfo:
    name: str
    source_path: Path
    result: IngestionResult
    columns: list[ColumnInfo]
    partition_keys: dict[str, str] = field(default_factory=dict)
    row_count: int = 0


@dataclass
class DocumentInfo:
    name: str
    source_path: Path
    result: IngestionResult
    element_count: int = 0


@dataclass
class InferredRelationship:
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    confidence: float
    evidence: str  # "naming" | "values" | "both"


@dataclass
class DocumentLink:
    document_name: str  # DocumentInfo.name (stem)
    entity_id: str      # matched entity ID value (e.g. "T001", "PR003")
    table_name: str     # table that owns the entity
    column_name: str    # entity_id column in that table
    doc_type: str       # inferred from filename suffix ("vision", "status", "workspec", …)


@dataclass
class AmorphousIngestionResult:
    dataset_name: str
    source_dir: Path
    tables: list[TableInfo]
    documents: list[DocumentInfo]
    relationships: list[InferredRelationship]
    document_links: list[DocumentLink] = field(default_factory=list)

    @property
    def all_elements(self) -> list[Element]:
        els: list[Element] = []
        for t in self.tables:
            els.extend(t.result.elements)
        for d in self.documents:
            els.extend(d.result.elements)
        return els


# ---------------------------------------------------------------------------
# Semantic role detection
# ---------------------------------------------------------------------------

def _semantic_role(col_name: str, table_name: str = "") -> str:
    """Classify a column's semantic role from its name and owning table name."""
    col = col_name.lower()

    # Primary key: "id" or "{singular_table}_id"
    singular = table_name.rstrip("s") if table_name.endswith("s") else table_name
    if col == "id" or col == f"{singular}_id" or col == f"{table_name}_id":
        return "entity_id"

    # Foreign key (any other *_id column)
    if col.endswith("_id"):
        return "foreign_key"

    if col in _LABEL_SET:
        return "label"
    if any(h in col for h in _CURRENCY_HINTS):
        return "currency"
    if any(h in col for h in _DATE_HINTS):
        return "date"
    if any(h in col for h in _CAT_HINTS):
        return "categorical"
    if any(h in col for h in _MEASURE_HINTS):
        return "measure"
    if col.startswith("is_") or col.startswith("has_"):
        return "boolean"
    if any(h in col for h in _TEXT_HINTS):
        return "text"
    return "unknown"


# ---------------------------------------------------------------------------
# Relationship inference
# ---------------------------------------------------------------------------

def _parse_row_text(text: str) -> dict[str, str]:
    row: dict[str, str] = {}
    for line in text.splitlines():
        if ": " in line:
            col, _, val = line.partition(": ")
            row[col.strip()] = val.strip()
    return row


def _column_values(table: TableInfo, column: str) -> set[str]:
    values: set[str] = set()
    for el in table.result.elements:
        row = _parse_row_text(el.text)
        v = row.get(column, "").strip()
        if v:
            values.add(v)
    return values


def _infer_relationships(tables: list[TableInfo]) -> list[InferredRelationship]:
    """Detect FK relationships using naming conventions and value overlap.

    For a column like ``lead_employee_id``:
    1. Strip ``_id`` → ``lead_employee``
    2. Split on ``_`` → ``["lead", "employee"]``
    3. Check suffixes: ``employee`` → look for table ``employees``
    4. Confirm with value overlap against ``employees.employee_id``
    """
    tbl_by_name: dict[str, TableInfo] = {t.name: t for t in tables}
    relationships: list[InferredRelationship] = []
    seen: set[tuple[str, str, str, str]] = set()

    for src_table in tables:
        fk_cols = [c for c in src_table.columns if c.semantic_role == "foreign_key"]

        for fk_col in fk_cols:
            base = fk_col.name[:-3]  # strip "_id"
            tokens = base.split("_")

            # Build candidate (target_table_name, expected_target_col) pairs
            # Direct: base or base+"s" → table; compound suffix: last N tokens
            candidates: list[tuple[str, str]] = []
            for n in range(len(tokens), 0, -1):
                suffix = "_".join(tokens[len(tokens) - n:])
                for variant in (suffix, suffix + "s"):
                    tgt_col_hint = f"{suffix}_id"
                    candidates.append((variant, tgt_col_hint))

            matched = False
            for tgt_name, tgt_col_hint in candidates:
                tgt_table = tbl_by_name.get(tgt_name)
                if not tgt_table or tgt_table.name == src_table.name:
                    continue

                # Choose the best-matching column in the target table
                tgt_col_names = {c.name for c in tgt_table.columns}
                if tgt_col_hint in tgt_col_names:
                    tgt_col = tgt_col_hint
                elif "id" in tgt_col_names:
                    tgt_col = "id"
                else:
                    entity_cols = [c for c in tgt_table.columns if c.semantic_role == "entity_id"]
                    tgt_col = entity_cols[0].name if entity_cols else None
                if not tgt_col:
                    continue

                key = (src_table.name, fk_col.name, tgt_table.name, tgt_col)
                if key in seen:
                    matched = True
                    break
                seen.add(key)

                evidence = "naming"
                confidence = 0.70

                src_vals = _column_values(src_table, fk_col.name)
                tgt_vals = _column_values(tgt_table, tgt_col)
                if src_vals and tgt_vals:
                    overlap = len(src_vals & tgt_vals) / len(src_vals)
                    if overlap >= 0.5:
                        evidence = "both"
                        confidence = min(0.95, 0.70 + overlap * 0.25)
                    elif overlap > 0:
                        evidence = "both"
                        confidence = 0.60 + overlap * 0.15

                relationships.append(InferredRelationship(
                    source_table=src_table.name,
                    source_column=fk_col.name,
                    target_table=tgt_table.name,
                    target_column=tgt_col,
                    confidence=round(confidence, 3),
                    evidence=evidence,
                ))
                matched = True
                break

    return relationships


# ---------------------------------------------------------------------------
# Document-entity linking
# ---------------------------------------------------------------------------

def _infer_document_links(
    tables: list[TableInfo],
    documents: list[DocumentInfo],
) -> list[DocumentLink]:
    """Match documents to their related entities by scanning entity IDs in filenames.

    For a file like ``project_PR001_status.html`` the stem is split on ``_``
    giving tokens ``["project", "PR001", "status"]``. Each token is checked
    against a map of all known entity ID values across every table.  The first
    matching token determines the link; remaining tokens become the ``doc_type``.
    """
    entity_id_map: dict[str, tuple[str, str]] = {}
    for tbl in tables:
        for col in tbl.columns:
            if col.semantic_role == "entity_id":
                for el in tbl.result.elements:
                    row = _parse_row_text(el.text)
                    val = row.get(col.name, "").strip()
                    if val:
                        entity_id_map[val] = (tbl.name, col.name)

    links: list[DocumentLink] = []
    for doc in documents:
        tokens = doc.name.split("_")
        for i, token in enumerate(tokens):
            if token in entity_id_map:
                table_name, column_name = entity_id_map[token]
                doc_type = "_".join(tokens[i + 1:]) if i + 1 < len(tokens) else "document"
                links.append(DocumentLink(
                    document_name=doc.name,
                    entity_id=token,
                    table_name=table_name,
                    column_name=column_name,
                    doc_type=doc_type,
                ))
                break
    return links


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def ingest_directory(
    source_dir: Path | str,
    dataset_name: str | None = None,
    metadata_base: dict | None = None,
) -> AmorphousIngestionResult:
    """Auto-discover and ingest every data file in *source_dir*.

    Tabular files (.csv, .parquet, .arrow, .feather, .arrows) become
    :class:`TableInfo` objects with schema, column metadata, and elements.
    Document files (.pdf, .docx, .html, .txt, .md) become :class:`DocumentInfo`
    objects. FK relationships between tables are inferred automatically.

    Args:
        source_dir:    Directory to scan (searched recursively).
        dataset_name:  Override dataset name (default: directory name).
        metadata_base: Extra fields merged into every element's metadata.
    """
    source_dir = Path(source_dir)
    # If the directory is literally named "data", use the parent's name instead
    _dir_name = source_dir.name
    if _dir_name.lower() in ("data", "dataset", "datasets") and source_dir.parent.name:
        _dir_name = source_dir.parent.name
    ds_name = dataset_name or _dir_name
    metadata_base = metadata_base or {}

    pipeline = IngestionPipeline()
    tables: list[TableInfo] = []
    documents: list[DocumentInfo] = []

    files = sorted(
        f for f in source_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in (_TABULAR_SUFFIXES | _DOCUMENT_SUFFIXES)
    )

    for file_path in files:
        suffix = file_path.suffix.lower()
        meta = {"source_url": str(file_path), "dataset_name": ds_name, **metadata_base}

        if suffix in _TABULAR_SUFFIXES:
            result = await pipeline.ingest_tabular_file(
                file_path, metadata=meta, dataset_name=ds_name
            )
            col_schema: list[dict] = []
            if result.elements:
                col_schema = result.elements[0].metadata.get("column_schema", [])

            columns = [
                ColumnInfo(
                    name=c["name"],
                    pa_type=c.get("type", "string"),
                    nullable=c.get("nullable", True),
                    semantic_role=_semantic_role(c["name"], file_path.stem),
                )
                for c in col_schema
            ]
            tables.append(TableInfo(
                name=file_path.stem,
                source_path=file_path,
                result=result,
                columns=columns,
                row_count=len(result.elements),
            ))
        else:
            try:
                result = await pipeline.ingest_file(file_path, metadata=meta)
                documents.append(DocumentInfo(
                    name=file_path.stem,
                    source_path=file_path,
                    result=result,
                    element_count=len(result.elements),
                ))
            except ValueError:
                pass  # unsupported extension — skip

    return AmorphousIngestionResult(
        dataset_name=ds_name,
        source_dir=source_dir,
        tables=tables,
        documents=documents,
        relationships=_infer_relationships(tables),
        document_links=_infer_document_links(tables, documents),
    )
