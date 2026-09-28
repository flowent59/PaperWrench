# ADR-0018: interface locale is a per-user presentation preference

## Status

Accepted.

## Context

PaperWrench needs a French and English interface before authentication as well
as after it. A browser language is useful on a first visit, but browser-local
storage cannot reliably represent a preference shared by the same Paperless
user across browsers. Paperless values, including user-defined field names and
select labels, must not be translated or rewritten.

## Decision

The frontend normalizes browser languages to the supported `en` and `fr`
locales, falling back to English. It sends that locale with the first successful
login. PaperWrench then stores the preference against the authenticated numeric
Paperless user ID in `user_preferences`; later logins return the stored value and
do not overwrite it with another browser's language.

An authenticated language change is applied immediately and persisted through
the CSRF-protected preferences endpoint. Before login, the selector changes only
the in-memory fallback used for that login. No token or locale is stored in the
browser.

Dates, numbers and monetary amounts are formatted for display with `Intl`.
Their API values remain unchanged. Stable PaperWrench capability identifiers
select translated labels, while Paperless-owned names and option labels remain
verbatim.

## Consequences

A user's interface language follows their Paperless identity across sessions
and browsers. Deleting the PaperWrench database also deletes this non-secret
preference. Adding another locale requires a complete typed catalogue and an
allowed database value plus migration. Backend error payload localization is a
separate concern tracked by issue #44.

## Alternatives considered

Browser-local persistence was rejected because it is device-specific and can
leak a previous user's choice on a shared browser. Storing the preference in
Paperless was rejected because UI settings are PaperWrench-owned and Paperless
is an integration boundary, not PaperWrench's configuration store.
