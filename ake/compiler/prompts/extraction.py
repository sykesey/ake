"""Extraction prompt templates for LLM-based artifact compilation."""
from __future__ import annotations

from typing import Any

from ake.compiler.artifact import DomainSchema
from ake.ingestion.element import Element

SYSTEM_PROMPT = """\
You are a structured-data extraction engine. Your task is to extract typed \
facts from a set of document elements and return them as valid JSON.

Rules:
1. Return ONLY a JSON object — no prose, no markdown fences.
2. For each field with a non-null value you MUST include a "source" block \
   with the element_id and the exact verbatim text span from which the value \
   was taken. The span must be a literal substring of that element's text.
3. If a field cannot be found in the provided elements, check the DOCUMENT \
   CONTEXT block — fields like department and owner may be stated there when \
   the section does not restate them. Use the context value with the most \
   relevant element_id as the source.
4. If a field is absent from both the elements and the document context, set \
   its value to null and omit the "source" block entirely.
5. Never invent values. If unsure, return null.
6. Preserve the exact type specified for each field (int, float, str, bool).
7. Never expand abbreviations or infer canonical names — extract the value \
   as it appears verbatim in the source text. If the text only contains an \
   abbreviation (e.g. "HR") and the field expects a full name, return null \
   unless the document context supplies the expansion explicitly.
8. For entity_id, use the shortest canonical name for the entity: strip any \
   document-hierarchy prefix (text before '›' or similar separators), and do \
   not append qualifier words like "Cycle", "Process", "Standard", or "Policy" \
   unless they are necessary to distinguish the entity from another.
"""


def _format_elements(elements: list[Element]) -> str:
    """Render elements as labelled blocks for LLM context."""
    parts: list[str] = []
    for el in elements:
        section = " > ".join(el.section_path) if el.section_path else "(root)"
        header = f"[ELEMENT {el.element_id} | {el.type} | {section}]"
        parts.append(f"{header}\n{el.text}")
    return "\n\n".join(parts)


def _format_doc_context(doc_metadata: dict[str, Any]) -> str:
    """Render document-level metadata as an authoritative context block.

    These values apply to all elements in the chunk and may be used to fill
    fields like 'department' or 'owner' when the section text does not restate
    them.  They are authoritative — prefer them over weak inferences from
    abbreviations in the body text.
    """
    _RELEVANT_KEYS = ("department", "owner", "doc_type", "source_url", "acl_principals")
    lines: list[str] = []
    for key in _RELEVANT_KEYS:
        val = doc_metadata.get(key)
        if val is not None:
            lines.append(f"  {key}: {val}")
    return "\n".join(lines) if lines else "  (none)"


def _schema_description(schema: DomainSchema) -> str:
    lines = [
        f"Artifact type: {schema.artifact_type}",
        f"Description: {schema.description}",
        "",
        "Fields to extract:",
    ]
    for name, spec in schema.fields.items():
        req = " (required)" if spec.required else ""
        lines.append(f"  {name} ({spec.type}){req}: {spec.description}")
    return "\n".join(lines)


def build_extraction_messages(
    elements: list[Element],
    schema: DomainSchema,
    doc_metadata: dict[str, Any] | None = None,
) -> list[dict]:
    """Build the messages list for LLMRouter.complete().

    Args:
        elements: Source elements for this chunk.
        schema: The DomainSchema describing what to extract.
        doc_metadata: Document-level metadata (e.g. department, doc_type) from
            the ingestion pipeline. Included as authoritative context so fields
            like department/owner propagate even when not restated per section.

    The expected JSON response shape:

        {
          "entity_id": "<value of entity_id_field>",
          "fiscal_year": <int or null>,
          "fields": {
            "<field_name>": {
              "value": <typed value or null>,
              "source": {
                "element_id": "<element_id>",
                "verbatim_span": "<exact substring of element text>"
              }
            },
            ...
          }
        }
    """
    schema_desc = _schema_description(schema)
    element_context = _format_elements(elements)
    doc_context_block = _format_doc_context(doc_metadata or {})

    fy_instruction = (
        f'Set "fiscal_year" to the integer value of field "{schema.fiscal_year_field}" '
        f"if present, otherwise null."
        if schema.fiscal_year_field
        else 'Set "fiscal_year" to null.'
    )

    user_content = f"""\
{schema_desc}

---
DOCUMENT CONTEXT (authoritative — applies to all elements below)
---
{doc_context_block}

---
DOCUMENT ELEMENTS
---
{element_context}

---
OUTPUT FORMAT
---
Return a single JSON object with this structure:
{{
  "entity_id": "<value of the '{schema.entity_id_field}' field>",
  "fiscal_year": <int or null>,
  "fields": {{
    "<field_name>": {{
      "value": <typed value or null>,
      "source": {{
        "element_id": "<element_id from above>",
        "verbatim_span": "<exact text copied from that element>"
      }}
    }}
  }}
}}

{fy_instruction}
Omit "source" when "value" is null.
"""

    return [{"role": "user", "content": user_content}]
