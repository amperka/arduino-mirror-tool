# network-retry Specification

## Purpose

Define bounded recovery from transient failures at HTTP, S3-compatible, and GCS
publication boundaries.

## Requirements

### Requirement: Bounded transient retry

The system SHALL retry transient failures during index retrieval, archive
download, and S3-compatible/GCS archive/index writes and stale-object removals using
a bounded full-jitter exponential-backoff schedule. It SHALL retry HTTP/S3-compatible
throttling (429), server failures (5xx), and eligible transport failures,
including mid-stream transfer failures. Once the configured attempt budget is
exhausted, it SHALL propagate the last failure.

#### Scenario: Recover an interrupted archive transfer

- **WHEN** an archive transfer fails with a transient chunked-encoding error and
  a later attempt succeeds within the attempt budget
- **THEN** the system retries the transfer and publishes the verified archive

### Requirement: S3 listing failures propagate during iteration

The system SHALL propagate an error raised while consuming the MinIO family
listing iterator. The current retry wrapper covers construction of that iterator,
not its later iteration; it SHALL not schedule another listing attempt for such
an error.

#### Scenario: Fail while consuming a family listing

- **WHEN** a transient S3-compatible error is raised after the MinIO listing
  iterator has been returned
- **THEN** reconciliation stops and propagates the error without a retry

### Requirement: Permanent failures are not retried

The system SHALL not retry HTTP 4xx responses other than 429, malformed URL,
schema, or header errors, invalid index payloads, archive integrity failures, or
cooperative cancellation. Upload retries SHALL rewind the verified stream so
every attempt sends the complete archive bytes.

#### Scenario: Propagate an unavailable index immediately

- **WHEN** index retrieval receives HTTP 404
- **THEN** the system does not retry the request and propagates the error

### Requirement: Cancellation-aware backoff

The system SHALL check cooperative cancellation before each retry attempt and
during inter-attempt backoff. A cancellation observed during backoff SHALL stop
further attempts with bounded latency.

#### Scenario: Cancel retry backoff

- **WHEN** cancellation is requested while a retry delay is in progress
- **THEN** the system stops retrying before the next invocation

### Requirement: Retry configuration and observability

The system SHALL expose `--retry-attempts` and `--retry-base-delay`, with
environment fallback through `RETRY_ATTEMPTS` and `RETRY_BASE_DELAY`. Defaults
SHALL be 10 total attempts and 1.0 second base delay; attempts SHALL be positive
and base delay non-negative. Debug records for scheduled retries and exhaustion
SHALL include attempt count, error type, and planned delay where applicable, and
SHALL not include credentials.

#### Scenario: Override the retry schedule

- **WHEN** an operator supplies `--retry-attempts 4 --retry-base-delay 2.5`
- **THEN** transient operations receive no more than four total attempts with a
  2.5-second base backoff ceiling before jitter

### Requirement: GCS retries have one application-owned budget

The GCS target SHALL disable nested SDK storage retries and use the configured
bounded retry engine for listing, archive upload, index replacement, and stale
removal. It SHALL retry 429, 5xx, and eligible transport failures, materializing
the entire listing within each retry attempt. Authentication, authorization,
validation, and integrity failures SHALL NOT be retried. Deleting an already
absent stale object SHALL succeed.

#### Scenario: Restart an interrupted paginated GCS listing

- **WHEN** consuming the GCS listing iterator fails transiently within the budget
- **THEN** reconciliation restarts the family listing and discards partial results

#### Scenario: Retry a GCS upload

- **WHEN** an archive upload fails transiently after consuming stream bytes
- **THEN** the next attempt rewinds and uploads the complete verified archive

#### Scenario: Stale deletion response is lost

- **WHEN** a repeated deletion finds that its stale GCS object is already absent
- **THEN** cleanup treats the missing object as success
