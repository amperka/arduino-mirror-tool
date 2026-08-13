# ADR-0003: Place configuration in the layer that owns its concern

- **Status:** Accepted
- **Date:** 2026-08-12
- **Supersedes:**
- **Superseded by:**

## Context

Arduino Mirror needs settings for source indexes, selection, mirror URLs,
storage, and client bootstrap. A residual top-level configuration package would
let every layer depend on parsing and delivery details, bypassing the layer
contract.

The alternatives are one global configuration aggregate, configuration types
in their owning layers, and parsing configuration inside domain models.

## Decision

There is no residual `config` layer. A configuration type lives in the layer
that owns its concern: domain settings describe mirror rules; infrastructure
settings describe HTTP and storage adapters; entrypoints own environment,
command-line, and configuration-file parsing plus the process composition
aggregate.

Application use cases receive only the domain settings and ports they need.
They do not receive the entrypoint aggregate or read process configuration.

## Alternatives Considered

### One global configuration aggregate

Rejected. Passing it into use cases imports entrypoint concerns into the
application layer.

### Parse configuration in domain models

Rejected. Domain models must not depend on command-line, environment, or file
formats.

## Consequences

- **Positive:** Each setting has a clear owner and dependency direction.
- **Positive:** Use-case dependencies remain explicit and mockable.
- **Negative / trade-offs:** Settings produced together can be declared in
  separate modules.
- **Accepted risks:** A setting can be placed in the wrong layer; review and
  import-linter provide the guardrails.
