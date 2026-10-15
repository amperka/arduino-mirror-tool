## MODIFIED Requirements

### Requirement: Scheduled XOD archive publication

The repository SHALL provide a GitHub Actions workflow that runs every Sunday
at 06:17 UTC and supports `workflow_dispatch`. It SHALL use `ubuntu-latest`, the
`prod` environment, read-only repository contents, and `id-token: write`.
It SHALL install rclone from APT, set `MIRROR_HOST` from `vars.MIRROR_HOST`, and
invoke the unchanged XOD build script. It SHALL NOT use the Python application,
its dependency installation, or AWS CLI.

#### Scenario: Scheduled build

- **WHEN** the Sunday 06:17 UTC schedule fires
- **THEN** the workflow builds all three XOD archives using the configured mirror

#### Scenario: Manual build

- **WHEN** an operator starts the workflow from the Actions UI
- **THEN** it performs the same build and publication flow as a scheduled run

## REMOVED Requirements

### Requirement: Isolated XOD S3 synchronization

**Reason**: XOD archives must share the GCS bucket used by both mirror workflows.
**Migration**: Preserve the `xod-fix/` namespace and rclone synchronization, replacing
S3 configuration and secrets with GCS and the existing WIF identity.

## ADDED Requirements

### Requirement: Shared GCS authentication and destination

The XOD workflow SHALL use `google-github-actions/auth@v3` after checkout to export
refreshable ADC using `GCP_WIF_PROVIDER` and `GCP_WIF_SERVICE_ACCOUNT`. It SHALL
publish to the existing `amperka-arduino-downloads` bucket, the same bucket as the
packages and libraries workflows, without reading legacy S3 secrets or using a
service-account key or fixed access token.

#### Scenario: One production artifact bucket

- **WHEN** the three publication workflows are configured
- **THEN** all target `amperka-arduino-downloads` using the same WIF secrets and
  the `prod` environment

### Requirement: Isolated XOD GCS synchronization

The workflow SHALL validate all three archive file lists before synchronization.
It SHALL serialize runs in `xod-fix-publication` without cancelling active runs.
It SHALL configure rclone's native GCS remote `storage` to use ADC and bucket-level
IAM, without setting object ACLs or checking/creating buckets. Synchronization
SHALL make only `amperka-arduino-downloads/xod-fix/` match `dist/`, including removal
of destination-only objects. Failed builds, validation, or sync SHALL fail the job.

#### Scenario: Successful prefix reconciliation

- **WHEN** all three archives are valid and synchronization succeeds
- **THEN** `xod-fix/` contains the current output and no stale objects, while `p/`,
  `l/`, and other prefixes are untouched; public access follows existing bucket IAM

#### Scenario: Invalid archive

- **WHEN** a generated archive cannot have its file list read
- **THEN** the workflow fails before it starts GCS synchronization

#### Scenario: Overlapping triggers

- **WHEN** a manual run starts during an active scheduled XOD publication
- **THEN** the later run waits without cancelling the active run
