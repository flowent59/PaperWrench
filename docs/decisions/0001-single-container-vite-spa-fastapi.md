# ADR-0001: Vite SPA + FastAPI in a single container

## Status

Accepted (M0).

## Context

PaperWrench targets self-hosters who already run Paperless-ngx, usually with
Docker Compose on a home server or a small VPS. That audience is technical but
their patience for infrastructure is finite: an application that requires a
reverse proxy, two containers, a shared network and a CORS configuration before
it shows a single screen will not be adopted, and every one of those moving
parts is a place where a support issue can be born.

At the same time the UI itself is not simple. Bulk transformation previews mean
large virtualised tables with per-row diffs, live job progress, and a lot of
client-side state. That rules out server-rendered templates and argues for a
real single-page application with React, TanStack Table and TanStack Query.

Next.js was the obvious alternative for the frontend, and we rejected it: it
brings a Node runtime, a second process to supervise, its own routing and data
fetching conventions, and a server-side rendering model that buys us nothing
here. There is no SEO requirement, no anonymous traffic, and no first-paint
budget worth a second runtime in the production image.

The remaining question was how the SPA and the API reach the user.

## Decision

The frontend is a Vite + React + TypeScript SPA. It is built to static assets
at image build time and copied into the Python package at
`paperwrench/static`. FastAPI serves those assets and the JSON API from the
same origin, in one container, on one port.

Concretely:

- `vite build` outputs into `backend/src/paperwrench/static`, so the wheel
  ships the SPA and an installed distribution can serve the UI with no extra
  files.
- FastAPI mounts `/assets` as static files and adds a catch-all route that
  returns `index.html`, which is what makes client-side routing work on a hard
  refresh.
- The catch-all explicitly refuses any path under `/api`, which must 404 with
  the JSON error envelope. Returning the HTML shell with a 200 to a client
  expecting JSON produces failures that are extremely hard to diagnose.
- CORS is disabled by default. `PAPERWRENCH_CORS_ORIGINS` exists only for
  split-origin development, where Vite runs on :5173 and proxies `/api` to
  :8000.
- The API client uses relative URLs exclusively. There is no base URL to
  configure, and therefore no way to misconfigure it.

The container runs a single Uvicorn worker as a non-root user, with `/data` as
the only writable location.

## Consequences

Deployment is `docker compose up` with two required environment variables. No
reverse proxy, no CORS, no second service, no inter-container networking.

Same-origin removes an entire class of security problems: no preflight, no
credential-bearing cross-origin requests, no origin allowlist to get wrong.
Cross-origin state-changing requests are additionally rejected by an origin
guard middleware, so the property holds even if someone enables CORS carelessly
later.

The single worker is not a limitation we work around, it is a constraint we
depend on: the job engine runs in-process and holds a single-instance runtime
lock (ADR-0006). Scaling horizontally is explicitly out of scope.

The costs we accept: the frontend must be rebuilt to change the UI, so no
hot-reload in production (irrelevant); a Node toolchain is required to build
but not to run; and the Python wheel is larger because it embeds static assets.

Development is the one place where the topology differs from production, since
Vite serves the SPA and proxies the API. Because the catch-all and error
envelope behaviour differ between the two modes, both are covered by tests
(`test_spa_serving.py`) rather than assumed.

## Alternatives considered

**Next.js frontend, separate container.** Rejected: a second runtime and a
second process for capabilities we do not need, plus CORS or a proxy to
configure.

**Server-rendered Jinja templates.** Rejected: the preview and job monitoring
screens are genuinely interactive, and rebuilding TanStack Table in templates
plus htmx would be more work for a worse result.

**Static assets served by a separate nginx.** Rejected: it is the same
container count problem, and it puts routing rules - especially the SPA
fallback - in a file that is not covered by our tests.
