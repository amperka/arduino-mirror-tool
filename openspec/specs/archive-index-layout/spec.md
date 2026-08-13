# archive-index-layout Specification

## Purpose

Define the stable public and storage layout for archive objects and transformed
Arduino indexes.

## Requirements

### Requirement: Source-independent archive namespaces

The system SHALL assign selected package archives logical keys beginning with
`p/` and selected library archives logical keys beginning with `l/`. It SHALL
append the complete path relative to the configured origin host without
removing, adding, or interpreting source path segments. A transformed archive
URL SHALL consist of the configured mirror host followed by that logical key.

#### Scenario: Library path already contains its family name

- **WHEN** an eligible library archive URL has path
  `/libraries/Servo-1.2.2.zip`
- **THEN** its logical key is `l/libraries/Servo-1.2.2.zip`, not
  `libraries/libraries/Servo-1.2.2.zip`

### Requirement: Family-prefixed index placement

The system SHALL publish each transformed index below its family namespace and
its complete configured input-index path relative to the origin host. It SHALL
not publish either index at the target root. Local and S3-compatible targets
SHALL exclude that index object from archive reconciliation.

#### Scenario: Publish standard Arduino indexes

- **WHEN** the package and library input paths are
  `/packages/package_index.json` and `/libraries/library_index.json`
- **THEN** their logical target keys are `p/packages/package_index.json` and
  `l/libraries/library_index.json`, respectively

### Requirement: Outer target prefix

The system SHALL apply an optional target prefix outside logical archive and
index keys. It SHALL not add this storage-only prefix when rewriting public
archive URLs.

#### Scenario: Publish under a target prefix

- **WHEN** the library input path is `/libraries/library_index.json` and the
  target prefix is `managed`
- **THEN** the storage key is `managed/l/libraries/library_index.json`
