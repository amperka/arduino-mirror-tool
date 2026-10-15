# publication-pipeline Specification

## Purpose

Define the current contract for independently mirroring Arduino Boards Manager
and Library Manager indexes from one configured HTTP origin to a local directory,
an S3-compatible target, or a GCS bucket.
## Requirements
### Requirement: Independent family commands

The system SHALL expose `packages` and `libraries` commands. Each invocation
SHALL operate on exactly one `IndexFamily`, fetch only that family's configured
input index, and never begin the other family's pipeline. CLI values SHALL take
precedence over non-empty environment values, which SHALL take precedence over
defaults. `--dry-run` SHALL select and report a plan without reconciling or
mutating a target.

#### Scenario: Preview library publication

- **WHEN** an operator runs `arduino-mirror libraries --dry-run`
- **THEN** the system fetches and selects the library index but performs no target
  read, archive download, upload, index replacement, or cleanup

### Requirement: Family-specific index selection

The system SHALL use separate selection policies for the two Arduino index
formats. For configured package names and architectures, the package policy
SHALL retain the newest eligible configured-origin platform per architecture and
the exact tool versions required by each retained platform. For a configured
package with no platforms, it SHALL retain the newest eligible tool version per
tool name. The library policy SHALL retain the newest eligible configured-origin
release per exact library name. For library releases with the same base version,
SemVer numeric prerelease identifiers SHALL have lower precedence than textual
prerelease identifiers at the same identifier position; stable releases SHALL
have higher precedence than prereleases. Library records, and configured
package/platform records, outside the configured origin SHALL remain unchanged
and SHALL create no mirror archive work. Selected origin archive URLs SHALL be
rewritten to the configured mirror host while other retained record fields are
preserved.

#### Scenario: Preserve an external library release

- **WHEN** a Library Manager record has an archive URL outside the configured
  origin
- **THEN** the generated index retains that record and its original URL, and no
  archive descriptor is created for it

#### Scenario: Prefer a textual library prerelease identifier

- **WHEN** eligible origin releases for one library are `1.0.0-1` and
  `1.0.0-alpha`
- **THEN** the selected release is `1.0.0-alpha`

### Requirement: Verified archive publication

The system SHALL create archive work only for selected configured-origin URLs.
Before a target publishes an archive, it SHALL stream the response through
temporary storage and verify a declared SHA-256 and/or declared size when those
fields are available. Temporary archive bytes SHALL be discarded after a
successful, failed, or cancelled transfer. A verification failure SHALL prevent
that failed candidate from appearing in a replaced index.

#### Scenario: Reject mismatched archive bytes

- **WHEN** an origin archive does not match its declared checksum or size
- **THEN** the target does not publish those bytes or replace an index that names
them

### Requirement: Target configuration validation

The system SHALL reject an input index URL without a non-empty absolute path or
with an empty, `.` or `..` path segment before target composition. It SHALL reject
an S3 target without its bucket, access key, or secret key. It SHALL reject a GCS
target without its bucket; GCS SHALL use ADC and SHALL NOT require S3 credentials.

#### Scenario: Reject an unusable input index

- **WHEN** an operator supplies a relative input-index URL
- **THEN** configuration fails before a publication target is created

### Requirement: Safe, family-scoped publication order

For non-empty origin plans, the system SHALL reconcile the family inventory,
publish all required archives, replace the family index, then remove reconciled
stale keys. Reconciliation and cleanup SHALL affect only that family. The local
target SHALL replace the index atomically; S3 and GCS SHALL use one object write.
Empty origin selections SHALL leave existing family target state unchanged.

#### Scenario: Fail before index replacement

- **WHEN** an archive cannot be made available and no eligible fallback remains
- **THEN** the system leaves the previously published family index and archives
  unchanged

### Requirement: Reconcile target archives without unnecessary transfers

The local target SHALL scan only the selected family namespace and retain an
archive only when all declared integrity fields match (size and/or SHA-256).
S3-compatible and GCS targets SHALL use one family-prefix listing without
per-archive object requests, retaining archives only when the listing confirms
the selected key and its declared size. Targets SHALL download and publish any
unconfirmed archive.

#### Scenario: Replace a same-size local archive with a mismatched checksum

- **WHEN** a local archive has the selected archive's declared size but differs
  from its declared SHA-256
- **THEN** the local target downloads and publishes the selected origin bytes

#### Scenario: Confirm cloud archives from one listing

- **WHEN** a family-prefix listing contains selected archive keys with their
  declared sizes
- **THEN** the S3-compatible or GCS target does not download or upload those archives

### Requirement: Cooperative cancellation

The CLI SHALL convert the first SIGINT or SIGTERM into cooperative cancellation.
It SHALL interrupt an active archive download and discard partial temporary
bytes. An upload, index replacement, or stale-object deletion already in
progress MAY finish, but no later operation SHALL start after cancellation is
observed. The command SHALL report `128 + signal number` when cancellation
reaches a boundary. A second SIGINT or SIGTERM SHALL restore default handling as
an emergency exit.

#### Scenario: Cancel before index replacement

- **WHEN** SIGTERM is received after archive publication but before index
  replacement
- **THEN** the command returns status 143 without replacing the index or starting
  stale cleanup

### Requirement: Operator-visible execution

The system SHALL report normal progress for source retrieval, selection, archive
transfer, index publication, and stale cleanup. At debug level it SHALL emit
structured records for configuration resolution, composition, source retrieval,
selection, reconciliation, archive verification, target operations, index
replacement, cleanup, cancellation, and archive fallback. Publication-flow
boundary records SHALL identify the family; archive verification and fallback
records SHALL also identify the archive key. Records SHALL NOT include
credentials.

#### Scenario: Inspect a successful family run

- **WHEN** an operator runs a family command with `--log-level DEBUG`
- **THEN** its records identify the completed family-specific publication
  boundaries in execution order without credentials

### Requirement: GCS publication with federated credentials

The system SHALL support `--target gcs` and `TARGET_KIND=gcs` using ADC. A preview
SHALL NOT discover cloud credentials or construct an authenticated storage client.
Production workflows SHALL export ADC using `GCP_WIF_PROVIDER` and
`GCP_WIF_SERVICE_ACCOUNT` with `id-token: write` and SHALL publish independently
to `amperka-arduino-downloads` without reading legacy S3 secrets.

#### Scenario: Publish through GitHub WIF

- **WHEN** either production family workflow runs
- **THEN** it authenticates using WIF after checkout, uses the `prod` environment,
  and publishes only its configured family to GCS

#### Scenario: Preview without cloud credentials

- **WHEN** an operator previews a GCS target with a bucket configured
- **THEN** the system selects a plan without ADC discovery or GCS access
