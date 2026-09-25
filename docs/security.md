# MVP security and accessibility review

PaperWrench 0.1.0 is a single-operator tool. Anyone who can reach its API can
exercise the configured Paperless credential and read local history. It does
not implement login, roles, multi-tenancy or a public internet security boundary.
For LAN installation, restrict the published HTTP port to trusted devices with
the host firewall or a private VLAN, and use a dedicated least-privilege Paperless
token. A domain, TLS and reverse proxy are optional for this local deployment.
If publishing beyond the trusted LAN, protect every route with authenticated TLS
at the reverse proxy; retain firewall restrictions.

Origin checks are a browser safeguard, not authentication: non-browser clients
can omit Origin. Direct LAN requests use their actual HTTP Host (IP:port or
localhost:port) for same-origin checks. A hostile Host/DNS environment requires
proxy host allowlisting.

Forward the original Host and scheme through a trusted proxy. Trust forwarded
headers only from that proxy's addresses; keep the backend unreachable directly.
Leave `PAPERWRENCH_CORS_ORIGINS` empty in production. M13 compares the complete
origin (scheme plus host/port), rejecting null, cross-scheme and malformed origins.
Do not loosen the allowlist to work around a misconfigured TLS proxy.

Existing regressions cover secret redaction, credential-free responses/history,
closed mutation payloads, typed custom-field merges, permission rejection,
provenance exclusion and refusal to restore later changed values. There is no
automatic mutation retry. The Paperless GET/PATCH race remains: pause external
writers, particularly for replacement-style custom-field writes. Readback cannot
recover overwritten external data or prove authorship after a lost response.

The container runs non-root with a read-only root filesystem in Compose. Keep
`/data` and backups private; preview expiry is logical deletion, not secure erasure.
See [deployment.md](deployment.md) for consistent SQLite snapshots and restoration.

## Dependency review (2026-09-25)

`npm audit` found high-severity development-tool advisories in js-yaml and its
Redocly consumer. Compatible lockfile updates remove those findings. Remaining
moderate advisories are recorded here rather than hidden with audit overrides:

- [GHSA-82fw-gwwq-j7x9](https://github.com/advisories/GHSA-82fw-gwwq-j7x9): Vitest
  mocker file-read issue. Vitest runs in CI/local development, is not in the
  production wheel, and must not serve untrusted network clients. Upgrade to
  the patched major test-tool version is deferred; no exposed test server is deployed.
- [GHSA-wrjc-x8rr-h8h6](https://github.com/advisories/GHSA-wrjc-x8rr-h8h6) and
  [GHSA-337j-9hxr-rhxg](https://github.com/advisories/GHSA-337j-9hxr-rhxg): Router
  redirect/SSR paths. This app renders a client-only SPA (no SSR hydration) and
  constructs internal links from fixed route prefixes, numeric IDs and encoded
  query data, never user-supplied destination URLs. This source review finds no
  affected input path; it is not a general claim that Router 6 is patched.
  A future arbitrary-link/SSR feature must first upgrade Router.

The Python environment/image audits identified old pip and setuptools tooling;
the runtime build upgrades pip to at least 26.2 and setuptools to at least 83.0.0.
The installed image is audited
separately in the M13 evidence report. Dependency audits are point-in-time checks,
not a penetration test or a complete OS-image vulnerability assessment.

## Accessibility scope

The compiled Chromium journey uses real controls, keyboard Enter/Space activation
for transformation navigation/acknowledgement, and axe WCAG 2 A/AA and 2.1 AA
checks through Explorer, Schemas, Quality, Transformation, preview and History.
It covers connection/error recovery and deep-route reload. M13 fixes a nested
main landmark, a missing search-input label and connection badge contrast.
Automated checks do not establish complete WCAG conformance. Screen-reader,
mobile and multi-browser manual audits remain NOT_RUN; see the evidence report.
