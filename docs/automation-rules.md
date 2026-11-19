# Saved bulk rules

Issue #50 introduces private, manually triggered bulk rules. A rule stores a
name, description, ordered transformation operations and one target:

- A `DatasetQuery` with an explicit FilterSet or search. It uses the same
  strict compiler as Explorer and manual transformations.
- A collection owned by the same Paperless user. Dynamic membership is
  resolved from its filter at preview time; static membership is snapshotted
  from stored IDs. An empty or oversized static collection is rejected.

`POST /api/v1/rules` creates revision 1. `GET /api/v1/rules` lists only the
current user's rules; `GET /{id}` reads one. `PUT /{id}` requires
`expected_revision`, validates the new definition and appends an immutable
revision. `GET /{id}/revisions` returns this change history. `DELETE /{id}`
removes the reusable definition; past job audit remains.

`POST /{id}/preview` resolves the target and uses the normal read-only
PreviewService. It returns the resolved `transformation` alongside the normal
preview token and summary. The user reviews the paginated preview through the
existing preview API. `POST /{id}/apply` requires the normal `CreateJob`
confirmation, including the preview token, target and result fingerprints,
acknowledgement and resolved transformation. Apply checks the owner and rule
revision again in the same SQLite transaction as preview consumption and Job
creation. A normal `/jobs` request cannot consume a rule preview.

An unfinished job with any shared document ID blocks a rule execution. A
manual job also cannot overlap an unfinished rule job. SQLite's immediate
transaction serializes this check with Job creation. The worker performs its
existing fresh document, permission, and metadata checks before each write.
Jobs retain the rule ID, revision and name plus the existing complete operation
audit. Rollback uses the same preview and rollback engine as manual jobs.

Manual repetition requires a new preview and explicit confirmation. Issue #51
adds opt-in [scheduled execution](automation-schedules.md), with approval tied
to the rule revision and the approving user's active session.

## Cleaning and copying metadata

- **Add or remove tags** changes the selected tags and retains the others.
  A request may add and remove different tags in the same operation.
- **Normalize text** targets titles and writable text fields. Trimming and
  collapsing use Unicode whitespace. Lowercase, uppercase and case folding
  use Python’s Unicode mappings without locale-specific rules.
- **Copy a field** supports matching data types. Titles and short/long
  text are compatible, as are creation dates and custom dates, and archive
  numbers and custom integers. Text and numbers are separate types.
  Monetary values carry their original decimal string and currency.
  Select option IDs must resolve in the destination definition.
- **Fill if not filled in** checks the selected states on each document.
  Custom fields can be absent or null. Text fields can also contain an empty
  string. Optional core fields can be null, and tag sets can be empty.
  Zero and false count as filled.

Each destination accepts one operation. All operations read the same original
document, so copying from a field edited by another operation uses its
original value. Preview captures the proposed values. Jobs re-read each
document and verify the reviewed changes before writing.
