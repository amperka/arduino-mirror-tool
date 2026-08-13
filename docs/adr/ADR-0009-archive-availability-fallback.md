# ADR-0009: Re-plan from older releases after an archive becomes unavailable

- **Status:** Accepted
- **Date:** 2026-08-13
- **Supersedes:**
- **Superseded by:**

## Context

A retry budget distinguishes transient errors from a persistent archive failure,
but cannot make a missing, corrupt, or unuploadable archive available. Aborting
at that point discards a long-running family's otherwise valid work. Publishing
an index that still names the failed archive is unsafe because clients cannot
install it.

The alternatives are: (a) abort the family and retain the old index; (b) omit
only the failed release; (c) select the next older release for that release's
logical library or platform; or (d) persist a durable availability cache across
runs.

## Decision

The application re-plans the already fetched source index after a target
identifies one archive as unavailable. Selection receives the set of excluded
family archive keys and chooses the newest eligible older origin candidate for
each library and configured package platform. An unavailable tool archive also
makes platforms requiring that exact tool version ineligible. The target
reconciles every re-planned result before publishing it.

Local and S3 targets translate archive download, integrity, and per-archive
upload failures into one domain `ArchiveUnavailableError` carrying only the
logical archive key. Before translation they re-check cooperative cancellation,
so a requested stop continues to propagate without fallback. Source-index
retrieval, target reconciliation, index replacement, and cleanup remain
family-level failures: they cannot safely identify a replacement release. If
exclusions leave no origin archives, the application preserves the existing
index and archive set rather than publishing an empty downgrade.

## Alternatives Considered

### Omit the failed release

Rejected. A client loses an available historical release even when the source
index contains an older compatible release.

### Abort after retries

Rejected. It retains consistency but makes one permanently unavailable archive
block all new available releases.

### Durable availability cache

Rejected. It adds storage, expiry, and consistency behavior beyond this static
mirror. Each run's source index and target reconciliation already provide the
authoritative current state.

## Consequences

- **Positive:** One unavailable archive no longer prevents publication of other
  available releases, and selected clients receive the newest viable version.
- **Positive:** Index-before-archive safety from ADR-0005 remains intact.
- **Negative / trade-offs:** A failure may require repeated selection and target
  reconciliation and can leave an attempted archive temporarily orphaned until
  the final successful cleanup or a later run.
- **Accepted risks:** An unavailable archive key shared by multiple releases
  excludes all of them. They name the same mirror object, so retaining any of
  them would again expose an unavailable archive.
