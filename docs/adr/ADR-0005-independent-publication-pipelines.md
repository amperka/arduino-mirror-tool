# ADR-0005: Publish package and library indexes through independent pipelines

- **Status:** Accepted
- **Date:** 2026-08-13
- **Supersedes:**
- **Superseded by:**

## Context

Arduino Mirror publishes two different Arduino index formats. Packages and
libraries use independent selection policies and archive sets, but they share
one S3-compatible target. A combined flow would make an error in one index
block or partially publish the other. A shared archive prefix could also make
stale-object deletion unsafe.

The alternatives are one combined CLI and reconciliation flow, or separate
flows with family-specific storage ownership. The project also needs a public
UX that makes each operation visible to an operator.

## Decision

The CLI exposes separate `packages` and `libraries` commands. Each command
runs an independent publication pipeline: fetch one source index, apply its
family-specific selection policy, create a publication plan, reconcile only
that family's archives, and publish only that family's index.

Package archives belong under `packages/`; library archives belong under
`libraries/`. The root objects `package_index.json` and `library_index.json` are
published independently. A pipeline uploads and verifies required archives
first, replaces its index only after those archives succeed, and deletes stale
objects from its own prefix only after the replacement. The S3 target replaces
an index with one object write; the local target uses an atomic rename. There is
no transaction across objects or families. A failure before index replacement
leaves that family's prior index intact and does not modify the other family's
index or archives.

The deployment environment serializes publications of the same family. Package
and library publications may run concurrently because their indexes and archive
prefixes are disjoint.

The layer boundaries are: `domain` owns immutable publication plans, index
family vocabulary, selection-policy ports, and storage/source port contracts;
`application` owns the package and library publication use cases; `infra`
implements HTTP source and S3-compatible target adapters; `entrypoints` owns
CLI parsing and dependency composition. The client bootstrap is outside these
pipelines and follows their implementation.

## Alternatives Considered

### One combined command and archive namespace

Rejected. It obscures the operator action, couples failures, and makes it
unsafe to reconcile or delete artifacts for one index independently.

### A shared generic policy for both index formats

Rejected. Boards use Arduino-specific package and architecture selection;
libraries use exact-name SemVer selection with a fallback. One policy would
hide materially different rules.

### Delete stale archives before index publication

Rejected. The previously published index can still reference those archives
until its replacement completes.

### Concurrent publications of the same family

Rejected. A concurrent run can reconcile stale keys against an obsolete target
inventory and later replace an index after another run's cleanup. An
adapter-level distributed lock adds target-specific coordination, so the
deployment environment serializes each family instead.

## Consequences

- **Positive:** Operators can mirror packages and libraries independently.
- **Positive:** Archive ownership and stale cleanup are bounded by family.
- **Positive:** A publication never exposes an index before its selected
  archives are available.
- **Positive:** A failed family cannot change the other family's index or
  archives.
- **Negative / trade-offs:** Shared configuration and adapter wiring are used
  by two commands rather than a single combined flow; deployment must
  serialize runs of the same family.
- **Accepted risks:** A failed cleanup leaves stale archives. They remain
  unreachable from the new index and are removed by a later successful run.
  A completed archive can remain unpublished by an index until a later run.
