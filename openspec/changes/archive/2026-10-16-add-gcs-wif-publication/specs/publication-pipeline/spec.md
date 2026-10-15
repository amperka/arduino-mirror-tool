## ADDED Requirements

### Requirement: GCS publication with federated credentials

The system SHALL support `--target gcs` and `TARGET_KIND=gcs`, require a non-empty
`TARGET_BUCKET`/`--bucket`, and use Application Default Credentials without S3
keys. It SHALL reconcile the selected family from one prefix listing, confirm
archives by declared size, publish verified archives before atomically replacing
the index object, and remove only reconciled same-family stale archives afterward.
A dry run SHALL not construct an authenticated GCS client or access GCS.

#### Scenario: Publish through GitHub WIF

- **WHEN** either production family workflow runs
- **THEN** it grants `id-token: write`, authenticates with `GCP_WIF_PROVIDER` and
  `GCP_WIF_SERVICE_ACCOUNT`, and publishes to `amperka-arduino-downloads` using
  exported ADC credentials without reading legacy S3 secrets

#### Scenario: Preview without cloud credentials

- **WHEN** an operator previews `--target gcs --bucket amperka-arduino-downloads`
- **THEN** the system plans the family without discovering ADC or accessing GCS
