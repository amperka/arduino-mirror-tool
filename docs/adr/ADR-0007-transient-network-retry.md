# ADR-0007: Retry transient network failures in publication adapters

- **Status:** Accepted
- **Date:** 2026-08-13
- **Supersedes:**
- **Superseded by:**

## Context

Index retrieval, origin archive download, and S3-compatible publication all
depend on the network. Today every adapter catches network errors only to emit
a trace record, then re-raises; the CLI catches only cooperative cancellation
(ADR-0006), so any transient failure — a refused connection, a read timeout, a
5xx, or a throttle — aborts the whole scheduled run. The mirror recovers only
on the next scheduled invocation, which is wasteful and fragile for flaky
upstream or storage endpoints.

The viable options are: (a) a shared retry utility in `shared`; (b) one
transport-agnostic retry engine in `infra` with per-library transient-error
classifiers; (c) bespoke retry loops inside each adapter; (d) a durable worker
with resumable jobs.

## Decision

Publication adapters recover from transient network failures through one
transport-agnostic retry engine in `infra/retry.py` (`RetryPolicy` +
`retry_call`) plus per-library classifiers (`is_transient_http`,
`is_transient_s3`). The engine is predicate-driven: it retries only when the
caller-supplied classifier returns `True`, applies full-jitter exponential
backoff capped at a configured maximum, and never retries cooperative
cancellation or non-transient exceptions. Each adapter wraps its own network
call (index fetch, archive download, S3 list/put/remove) so the application
layer stays free of transport detail.

Classification uses a denylist posture rather than an explicit transient
enumeration. For HTTP, `HTTPError` responses are classified by status (5xx and
429 transient; other 4xx permanent) and every other `requests.RequestException`
retries except a small denylist of definitively-permanent malformed-request
errors (URL/schema/header, redirect loops, consumed streams). For S3, `S3Error`
is classified by code or status and every other `urllib3` error retries except
misconfiguration errors. This is deliberate: the streaming transport surfaces
several transient failure modes that do not share one enumerable base type — a
connection broken mid-transfer raises `requests.exceptions.ChunkedEncodingError`,
which is a direct `RequestException`, not a `ConnectionError` — so enumerating
transient types leaves gaps that crash the workflow on the first miss. Not
retried regardless of transport: an invalid index payload, an origin archive
integrity mismatch, and any cooperative-cancellation exception. The
inter-attempt backoff sleeps in bounded chunks that re-check the cancellation
port, preserving ADR-0006 latency. Operators tune the schedule through
`--retry-attempts` (default 10) and `--retry-base-delay` (default 1.0s).

## Alternatives Considered

### Shared retry utility

Rejected. `shared` holds utilities consumed by two or more layers with no I/O
and no business rules (ADR-0001); retry orchestrates repeated invocations and
sleeps, and its classifiers must reason about transport exceptions, so it is an
infrastructure concern, not a shared kernel utility.

### Bespoke retry per adapter

Rejected. Three adapters would diverge on the transient taxonomy and backoff
schedule, duplicating the same failure-handling logic and inviting
inconsistency.

### Durable worker with resumable jobs

Rejected for the same reason as ADR-0006: a job store and recovery protocol
exceed the static mirror's operational scope. Archive-first publication and a
later scheduled run already provide recovery.

## Consequences

- **Positive:** A transient upstream or storage failure no longer aborts a
  scheduled run; bounded retries recover it in place.
- **Positive:** One transient taxonomy is centralized and unit-testable, and the
  predicate-driven engine stays testable without a network.
- **Negative / trade-offs:** A persistent failure now adds bounded latency (the
  sum of the backoff schedule) before it propagates, instead of failing fast.
- **Accepted risks:** A permanent error misclassified as transient wastes
  retries until exhaustion. Mitigated by a small, explicit permanent-error
  denylist and unit tests for every classifier branch.
