# M7 Dry Run API

All paths are below `/api/v1`. These routes stage/review proposed results in
PaperWrench only. They never mutate Paperless documents. No Apply route exists.
See [ADR-0013](decisions/0013-expiring-preview-staging-and-confirmation.md) for
canonicalization, lifecycle, error policy and the M8 handoff contract.

## Create

`POST /previews` accepts the existing M6 `Transformation` without a second
selection syntax. A dataset is `SearchSpec + FilterSet + Ordering`; no page
parameters belong in its identity. Example:

```json
{
  "targets": {"source": "ids", "document_ids": [12, 13]},
  "operations": [{
    "operation": "template",
    "field": {"source": "core", "name": "title"},
    "template": "Relevé de vacations – {Période concernée}",
    "bindings": {"Période concernée": {"source": "custom_field", "field_id": 7}}
  }]
}
```

Dataset targets instead use `{"source":"dataset","query":{"search":null,
"filters":{"root":{"children":[{"kind":"condition","field":{"source":
"core","name":"document_type"},"operator":"equals","value":3}]}},
"ordering":"title"}}`.

201 returns `id`, `version=1`, `created_at`, `expires_at`, `matched`, `evaluated`,
`changed`, `unchanged`, `errors`, `selection_fingerprint`, `spec_fingerprint`,
`target_fingerprint`, `result_fingerprint`, `confirmed=false` and `preview_token`.
Creation is a bounded synchronous HTTP operation, not a background Job. A failed
request has no usable partial result. Configure proxy timeouts accordingly.

`matched` is Paperless's first page count for datasets; for explicit IDs it is
the number requested. `evaluated` counts attempted targets, including unavailable
IDs. `changed + unchanged + errors == evaluated == matched` for every successful
preview. A document with any error belongs only to `errors`, even if another
operation would change a field. Confirmation is unavailable when errors exist
or nothing would change.

Bounds: 100,000 targets, 100 operations from M6, 100 upstream documents/page,
128 MiB result JSON, 300 seconds/build, one active builder, four retained
previews, 30-minute TTL from start. Limit failures use 422 `VALIDATION_ERROR`;
another active builder uses 409 `CONFLICT`. The UI never truncates silently.

## Read the same preview

- `GET /previews/{id}` returns the summary without the token.
- `GET /previews/{id}/documents?page=1&page_size=25&status=error` returns
  `{items,page,page_size,total,page_count}`. Omit `status` for all; allowed values
  are `change`, `unchanged`, `error`. Sizes: 25, 50, 100, 250. Out-of-range pages
  return an empty list. `total`/`page_count` describe the selected status subset.

Each item extends M6 `EvaluationResult`:

```json
{
  "document_id": 12, "title": "scan_001", "status": "change", "issue": null,
  "changes": [{
    "field": {"source":"core","name":"title"}, "operation":"template",
    "status":"change", "before":{"kind":"present","raw":"scan_001"},
    "intended":{"kind":"present","raw":"Relevé de vacations – juillet 2026"},
    "issue":null
  }]
}
```

Custom values reuse M6/M2 `TypedCustomFieldValue`, including `field_id`,
`monetary` (decimal amount string), `select_option_id` and `select_label`.
`kind=absent`, `kind=null` and `kind=present,raw=""` remain distinct; zero/false
are present values. Error operations retain `before`, have `intended=null`, and
carry a stable issue code/message. Missing documents have no fabricated changes.
There is never a `written_value`.

## Confirm review, without Apply

`POST /previews/{id}/confirm` accepts exactly:

```text
{
  preview_token, transformation,
  target_fingerprint, result_fingerprint,
  version: 1, acknowledge: true
}
```

On success, 200 returns the summary with `confirmed=true`. The token is consumed
once; no Job is created and no Paperless request occurs. Invalid/replayed/
expired/mismatched tokens produce 409 `PREVIEW_STALE`; invalid DTOs produce 422.
Readiness/expiry are also checked on page/summary access. The frontend discards
its confirmation when any selection/operation changes and keeps Apply disabled.

`DELETE /previews/{id}` idempotently discards local completed staging (204).
It does not delete a Paperless document. Expiry cleanup and startup removal of
interrupted builds are automatic. Completed previews survive restart until TTL.
Tokens remain backend-verifiable because only their hashes are persisted.

## Errors and future execution

Query failures retain existing `FILTER_NOT_COMPILABLE`/validation codes and
cause zero **document** requests. A cold metadata registry can need metadata
GETs before compilation. No fetch-all/local filtering fallback exists.
Dataset list failures, authentication/transport errors and invalid page envelopes
are fatal. Explicit-ID 404 (`DOCUMENT_UNAVAILABLE`, including invisible objects),
403 (`DOCUMENT_FORBIDDEN`), invalid document (`DOCUMENT_INVALID`), unconfirmed
edit permission (`DOCUMENT_NOT_EDITABLE`) and M6 field errors are reviewable rows.

The token binds the staged observation, not current Paperless state. M8 must
atomically claim an unconsumed token and copy exact staged targets to its own
durable rows, then re-read/revalidate at execution. Never re-evaluate the dataset
to choose Job targets, never reuse a consumed M7 review, and never treat the
token as a concurrency guarantee. A new preview is required after expiry.
