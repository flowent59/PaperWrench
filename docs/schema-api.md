# M10 document schema contract

Schemas are PaperWrench-owned definitions in the existing `schemas` table. No
Paperless document or metadata is mirrored in SQLite. `applies_when_json` and
`rules_json` hold versioned JSON objects (`version: 1`), validated on every
read and write. The table already has both columns, so M10 needs no Alembic
revision; the existing fresh-install and drift checks still apply. Old
unversioned or malformed rows fail with 422 and are never evaluated.

## API

`GET /api/v1/schemas` lists definitions. `POST /api/v1/schemas` creates,
`GET /api/v1/schemas/{id}` reads, `PUT /api/v1/schemas/{id}` replaces and
`DELETE /api/v1/schemas/{id}` deletes a definition. Names are unique.

`GET /api/v1/schemas/{id}/evaluate?page=1&page_size=25` reads one bounded
Paperless page. Page sizes are the shared dataset sizes 25, 50 or 100. The
response carries `schema_id`, `items`, `page`, `page_size`, `total`, and
`page_count`. Each item has `document_id`, `title`, `status` (`pass`/`fail`),
and ordered `rules`. Each rule result has `rule_index`, stable `field` reference,
`kind`, `status`, `value_kind` (`absent`/`null`/`present`), `actual`, `expected`,
and a failure `code` (`REQUIRED` or `NOT_EQUAL`). This document-level result is
the handoff contract for M11; M10 does not implement a quality dashboard.

Example definition:

```json
{
  "name": "Relevé de vacations",
  "description": null,
  "applies_when": {"filters": {"root": {"kind": "group", "operator": "and", "children": [
    {"kind": "condition", "field": {"source": "core", "name": "document_type"}, "operator": "equals", "value": 1}
  ]}}},
  "rules": [
    {"kind": "required", "field": {"source": "custom_field", "field_id": 1}, "field_type": "text"},
    {"kind": "required", "field": {"source": "custom_field", "field_id": 2}, "field_type": "monetary"}
  ]
}
```

The document type ID and custom field IDs above are examples. The editor reads
the actual IDs from the Metadata Registry and Filter Engine capabilities.

## Semantics and safety

The scope is exactly the existing `DatasetQuery`: search AND `FilterSet`, with
ordering. It is validated through the shared dataset parameter builder and
FilterSet compiler both on save and immediately before every evaluation.
Unknown fields, changed metadata, invalid search/ordering and uncompilable
filters return 422. There is no local scope fallback and no document request
after such a failure. An empty query is an explicit all-document scope.

Every rule stores a `FieldRef` and its semantic `field_type` snapshot. A missing
field, changed type or removed select option makes the schema invalid until
edited; it does not silently skip the rule. Only `required` and `equals` exist.
`equals` supports scalar fields, with exact text and boolean comparison, ISO
calendar date comparison, stable select option IDs and Decimal monetary amount
comparison. Monetary comparison ignores currency, as the Filter Engine does;
the required literal is a decimal string. A label is never a select identity.

`required` fails for ABSENT, NULL, empty string and an empty set. `0` and
`false` pass. `equals` fails for ABSENT and NULL; `""` only equals an explicit
empty string. `actual` and `expected` in the result preserve their typed values;
Decimal values serialize as exact JSON strings. A malformed monetary value
cannot satisfy a monetary `equals` rule. Evaluation issues only Paperless GET
requests and never corrects documents.
