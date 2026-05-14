# F001 — Document Ingestion & Parsing

**Status:** Implemented  
**Layer:** 1 — Ingestion & Parsing

## Statement

The system ingests raw source documents (PDF, DOCX, HTML) and produces normalized `Element` records tagged with stable IDs, semantic section paths, and source metadata — creating a format-agnostic foundation that downstream compilation can consume without parsing knowledge.

## Acceptance Criteria

- Documents from each supported format (PDF, DOCX, HTML) parse without error
- Every `Element` carries: `doc_id`, `element_id`, `type`, `text`, `page`, `section_path`, and `metadata.source_url`
- `section_path` is populated and accurate (verified by manual spot-check)
- Ingesting an unchanged document produces an identical `doc_id` and element set (idempotency)
- Source ACLs (Box, SharePoint) are propagated into `metadata.acl_principals`
- Parser output is retrievable from the store by `doc_id`

## Key Behaviours

- **Content-hash stability** — `doc_id` is a stable hash of source content, enabling incremental re-ingestion without duplication
- **Section-path navigation** — heading hierarchy is extracted into `section_path` so the compiler can locate elements by semantic position (e.g. `["Item 7", "Capital Returns"]`) rather than raw offset
- **Format abstraction** — the `normalizer` maps any supported parser's output to the `Element` schema; the compiler never touches parser-specific data structures

## Out of Scope

- Structured extraction of domain facts (Layer 2)
- Permission enforcement at query time (Layer 3)
- Handling of audio, video, or image-only documents
