# ADR-0002: Use immutable stdlib domain models and Protocol ports

- **Status:** Accepted
- **Date:** 2026-08-12
- **Supersedes:**
- **Superseded by:**

## Context

The mirror must apply selection and publication rules identically with mock and
production adapters. Mutable records or adapter-owned models would make the
rules difficult to test and could hide changes to a planned mirror release.

The alternatives are third-party model libraries or stdlib dataclasses,
abstract base classes or structural ports, and mutable or immutable domain
objects.

## Decision

The domain uses stdlib `@dataclass(frozen=True)` value objects and entities.
Domain transitions return replacement values rather than mutate existing
objects. Ports use `typing.Protocol`; adapters satisfy the required behavior
without inheriting from domain classes.

Domain methods hold rules that belong to one model. Application use cases
coordinate rules across models and call ports.

## Alternatives Considered

### A third-party model library

Rejected. Frozen dataclasses provide the required immutability without an
additional runtime dependency.

### Abstract base classes for ports

Rejected. Inheritance would couple infrastructure adapters to domain types.

### Mutable domain objects

Rejected. Explicit replacement values make a planned release safe to inspect,
test, and pass between use cases.

## Consequences

- **Positive:** Selection and release rules are deterministic unit-test targets.
- **Positive:** Test doubles and production adapters share one structural port
  contract.
- **Negative / trade-offs:** Transitions allocate replacement values.
- **Accepted risks:** Protocol conformance is checked by static analysis and
  focused adapter tests rather than at every definition.
