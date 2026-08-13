# ADR-0004: Use stdlib module-local structured logging

- **Status:** Accepted
- **Date:** 2026-08-12
- **Supersedes:**
- **Superseded by:**

## Context

A mirror run can fetch, select, upload, delete stale objects, and publish
indexes. Operators must be able to identify the producing module and inspect
important execution boundaries without a project-specific logging API.

The alternatives are a custom logger subclass, a trace wrapper, a sentinel
inside logging extras, and direct use of stdlib module-local loggers.

## Decision

Each module binds `logger = logging.getLogger(__name__)`. Important block
boundaries emit `logger.debug("BLOCK", extra={...})` with flat, non-secret
structured fields. One formatter renders in-package DEBUG records with extra
fields as traces and renders other records as normal user-facing logs.

## Alternatives Considered

### Custom logger or trace wrapper

Rejected. The stdlib `debug` method and `extra` argument already provide the
required behavior.

### Sentinel key in the extra mapping

Rejected. It reserves an application key and duplicates information observable
from logger provenance, level, and extra fields.

## Consequences

- **Positive:** A logger name maps directly to the source module.
- **Positive:** Tests can assert block markers and structured fields.
- **Negative / trade-offs:** Callers must avoid keys reserved by `LogRecord`.
- **Accepted risks:** Excessive trace records can add noise; trace only
  meaningful block boundaries and redact secrets.
