# local-index-overlay Specification

## Purpose

Define the configuration, validation, and deterministic merge behavior for an
optional local Arduino index overlay prepared before family selection.

## Requirements

### Requirement: Family-scoped local overlay configuration

The system SHALL accept an optional `--local-index` path for both `packages` and
`libraries` commands. For `packages`, it SHALL resolve a non-empty CLI value,
then a non-empty `PACKAGES_LOCAL_INDEX` value, then no overlay. For `libraries`,
it SHALL resolve a non-empty CLI value, then a non-empty `LIBRARIES_LOCAL_INDEX`
value, then no overlay. The setting for the unselected family SHALL be ignored.

#### Scenario: Package CLI path overrides package environment path

- **WHEN** an operator runs
  `arduino-mirror packages --local-index ./cli-packages.json` with
  `PACKAGES_LOCAL_INDEX=./environment-packages.json`
- **THEN** the packages pipeline uses `./cli-packages.json` as its only local
  overlay

#### Scenario: Library command ignores package overlay environment

- **WHEN** an operator runs `arduino-mirror libraries` with only
  `PACKAGES_LOCAL_INDEX=./packages.json` set
- **THEN** the libraries pipeline runs without a local overlay

### Requirement: Merge a local overlay before selection

When a local overlay is configured, the system SHALL fetch the selected family's
remote index, merge its selected family collection with the local JSON index, and
pass the merged index to selection. Package tool pins SHALL be evaluated against
that merged index.

The local overlay SHALL be a JSON object containing a list under `packages` for
the packages family or `libraries` for the libraries family. Invalid JSON, a
non-object root, or a missing or non-list selected collection SHALL fail the
command before any publication-target operation.

#### Scenario: Pin selects an overlay tool version

- **WHEN** a package overlay adds `builtin:serial-discovery@1.1.0` and
  `PINNED_TOOLS` includes that exact identity
- **THEN** package selection retains the overlay tool version and its selected
  systems

#### Scenario: Invalid overlay prevents target access

- **WHEN** the configured local overlay is invalid JSON
- **THEN** the command fails without target reconciliation, archive publication,
  index replacement, or stale cleanup

### Requirement: Deterministic schema-aware overlay merge

The merge SHALL retain remote-record order, replace matching records in their
remote position, and append unmatched local records in local-file order. A local
record SHALL overlay only fields that it supplies.

Packages SHALL match by `name`; platforms within a matching package SHALL match
by `(architecture, version)`; tools within a matching package SHALL match by
`(name, version)`; and systems within a matching tool SHALL match by `host`. A
local system with a matching `host` SHALL replace the remote system's supplied
fields, while a local system with a new host SHALL be appended. Libraries SHALL
match by `(name, version)`.

#### Scenario: Add a host-specific system to an existing tool

- **WHEN** a local overlay supplies `builtin:serial-discovery@1.0.0` with a
  system whose `host` is absent from the remote tool
- **THEN** the merged tool contains both every remote system and the new local
  system

#### Scenario: Replace a host-specific system

- **WHEN** a local overlay supplies a system with the same `host` as an existing
  `builtin:serial-discovery@1.0.0` system
- **THEN** the merged tool uses the local system fields for that host and retains
  its other systems

### Requirement: Overlay archive URLs retain origin ownership behavior

The system SHALL apply its existing archive-origin rules to records after
merging. A selected configured-origin URL, including one introduced by an
overlay, SHALL be verified, published, and rewritten to the mirror host. A
selected URL outside the configured origin, including one introduced by an
overlay, SHALL remain unchanged and SHALL create no archive publication work.

#### Scenario: Preserve an external overlay system URL

- **WHEN** a selected pinned overlay tool contains one system URL outside the
  configured origin
- **THEN** the published package index retains that system URL and the target
  does not download or upload it
