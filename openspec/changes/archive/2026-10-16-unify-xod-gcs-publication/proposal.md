# Publish XOD archives into the shared GCS bucket

## Why

All production artifacts must live in `amperka-arduino-downloads`. The two mirror
workflows already target GCS, but the newly merged XOD workflow still targets S3
and requires legacy secrets. This leaves the migration incomplete.

## What Changes

- Keep the XOD build, archive validation, weekly schedule, concurrency, and rclone.
- Authenticate through the same GitHub WIF provider/service account and ADC export
  as the package/library workflows.
- Synchronize only `gs://amperka-arduino-downloads/xod-fix/` through rclone's native
  GCS backend, without object ACLs, bucket creation, or legacy S3 secrets.
- Test the shared bucket/authentication and prefix-isolation contracts, and update
  deployment documentation. Do not change or accept the proposed Python SDK ADR.

## Impact

Affected capability: `xod-fix-package-publication`. No new dependencies: rclone
remains installed from APT. No cloud data, IAM, secrets, or remote Git state will
be changed during local implementation. Live deployment remains a separate,
authorized check; existing objects are not migrated by this code change.
