"""Extraction prompt templates for LLM-based artifact compilation."""
from __future__ import annotations

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
3. If a field cannot be found in the provided elements, set its value to null \
   and omit the "source" block entirely.
4. Never invent values. If unsure, return null.
5. Preserve the exact type specified for each field (int, float, str, bool).
"""


def _format_elements(elements: list[Element]) -> str:
    """Render elements as labelled blocks for LLM context."""
    parts: list[str] = []
    for el in elements:
        section = " > ".join(el.section_path) if el.section_path else "(root)"
        header = f"[ELEMENT {el.element_id} | {el.type} | {section}]"
        parts.append(f"{header}\n{el.text}")
    return "\n\n".join(parts)


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
) -> list[dict]:
    """Build the messages list for LLMRouter.complete().

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

    fy_instruction = (
        f'Set "fiscal_year" to the integer value of field "{schema.fiscal_year_field}" '
        f"if present, otherwise null."
        if schema.fiscal_year_field
        else 'Set "fiscal_year" to null.'
    )

    user_content = f"""\
{schema_desc}

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
