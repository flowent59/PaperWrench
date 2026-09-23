# ADR-0010: Search is a separate primitive, not a FilterSet condition

## Status

Accepted (M4).

## Context

M4 built the FilterSet: a nested AND/OR tree of `field operator value`
conditions that compiles to a Paperless query. The obvious next thought is
that everything the Explorer sends should be one of those, so that a saved
selection is a single object and the API has a single concept.

Full-text search resists that, and the resistance turns out to be
informative rather than inconvenient.

**Search is a different mechanism, not a different field.** VERIFIED_SOURCE
(3.1.2, `documents/views.py`): when any of `text`, `title_search`, `query` or
`more_like_id` is present, `DocumentViewSet.list` abandons the ordinary
queryset path entirely. It runs the query against a **Tantivy index**, gets
back a ranked list of document ids, and intersects those ids with the
ORM-filtered queryset:

```python
filtered_qs = self.filter_queryset(self.get_queryset())
result = run_text_search(backend, user, filtered_qs)
```

Filters narrow a queryset. Search produces a set of ids from a different
engine, with its own ranking, its own tokenisation, and its own idea of what
matching means. That the two intersect is a convenience of the
implementation, not evidence that they are the same kind of thing.

**Only one search may be active.** The server returns 400 for a request
carrying more than one of the four search parameters. A FilterSet, by
construction, allows any number of conditions in any combination. Modelling
search as a condition would mean a tree that can trivially express something
the server rejects, and a compiler that has to reach across the whole tree to
notice — the exact class of "valid model, refused query" that ADR-0007
already forces us to handle, added voluntarily this time.

**Advanced search carries a foreign language.** The `query` parameter is raw
Tantivy syntax. PaperWrench does not parse it, does not validate it, and
should not: it is not our grammar, its capabilities change with the upstream
index, and a partial parser would be worse than none. As a `FilterCondition`
it would have to be something like
`field=<none> operator=matches value="title:foo AND created:[..]"` — a
pseudo-operator whose value is an opaque foreign expression the engine cannot
reason about, sitting in a tree whose entire purpose is that every node can be
reasoned about.

**Three modes are three questions.** `title_search`, `text` and `query` search
the title, the extracted content and the whole index respectively. M3
collapsed them into one `search` parameter that always meant `title_search`
and documented that nowhere — a small ambiguity that would have become a large
one the moment anything relied on it.

## Decision

A dataset is composed of **four separate primitives**:

```
Dataset query = SearchSpec + FilterSet + Ordering + Pagination
```

- **`SearchSpec`** — `{mode, text}` where mode is `title`, `content` or
  `advanced`. Exactly one mode, exactly one query string, or no SearchSpec at
  all. `advanced` is passed through opaquely and PaperWrench claims nothing
  about it.
- **`FilterSet`** — the compilable condition tree (ADR-0007).
- **`Ordering`** — a single allowlisted key. Not a filter: it changes which
  documents "the first fifty" are, never which documents match.
- **`Pagination`** — a window onto a result, and the only one of the four that
  is not part of the dataset's *identity*.

The first three make up `DatasetQuery`, which has a stable fingerprint. That
is what a later milestone means by "the same selection": Transform (M6), Dry
Run (M7), Jobs (M8), "select all matching" and Collections all name a dataset,
and they all name it the same way.

They compose with AND at the server, which is the only composition Paperless
offers between them, and the only one PaperWrench claims.

## Consequences

The API carries four fields where one might have been tidier. In exchange,
every one of them means exactly one thing, and none of them can express a
request the server would reject.

`SearchSpec` cannot be OR-ed with a filter condition. "Title contains X **or**
Montant is missing" is not expressible — but it was not expressible anyway
(ADR-0007: no OR across heterogeneous core fields), so nothing was lost by
being honest about it up front rather than at compile time.

The Filter Engine stays a closed, analysable language. Every node in a
FilterSet has a known field, a known operator and a typed value; there is no
node whose meaning is "some string another system will interpret". That is
what makes the compiler exhaustively testable, and it is what would have been
given up to make everything a FilterSet.

Saved filters and saved searches will be separately meaningful when they
arrive. A user who saves "documents where Montant is missing" has saved
something durable; a user who saves a Tantivy query has saved something whose
behaviour is the index's, not ours. Keeping them apart keeps that difference
visible.

## Alternatives considered

**A `search` pseudo-condition on a virtual field.** Rejected: it makes the
tree able to express multi-search requests the server refuses, puts an opaque
foreign grammar inside a language whose value is that it has none, and gains
only the ability to say "everything is a FilterSet".

**Forcing search into `title__icontains`.** Rejected outright. It is not the
same query — different tokenisation, no ranking, no phrase handling — and
silently substituting a similar-looking one is precisely the kind of
approximation this project refuses everywhere else.

**Dropping the mode and always using `title_search` (M3's behaviour).**
Rejected: it made "search" mean something narrower than every user would
assume, without saying so anywhere the user could see.
