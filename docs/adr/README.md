# Architectural Decision Records

This directory holds the project's active set of architectural decisions.
Each ADR describes a decision currently in effect, with its context,
alternatives considered, and consequences.

## Format and conventions

- One decision per file, named `ADR-NNNN-kebab-case-title.md`.
- Sequential numbering starting at `0001`. No numbers are reserved.
- Status follows the standard ADR lifecycle (Proposed, Accepted,
  Deprecated, Superseded). A `Supersedes` / `Superseded by` pair links
  related ADRs when a later decision replaces an earlier one.
- The `Supersedes` field is left empty unless this ADR actually replaces
  a previously-Accepted ADR in this directory. Do not link to retired
  sources outside this directory.
- New ADRs use `docs/decisions/_template.md`.

## Scope

An ADR records an architectural trade-off: a choice between viable
approaches that is non-obvious from reading the code alone, where the
wrong choice has long-term cost. Module boundaries, data ownership,
protocols, tech/library selection, security model, failure semantics,
identity/lifecycle design, dependency direction — ADR-worthy.

Bug fixes, file relocations, test additions, spec maintenance,
incremental renames, and feature work are not ADRs. If no real
alternative was considered, there is no decision to record.
