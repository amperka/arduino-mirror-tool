# ADR-0001: Adopt 5-layer hexagonal architecture with import-linter enforcement

- **Status:** Accepted
- **Date:** 2026-08-12
- **Supersedes:**
- **Superseded by:**

## Context

Arduino Mirror fetches public indexes and archives, transforms selected index
entries, and publishes files to storage. Without explicit boundaries, CLI,
HTTP, S3, filtering, and domain rules will become coupled and hard to test.

The project needs five mandatory layers and a check that preserves dependency
direction. The alternatives are convention-only layering, custom forbidden-import
rules, and an ordered `import-linter` layers contract.

## Decision

The project uses five layers in this order: `entrypoints → infra → application
→ domain → shared`. Dependencies only point to the right. `import-linter`
enforces this order with its `layers` contract.

`entrypoints` contains CLI and composition roots. `infra` contains HTTP and
storage adapters. `application` contains use cases. `domain` contains mirror
rules and port contracts. `shared` contains only utilities consumed by two or
more layers; it contains no business rules or I/O.

## Alternatives Considered

### Convention-only layering

Rejected. It does not prevent an adapter or entry point from leaking into a
use case.

### Custom forbidden-import rules

Rejected. A separate rule for every new module is maintenance work and less
clear than an ordered layer list.

## Consequences

- **Positive:** Pure mirror rules and use cases are isolated from HTTP and S3.
- **Positive:** The import check detects upward dependencies in CI.
- **Negative / trade-offs:** Facade-only imports within a layer remain a review
  rule because the layers contract does not enforce them.
- **Accepted risks:** A utility can be misplaced in `shared`; reviewers must
  apply its narrow definition.
