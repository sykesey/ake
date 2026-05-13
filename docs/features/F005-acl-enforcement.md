# F005 — Access Control & Row-Level Security

**Status:** Defined  
**Layer:** 2 (storage) / 3 (query)

## Statement

The system enforces document-level access control such that a principal can only retrieve artifacts compiled from documents they are authorised to read — using Postgres row-level security so that ACL enforcement is structural and cannot be bypassed by application code.

## Acceptance Criteria

- `acl_principals` is populated from source document permissions at ingest time (Box, SharePoint) and stored as a Postgres array column on `artifacts`
- A Postgres RLS policy rejects rows where `acl_principals` does not overlap the session's `app.current_principals` setting
- `execute()` sets `app.current_principals` from the authenticated `principal` before any query
- A test confirms that a principal without access to an artifact does not see it in `QueryResult.artifacts_used` or `QueryResult.data`
- ACL changes on source documents are re-propagated on re-ingestion

## Key Behaviours

- **RLS as the enforcement boundary** — ACL checks live in the database, not in application-layer `if` statements; a bug in the planner or composer cannot leak restricted artifacts
- **GIN index on `acl_principals`** — array overlap checks (`&&`) use a GIN index to avoid full-table scans
- **Propagation from source** — ACLs are read from source system metadata at ingest and stored verbatim; the engine does not define its own permission model

## Out of Scope

- Attribute-based access control (ABAC) or dynamic group membership — see database decision checklist; add Oso/Cerbos only when RLS is insufficient
- User authentication (handled by the calling application)
- Field-level redaction within an artifact
