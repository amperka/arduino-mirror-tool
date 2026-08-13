# ADR-0006: Cancel publication cooperatively at operation boundaries

- **Status:** Accepted
- **Date:** 2026-08-13
- **Supersedes:**
- **Superseded by:**

## Context

A package or library publication has ordered mutations: archive upload, index
replacement, then stale cleanup. Default SIGINT and SIGTERM handling can stop
Python at any point. That can expose a new index after an operator requested a
stop, or begin destructive cleanup after the stop request.

The alternatives are default immediate signal termination, a cooperative signal
handler that records cancellation and lets the pipeline stop at safe
boundaries, or moving publication to a durable worker with resumable jobs.

## Decision

The CLI installs temporary SIGINT and SIGTERM handlers. The first request is
recorded. During a streamed archive download, that handler immediately raises
the cancellation so Requests unwinds; the temporary archive stream closes in
exception cleanup. During upload, index replacement, and stale cleanup, the
first handler only records cancellation. The publication checks that record
before each later external operation and after cleanup, then returns `128 +
signal number`. A recorded cancellation does not start a later index replacement
or stale cleanup; it does not roll back an operation already in progress.

A second SIGINT or SIGTERM restores the default handler for that signal and
redelivers it immediately. This is an operator emergency exit; it does not
preserve publication consistency guarantees.

## Alternatives Considered

### Default immediate signal termination

Rejected. It offers no defined boundary for index replacement or stale cleanup,
and it gives operators no controlled result from the CLI.

### Durable worker with resumable jobs

Rejected. A job store and recovery protocol exceed the static mirror's current
operational scope. Archive-first publication and a later scheduled run provide
the required recovery behavior.

### Ignore subsequent interruption signals

Rejected. An operator needs an emergency exit when an in-progress target
operation does not return in acceptable time.

## Consequences

- **Positive:** An interruption prevents new download, upload, index, and stale
  deletion operations from starting after it is observed.
- **Positive:** Operators receive a conventional nonzero exit status after the
  first cancellation reaches a boundary.
- **Negative / trade-offs:** Upload, index replacement, and stale cleanup
  already in progress can finish before the next cancellation boundary.
- **Accepted risks:** A completed archive can remain uploaded without a new
  index. A first cancellation during index replacement can leave the new index
  published. A second signal can terminate an in-progress operation and leave
  partially completed target state.
