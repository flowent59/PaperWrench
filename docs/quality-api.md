# Quality API (M11)

Quality is a read-only view of saved M10 schemas. It calls the M10 evaluator for
each document returned by the schema's existing `DatasetQuery`; it does not own
another rule or filter language.

## `GET /api/v1/quality/schemas/{schema_id}`

Query parameters: `page` (from 1, default 1) and `page_size` (25, 50 or 100;
default 25). A request evaluates exactly one Paperless dataset page. The response has:

- `items`: one finding per failed rule, with document ID/title and the M10
  `RuleResult` (`field`, `value_kind`, `actual`, `expected`, `code`);
- `rules`: each rule's violation count **on this page**, exact drill-down
  `DatasetQuery` when available, and exact document IDs **on this page**;
- `evaluated_count`: documents returned and evaluated on this page;
- `violation_count`: failed rules on this page (one document may have several);
- `dataset_total`: Paperless's exact count for the schema scope, across all
  pages, regardless of conformance;
- `page`, `page_size`, `page_count`: dataset pagination.

There is no implied whole-dataset violation total. Navigate all dataset pages
to audit all documents. Paperless can change between requests, so totals and
page membership are snapshots of each request rather than a locked scan.
M10 keeps `ABSENT`, `NULL`, empty text, zero, false, Decimal monetary amounts
and stored Select IDs distinct in the result contract. A removed or retyped
field refuses evaluation with 422 before listing documents.

### Explorer drill-down

For a `required` custom scalar field, Quality builds the exact complement
`IS_MISSING OR IS_NULL`; text shaped fields use `IS_MISSING OR IS_EMPTY` so
the empty string is included. This complement is intersected with the saved
scope, including its search, and passed through M4 validation and compilation.
`exact_query` is returned only if that full query compiles. An `equals` rule,
core field or document-link field has no general exact complement in M4;
Quality returns `null` and the UI offers the explicit IDs of findings on the
evaluated page. It never drops part of a condition or widens the scope.

`POST /api/v1/documents/by-ids` accepts 1–100 explicit positive IDs. It
reads each ID, returns the Explorer's normal document row shape, and reports
`unavailable_count` without exposing a deleted or inaccessible document's
response body. This is an exact bounded selection, separate from
`DatasetQuery` and `FilterSet`. Explorer URLs carry either the full exact
dataset query or these explicit IDs so refreshing the page preserves the
drill-down. All Paperless requests on both paths are GETs.
