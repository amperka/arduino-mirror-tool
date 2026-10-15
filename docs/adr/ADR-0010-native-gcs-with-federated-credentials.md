# ADR-0010: Publish to GCS with native SDK and federated credentials

- **Status:** Proposed
- **Date:** 2026-10-16
- **Supersedes:**
- **Superseded by:**

## Context

Production now has a GCS bucket and GitHub WIF provider/service account. The
existing MinIO adapter requires S3 HMAC keys, which WIF does not supply. The
publication contract must retain streaming verification, independent family
ownership, and archives-before-index-before-cleanup ordering.

## Decision

Add a native GCS adapter behind the existing publication port using
`google-cloud-storage` and ADC. GitHub Actions exports refreshable WIF ADC via
`google-github-actions/auth`; no service-account JSON key or static access token
is stored. Reuse the application's bounded cancellation-aware retry engine and
disable SDK storage retries. Preserve the local and S3 targets and CLI default.

## Alternatives Considered

### GCS S3 interoperability with HMAC keys

Rejected: requires additional long-lived secrets and cannot use the supplied WIF
identity directly.

### Local staging followed by gcloud storage rsync

Rejected: stages the entire mirror and does not preserve the existing incremental
archive verification and ordered family index publication contract directly.

### Hand-written REST and OAuth

Rejected: duplicates ADC refresh, resumable transfer, and checksum behavior
already maintained in Google's SDK.

## Consequences

- **Positive:** CI uses short-lived federated credentials; storage remains an
  infrastructure concern and all public object keys remain stable.
- **Negative / trade-offs:** Adds the official GCS SDK and its dependencies.
- **Accepted risks:** Live WIF/IAM and public-host routing require an authorized
  production check; local tests cannot establish those external permissions.
