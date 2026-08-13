# archive-availability-fallback Specification

## Purpose

Define safe release fallback when a selected origin archive cannot be made
available after retry handling.

## Requirements

### Requirement: Re-plan after an archive-specific failure

The system SHALL exclude an archive's logical key and re-plan from the already
fetched source index when its download, integrity check, or per-archive target
upload remains unavailable. It SHALL select the newest remaining eligible
origin release for each library name and configured package platform. A failed
tool archive SHALL make platforms requiring that exact tool version ineligible.
Cooperative cancellation SHALL propagate immediately and SHALL not initiate
fallback.

#### Scenario: Fall back from a missing latest library release

- **WHEN** the newest selected library archive is permanently unavailable and an
  older eligible origin release exists
- **THEN** the system selects the older release and publishes an index that names
  it instead

### Requirement: Reconcile every fallback plan

The system SHALL reconcile the target after every fallback re-plan before it
publishes the replacement plan. It SHALL only replace an index after every
archive referenced by that final index is available in the target.

#### Scenario: Continue with other eligible releases

- **WHEN** one selected archive is unavailable while other selected releases
  remain eligible
- **THEN** the system publishes the eligible release set and exposes none of the
  unavailable archive in the replacement index

### Requirement: Preserve prior publication when fallback is empty

If exclusions leave no selected origin archives, the system SHALL not reconcile,
replace the family index, or clean its archives. Source-index retrieval, target
reconciliation, index replacement, and cleanup failures SHALL propagate without
fallback because they do not identify a safe replacement archive.

#### Scenario: All origin candidates are unavailable

- **WHEN** every selected origin archive has become unavailable
- **THEN** the system retains the previously published family index and archives
  unchanged

### Requirement: Observable fallback

The system SHALL emit a structured debug record for every excluded archive that
causes a fallback plan. The record SHALL identify the family and logical archive
key and SHALL not include credentials.

#### Scenario: Inspect a fallback

- **WHEN** an operator enables debug logging and a candidate archive becomes
  unavailable
- **THEN** the records include the family and unavailable logical key before the
  next plan is selected
