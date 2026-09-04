# ADR-0007: FilterSet compilable subset

## Status

Accepted (M0).

## Context

A FilterSet is how a user selects the documents a transformation will act on.
It is therefore not a search convenience: it is the definition of a destructive
operation's blast radius. Being approximately right is not an option.

The temptation is to build a rich, expressive filter language - arbitrary
nesting, OR across any combination of fields, regex everywhere - and then make
it work by whatever means necessary. The "whatever means necessary" is where the
danger lies: when the API cannot express a condition, the obvious fallback is to
fetch a broader set of documents and filter them in PaperWrench.

That fallback is unacceptable here, for reasons that compound:

- To client-filter honestly you must fetch *every* candidate document. On a
  50,000-document library that is hundreds of paginated requests, and users
  will not wait, so in practice such implementations quietly cap the fetch -
  and a capped fetch means the preview shows a subset of what exists while the
  user believes they are seeing everything.
- The count is then wrong, and the count is the number the user checks before
  clicking a destructive button.
- Two code paths with subtly different semantics (server-side lookups versus
  client-side comparison) will diverge on edge cases: case sensitivity, null
  handling, accent folding, empty-versus-missing.
- The failure is invisible. Nothing errors; the user simply operates on the
  wrong set.

The Paperless filter surface was mapped during the review. It is genuinely
capable: `CHAR_KWARGS`, `ID_KWARGS`, `INT_KWARGS`, `DATE_KWARGS` and
`DATETIME_KWARGS` lookups on core fields, and `custom_field_query`, which
supports nested AND/OR/NOT (depth ≤ 10, ≤ 20 atoms) on custom fields. What it
does not support is arbitrary OR across heterogeneous core fields: Django
filter backends compose query parameters with AND, so "title contains X OR
correspondent is Y" has no server-side expression.

## Decision

PaperWrench defines a **compilable subset**: every FilterSet must compile,
completely and exactly, to Paperless query parameters. There is no client-side
filtering fallback. Ever.

A FilterSet that cannot be compiled is rejected at validation time with
`FILTER_NOT_COMPILABLE`, and the error names the specific condition that cannot
be expressed and why. The user is told before they build an operation on it,
not after.

What is in the subset:

- Core field conditions using the lookups Paperless actually exposes
  (`__icontains`, `__iexact`, `__gt`, `__lt`, `__gte`, `__lte`, `__in`,
  `__isnull`, and the date variants), combined with AND.
- Custom field conditions compiled to `custom_field_query`, which may use
  nested AND/OR/NOT within the documented limits of depth 10 and 20 atoms.
- Ordering restricted to the server's `ordering_fields` whitelist, including
  `custom_field_<id>`. An unsupported ordering is rejected rather than silently
  dropped - a preview sorted differently from the executed job is a lie about
  which documents "the first 50" are.

What is explicitly outside the subset, and rejected:

- OR across heterogeneous core fields.
- Nesting of custom field conditions beyond the server's limits.
- Any condition requiring evaluation PaperWrench would have to perform itself.

Two consequences of this are enforced rather than left to discipline:

**Count and page must come from the server.** The result count shown before a
destructive operation is Paperless's `count`, obtained with the same parameters
the job will use. It is not derived from a fetched page.

**The compiled query is stored with the job.** The exact parameter set is
recorded so the history can show precisely what was asked, and so a preview and
its execution provably used the same query. Note that the job still executes
against a *materialised list of document ids* captured at creation time
(ADR-0003) - the stored query is for explanation and auditability, not
re-evaluation.

## Consequences

The filter builder must communicate its limits in the UI. A condition that
cannot be compiled has to be visibly unavailable or clearly explained at the
point of construction, not rejected as a surprise on submit. That is real
frontend work, and it is the price of honesty.

Some legitimate user intentions cannot be expressed. "Documents where the title
contains X or the correspondent is Y" requires two FilterSets and two
operations. This is an acceptable inconvenience, and it is preferable to a
plausible-looking filter that operates on the wrong set.

Every filter, however large the library, runs at server speed with a correct
count and correct pagination. Performance stays predictable because the work
happens where the index is.

If Paperless later gains a richer filter API, the subset expands - and
`FILTER_NOT_COMPILABLE` becomes the mechanism that tells us exactly which
conditions users were asking for.

## Alternatives considered

**Client-side filtering fallback.** Rejected: wrong counts, capped result sets,
divergent semantics, and silent failure - all on the code path that decides
what a destructive operation touches.

**Hybrid: server-side filter then client-side refinement.** Rejected: it is the
same problem with extra steps, and the count remains unreliable.

**Restricting the UI to a single condition.** Rejected as unnecessarily
limiting: AND composition and `custom_field_query` cover the great majority of
real use cases, including the reference `Relevé de vacations` scenario.

**Silently dropping conditions that cannot be compiled.** Rejected with
prejudice: this is the worst possible behaviour, since it broadens the
operation's scope without telling anyone.
