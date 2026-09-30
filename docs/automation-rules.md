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
