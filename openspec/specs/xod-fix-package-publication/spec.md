# xod-fix-package-publication Specification

## Purpose

Define the build and scheduled S3 publication contract for XOD bootstrap
`__packages__` archives.

## Requirements

### Requirement: XOD bootstrap archive build script

The repository SHALL provide `scripts/build-xod-fix-packages.sh` as a
POSIX-hosted Bash script that builds the Linux, Windows, and macOS XOD
`__packages__` archives. It SHALL resolve its mirror root from a non-empty
`MIRROR_HOST` environment variable or default to
`https://arduino-downloads.amperka.ru`, retrieve the ESP8266 index over HTTPS,
and write `xod-packages-linux-x86_64.tar.gz`,
`xod-packages-windows-x86_64.zip`, and
`xod-packages-macos-x86_64.tar.gz` to `OUT`, defaulting to `dist`. Its comments
and operator output SHALL be technical English.

#### Scenario: Build with the default mirror

- **WHEN** an operator runs the script without `MIRROR_HOST` or `OUT`
- **THEN** it uses `https://arduino-downloads.amperka.ru` and writes the three
  platform archives under `dist`

#### Scenario: Build with the configured production mirror

- **WHEN** CI supplies a non-empty `MIRROR_HOST`
- **THEN** the script retrieves its mirrored package index and tools from that
  host

### Requirement: Scheduled XOD archive publication

The repository SHALL provide a GitHub Actions workflow that runs every Sunday
at 06:17 UTC and supports `workflow_dispatch`. It SHALL run on
`ubuntu-latest` in the `prod` environment with read-only repository contents,
install rclone from APT, set `MIRROR_HOST` from `vars.MIRROR_HOST`, and invoke
the XOD build script. It SHALL not use the Python application, its dependency
installation, AWS CLI, or a third-party GitHub Action for S3 publication.

#### Scenario: Scheduled build

- **WHEN** the Sunday 06:17 UTC schedule fires
- **THEN** the workflow builds all three XOD archives using the configured
  production mirror

#### Scenario: Manual build

- **WHEN** an operator starts the workflow from the Actions UI
- **THEN** it performs the same build and publication flow as a scheduled run

### Requirement: Isolated XOD S3 synchronization

Before publishing, the workflow SHALL verify that the Linux and macOS tarballs
and the Windows ZIP archive can each have their file lists read. It SHALL
serialize runs in the `xod-fix-publication` concurrency group without
cancelling an active run. It SHALL configure an rclone S3 remote named
`storage` with provider `Other` and ACL `public-read`; it SHALL map the
existing `TARGET_ENDPOINT`, `TARGET_ACCESS_KEY_ID`, and
`TARGET_SECRET_ACCESS_KEY` secrets to that remote's endpoint and access-key
environment variables. It SHALL run an rclone synchronization from `dist/` to
`storage:${TARGET_BUCKET}/xod-fix/`, making only that prefix match the build
output, including removal of destination-only objects. A failed build,
validation, or synchronization SHALL fail the workflow and SHALL not report a
successful publication.

#### Scenario: Successful prefix reconciliation

- **WHEN** all three archives are listable and rclone synchronization succeeds
- **THEN** `xod-fix/` contains the current build output with publicly readable
  object ACLs and no destination-only objects

#### Scenario: Invalid archive

- **WHEN** any generated archive cannot have its file list read
- **THEN** the workflow fails before it starts S3 synchronization

#### Scenario: Overlapping triggers

- **WHEN** a manual run begins while a scheduled XOD publication is active
- **THEN** the later run waits without cancelling the active run
