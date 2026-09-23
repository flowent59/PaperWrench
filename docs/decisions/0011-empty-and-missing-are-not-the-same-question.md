# ADR-0011: `is missing`, `is null` and `is empty` are three different questions

## Status

Accepted (M4). Semantics VERIFIED_LIVE against Paperless-ngx 3.1.2 before
being frozen.

## Context

M1 and M2 established that a custom field value has states most code
collapses:

```
ABSENT      the field is not attached to the document at all
NULL        the field is attached, its value is explicitly null
""          the field is attached and holds the empty string
0 / false   real values that are falsy in almost every language
```

`TypedCustomFieldValue` already keeps these apart on the read path. The Filter
Engine has to keep them apart on the *query* path too, and here the stakes are
higher: a filter is the definition of a transformation's blast radius (M6+).
"Fill in the amount where it is missing" and "fill in the amount where it is
zero" are different operations on different documents, and a single "is empty"
operator would silently merge them.

The M4 brief proposed a definition to be checked rather than assumed:

```
is_empty  ==  OR(isnull = true, exact = "")     # but not exists = false
```

### What Paperless actually does

VERIFIED_SOURCE (3.1.2, `documents/filters.py`, `CustomFieldQueryParser._parse_atom`)
and VERIFIED_LIVE (`TestFilterEngineEmptyMissingLive`). The parser builds a
`Count` annotation over the document's custom field instances:

`exists` counts instances of the field, whatever their value:

```python
annotation = Count("custom_fields", filter=Q(custom_fields__field=custom_field))
query_op = "gt" if value else "exact"      # exists=true -> count > 0
```

Every other operator counts instances that *also* satisfy the value condition:

```python
field_filter = has_field & Q(**{f"custom_fields__{value_field}__{op}": value})
annotation = Count("custom_fields", filter=field_filter)
query = Q(**{f"{annotation_name}__gt": 0})
```

The consequence is the one that makes the whole distinction work:
**`isnull=true` is `has_field AND value IS NULL`**. It cannot match a document
that does not carry the field, because there is no instance to count. So
`exists` and `isnull` are not two spellings of the same idea — one asks about
the instance, the other about its value, and only the second requires the
instance to be there.

`exact: ""` is accepted for text-shaped fields specifically: the parser sets
`allow_blank = True` on `CharField`-derived value fields (working around
upstream issue #7361). On a Monetary, Date, Integer, Boolean or Select field
the same expression is a 400.

## Decision

Five operators, each mapping to one server-side expression, none of them
overlapping by accident:

| Operator | Compiles to | Matches |
| --- | --- | --- |
| `is_missing` | `[id, "exists", false]` | ABSENT only |
| `is_present` | `[id, "exists", true]` | NULL, `""`, `0`, `false`, any value |
| `is_null` | `[id, "isnull", true]` | NULL only — **never** ABSENT |
| `has_value` | `[id, "isnull", false]` | `""`, `0`, `false`, any real value |
| `is_empty` | `["OR", [[id,"isnull",true], [id,"exact",""]]]` | NULL or `""` |

`is_empty` is **defined as proposed and confirmed live**: null or the empty
string, and deliberately *not* `exists=false`. A document that never had the
field is a different thing from one that has it and left it blank, and the
whole point of having five operators is that the user says which they mean.

`is_empty` is offered only for Text, Long text and URL fields — the three
whose Paperless column can genuinely hold `""`. On a Monetary field it is not
in the operator list at all, because `exact: ""` there is a guaranteed 400 and
offering an operator that always errors is worse than not having it.

Core fields get a smaller set, because they have fewer states. A core
`correspondent` is either set or not: there is no "attached but null", so
`is_missing`/`is_present` map to `correspondent__isnull` and `is_null` and
`is_empty` are not offered. A core `title` is not nullable at all and gets
none of them.

`0` and `false` are never treated as empty by anything here. They are matched
by `is_present` and by `has_value`, and excluded by `is_missing`, `is_null`
and `is_empty` — which is the behaviour the Golden Dataset's deliberate
`EUR0.00` rows exist to keep honest.

## Consequences

The operator list is longer than a filter UI usually has, and two of the
entries (`is_present`, `has_value`) differ only in whether they include an
explicit null. That is a real cost in interface complexity, and it is paid on
purpose: the capabilities endpoint carries a one-sentence definition of each,
so the UI can explain the difference where the user is choosing between them
rather than in documentation they will not read.

A filter written as "amount is empty" will not match documents that never had
an amount. Users who want both must say so — `is_missing OR is_empty`, which
is a supported custom-field OR group and compiles fine.

If Paperless ever changes `isnull` to match absent instances, this ADR is
wrong and the live test `test_absent_null_and_empty_string_are_three_distinct_states`
fails, which is the intended way to find out.

## Alternatives considered

**One `is_empty` covering ABSENT, NULL and `""`.** Rejected: it is the
conflation M1 and M2 spent their effort avoiding, and it would make "fill in
the missing amounts" silently overwrite deliberate zeros and blanks the first
time someone used it for a transformation.

**Only `is_missing` and `has_value`, dropping the middle.** Rejected as
under-powered for the data-quality work (M11) this engine is meant to
underpin: "the field is attached but null" is exactly the kind of state a
quality report needs to be able to name.

**Deriving emptiness in PaperWrench after fetching.** Rejected — that is a
local fallback, which ADR-0007 forbids outright.
