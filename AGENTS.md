# AGENTS.md

<!-- #region SECTION_Dev_Rules -->

## Development Rules

- Follow the OpenSpec, YAGNI, DRY, KISS, SOLID principles.
- Top-down approach: Start with requirements and a bird's-eye view plan. Define
  module contracts with purpose and boundaries before any code. Specify
  contracts for public classes, methods, and functions. Create stubs. Only then
  write code inside the contracted regions.
- Public interface stability: CLI commands, public API, INI config format, DB
  schema. Schema changes MUST include migrations.
- To add new dependencies: FIRST declare them in an OpenSpec change proposal
  with rationale.
- Prefer minimal changes over broad refactors.
- Do not add compatibility layers without a concrete need.
- If a commit is required, format the message according to Conventional Commits.
- Every module should export only the public API via `__all__`.

<!-- #endregion SECTION_Dev_Rules -->

<!-- #region SECTION_OpenSpec_Rule -->

## OpenSpec Rule

Any code, configuration, CLI, workflow, engine contract, cloud behavior, DB
schema, or operational behavior change must consult `openspec/specs/` before
implementation and update the relevant OpenSpec requirements in the same change.

Use `openspec/changes/` proposals for behavior-changing work before
implementation.

If the user requests changes outside the OpenSpec workflow, offer to use
OpenSpec via `/opsx-propose`, but do not block or refuse the requested work.

<!-- #endregion SECTION_OpenSpec_Rule -->

<!-- #region SECTION_ADR_Rule -->

## Architectural Decisions Rule

Architectural trade-offs (module boundaries, data ownership, protocols,
tech/library selection, security model, failure/error handling,
identity/lifecycle design, dependency direction) must consult `docs/adr/`
before implementation.

If a change introduces a new architectural trade-off with viable alternatives,
record it as a new ADR in `docs/adr/` using `_template.md`. Sequential
numbering starts at the next free slot; no numbers are reserved. Bug fixes, file
relocations, test additions, spec maintenance, and feature work are not ADRs.

<!-- #endregion SECTION_ADR_Rule -->

<!-- #region SECTION_Verification -->

## Verification

- New code should include focused unit tests for core logic and pure behavior;
  test happy paths first, then meaningful edge cases.
- For changes touching real host and storage, also add or update integration/e2e
  tests per the relevant OpenSpec specs.
- Run tests: `uv run pytest -m unit`, `uv run pytest -m integration`, `uv run
  pytest -m e2e`.
- Static checks: `uv run zuban check`, `uv run ruff check .`, `uv run ruff
  format --check .`, `uv run lint-imports`
- Spec validation: `openspec validate --all --json` must pass after creating a
  change proposal and after any modification to `openspec/specs/`, and also
  after archiving or syncing changes.

<!-- #region SECTION_Logging -->

### Logging & Verification

Structured logs = primary observability. Block boundary log entries declare
what code assumes at that point. Runtime behavior traceable back to contract.

**Trace method.** Emit `logger.debug("BLOCK", extra={"k": v, ...})` at block
boundaries. The positional block marker is the debug message; structured fields
are the flat `extra` dict (no nested sentinel, no wrapper function). Structured
fields preferred; redact secrets. Missing trace logging on critical branches =
verification defect.

**Logger binding.** Modules bind loggers via stdlib:

```python
import logging

logger = logging.getLogger(__name__)
```

**Module-local logger names.** Modules obtain a logger via
logging.getLogger(**name**), which yields names like
`arduino_mirror.<dotted.module.path>`.

**Record contract for tests.** Trace records expose `getMessage()` and
structured fields as record attributes. Tests assert the block marker and extra
fields.

**Tests:** deterministic assertions first. Trace/log assertions when trajectory
matters. Module-local tests stay close to module. Update tests when log markers
change intentionally.

<!-- #endregion SECTION_Logging -->

<!-- #endregion SECTION_Verification -->

<!-- #region SECTION_Project -->

## Project

Static, filtered mirror of Arduino Boards Manager packages and libraries
for networks where `downloads.arduino.cc` is unreachable.

### Core Flow

1. CLI loads configuration and composes source, selection-policy, and
   publication-target adapters.
2. The source adapter fetches Boards Manager and Library Manager indexes.
3. A replaceable policy retains the selected latest package and library
   releases; the application rewrites their archive URLs for the mirror and
   builds publication plans.
4. The publication target uploads missing archives and publishes the rewritten
   indexes to the managed S3-compatible storage prefixes.
5. Scheduled GitHub Actions runs the flow; tests exercise it first with mocked
   adapters, then local and production-target boundaries.

### Structure

Hexagonal architecture:
shared <- domain (no external deps) <- application <- infra <- entrypoints.

```txt
src/arduino_mirror/
├── entrypoints/  # drivers: cli, entrypoints, public API, DI
├── infra/        # driven: storage, network adapters
├── application/  # use cases
├── domain/       # entities, ports, events, exceptions
└── shared/       # shared kernel
```

<!-- #endregion SECTION_Project -->

<!-- #region RULES_REPEATED -->

<critical_rules>
<rule>Follow OpenSpec, YAGNI, DRY, KISS, SOLID</rule>
<rule>Top-down: requirements → module contract → contracts → stubs → code</rule>
<rule>Minimal changes; NEVER compatibility layers without concrete need</rule>
<rule>Conventional Commits when committing</rule>
<rule>Consult openspec/specs, update specs; use openspec/changes for proposals</rule>
<rule>Consult docs/adr/ before architectural work; new trade-off → new ADR</rule>
<rule>Offer /opsx-propose if outside OpenSpec, NEVER block requested work</rule>
<rule>Unit tests for core logic; integration/e2e tests for external deps</rule>
<rule>Run: uv run pytest -m unit / -m integration / -m e2e</rule>
<rule>Static checks: uv run zuban check, ruff check, ruff format --check, lint-imports</rule>
<rule>openspec validate --all --json must pass after any spec changes</rule>
<rule>Structured logging: `logger.debug("BLOCK", extra={...});`,
test log records.</rule>
<rule>Hexagonal architecture; adhere to src/arduino_mirror/ structure</rule>
</critical_rules>

<!-- #endregion RULES_REPEATED -->
