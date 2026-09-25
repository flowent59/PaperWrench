# Rollback API (M9)

All routes use `/api/v1`, the standard error envelope and `Cache-Control: no-store`.
Safety and grouped-document semantics: [ADR-0015](decisions/0015-safe-rollback-jobs.md).

1. `POST /jobs/{original_id}/rollback-preview` (no body) reads eligible original
   operations and Paperless. Returns the ordinary `CreatedPreview`, additionally
   `rollback_of_job_id` and `requires_external_race_ack`. No writes upstream.
2. `GET /previews/{id}/documents?page=1&page_size=25` shows current/restoration
   values, per-document issues, `excluded_operations` reasons, original operation
   IDs and a per-document `rollback_spec`. Pages remain bounded to 25/50/100/250.
   Existing summary/delete/expiry behavior is unchanged.
3. `POST /jobs/{original_id}/rollback` explicitly confirms:

```json
{
  "preview_id": "<preview ID>",
  "preview_token": "<creation-only capability>",
  "target_fingerprint": "<reviewed targets>",
  "result_fingerprint": "<reviewed results>",
  "version": 1,
  "acknowledge": true,
  "acknowledge_external_race": true
}
```

Returns 201 with the new durable Job; at least one eligible changed document is
required. Custom restoration requires explicit race acknowledgement. No caller
supplied IDs, specs or values are accepted by confirmation. Invalid, expired,
consumed, cross-Job or duplicate-original confirmation returns 409; missing
acknowledgement/body returns 422. An original must be a terminal transformation.

`JobView` adds `type`, `rollback_of_job_id` and `rollback_job_id` (nullable links).
`OperationView` adds `rollback_of_operation_id`. Original History is immutable;
the reverse Job link is computed. Use ordinary Job/target/operation pagination,
polling and `/jobs/{id}/resume` for rollback progress and recovery. Inspect the
linked Job after a lost confirmation response; do not attempt another rollback.

Preview conflicts, unavailable documents and manual review remain visible terminal
targets. Execution rechecks permissions, catalogue and candidate values immediately
before one coordinated document PATCH. Unrelated later edits are preserved.
Any candidate difference means conflict with no PATCH for that document. Unknown
write outcomes are never retried or automatically rollbackable. A rollback may
therefore complete partially; the UI does not offer conflict override.
