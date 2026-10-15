# Publish to Google Cloud Storage using WIF

## Why

The production mirror is moving from S3-compatible storage to the existing GCS
bucket `amperka-arduino-downloads`, confirmed by the operator. GitHub already has `GCP_WIF_PROVIDER` and
`GCP_WIF_SERVICE_ACCOUNT`; scheduled publication still uses legacy S3 secrets.

## What Changes

- Add an additive `gcs` publication target using Application Default Credentials
  (ADC), preserving local/S3 CLI behavior and family-owned object keys.
- Add `google-cloud-storage>=3.0` as a runtime dependency. The official SDK
  handles ADC/WIF refresh, resumable uploads, and integrity checks; hand-written
  OAuth/storage HTTP or static GCS HMAC keys would increase security burden.
- Use the existing bounded retry engine, disabling nested SDK storage retries.
- Authenticate both production workflows with GitHub OIDC/WIF and publish to
  `amperka-arduino-downloads` without any legacy S3 secret references.
- Document required IAM and safe secret retirement. Do not delete secrets,
  change cloud IAM, deploy, migrate objects, or change DNS in this change.

## Impact

Affected capabilities: publication-pipeline, archive-index-layout, network-retry.
Verification: focused adapter/config/workflow tests, local HTTP plus GCS SDK
integration/e2e contract tests without live credentials, existing test suites,
static checks, and OpenSpec validation. A real GitHub WIF publication remains an
explicitly authorized operational check.
