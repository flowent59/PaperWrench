# ADR-0006: SQLite durable job engine, single instance

## Status

Accepted (M0).

M8 implementation: [ADR-0014](0014-durable-jobs-and-write-provenance.md) supersedes
SSE with polling of durable paginated History and specifies recovery/lock-loss
behavior. No unattended automatic resume is supported.

## Context

Bulk jobs are long-running: several hundred documents at concurrency 4 takes
minutes. They must survive being watched, they must report progress, and above
all they must survive the process dying halfway through, because a bulk write
that has completed 137 of 300 documents and left no record of which 137 is
precisely the disaster this tool is supposed to prevent.

The conventional answer is Celery with Redis, or RQ, or Arq. All of them mean a
second service in the deployment. For an application whose entire value
proposition to self-hosters is "one container, two environment variables"
(ADR-0001), adding a broker is a serious cost: another container to run, another
process to supervise, another thing to back up, another failure mode, and a
larger attack surface - for a workload that is one user running one job at a
time on a home server.

The workload also does not look like what a broker is for. There is no fan-out
across machines, no competing consumers, no scheduling, no retry storms. It is
a single operator, a handful of jobs, and a strict requirement that nothing be
written twice.

The real requirement is not distributed task execution. It is **durability of
per-document state** and **exactly-once writes across a crash**. A broker does
not give us either of those for free; the durable record does.

## Decision

The job engine runs in-process, inside the FastAPI application, using asyncio.
There is no Redis, no Celery, no external broker, no separate worker container.

**Durability lives in SQLite.** Every job is a `Job` row and every document
touched is a `JobOperation` row, committed as each operation completes rather
than batched at the end. The database is opened in WAL mode with
`busy_timeout=5000`, `foreign_keys=ON` and `synchronous=NORMAL` - WAL because a
reader (the progress stream) must not block the writer (the job).

The state that must survive a crash is on disk before the next document is
attempted. Concretely, if the process is killed at document 137, the database
says so.

**Exactly-once via a uniqueness constraint.** `JobOperation` carries a unique
constraint on `(job_id, document_id, field_kind, field_key)`. Resumption is
therefore idempotent at the storage layer, not merely by convention: a resumed
job cannot record - and therefore cannot repeat - a write it already performed.
A crash between the PATCH and the local commit leaves one operation whose
outcome is unknown, and resumption resolves it by re-reading the document
rather than guessing.

**Interruption is a first-class status.** A job that was `RUNNING` when the
process died is marked `INTERRUPTED` at the next startup. `INTERRUPTED` is
deliberately *not* terminal: it is the resumable state. A job is never silently
resumed on boot - the operator decides, because an unattended automatic resume
of a destructive write is not a decision software should make.

**Single instance, enforced not assumed.** A `runtime_lock` table holds exactly
one row, guarded by a `CHECK (id = 1)` constraint. At startup the process
acquires the lock with its instance id; a second process is refused with
`SINGLE_INSTANCE_VIOLATION` and exits. A heartbeat refreshes the lock every 15
seconds and a lock that has not been refreshed for 60 seconds is considered
stale and can be taken over - which is what makes recovery after a hard kill
possible without manual intervention. `PAPERWRENCH_FORCE_LOCK` exists as a
documented escape hatch for the case where an operator knows better.

This is why the container runs a single Uvicorn worker: a second worker would
be refused at boot, loudly, rather than corrupting job state quietly.

**Migrations run at startup**, before the lock is acquired - the lock table is
itself created by a migration. Concurrent first boots are serialised by
SQLite's own write lock plus `busy_timeout`; the loser finds the schema already
at head and applies nothing. Alembic revisions ship inside the Python package
so an installed wheel is self-contained.

**Progress is streamed over SSE.** Job progress is one-directional
server-to-client, which is exactly what Server-Sent Events are for. WebSocket
would add a bidirectional protocol, its own reconnection logic and proxy
buffering issues for no gain.

## Consequences

Deployment stays a single container with a single writable volume. Backing up
PaperWrench means backing up one SQLite file - and that file is what makes
rollback possible, which the README says explicitly.

Horizontal scaling is impossible, by design and by enforcement. This is
acceptable: the tool is a single-operator power tool, not a service.

The application process becomes stateful in a way that matters. A restart
during a job interrupts it, and the operator must resume it deliberately. That
is the correct trade: an interrupted job with a precise per-document record is
strictly better than an untraceable one.

SQLite write throughput is not a concern at this scale - a few hundred small
commits per job, dwarfed by the HTTP latency of the writes themselves.

If a genuine multi-user, multi-instance requirement ever appears, this decision
is the one to revisit, and the durable `JobOperation` record is what would make
that migration tractable.

## Alternatives considered

**Celery + Redis.** Rejected: a second service and a second container for a
single-user workload, and it still would not provide the per-document durable
record we need to build ourselves anyway.

**In-memory jobs with periodic checkpoints.** Rejected: the window between
checkpoints is exactly the window where an unrecorded write can happen.

**Background threads instead of asyncio.** Rejected: the workload is HTTP-bound
and the client is async, so threads add synchronisation problems without adding
throughput.

**Automatic resume of interrupted jobs at startup.** Rejected: a crash loop
would repeatedly resume a destructive write unattended.

**File-based locking (flock) instead of a database row.** Rejected: it does not
survive containerisation cleanly across bind-mount and volume configurations,
and it cannot carry the instance id and heartbeat needed for stale-lock
takeover.
