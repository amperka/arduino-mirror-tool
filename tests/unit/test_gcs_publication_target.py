# region MODULE_CONTRACT
# PURPOSE: Verify native GCS publication ownership, lazy ADC, and bounded retry/cancellation behavior without cloud access.
# SCOPE:
# - Public SDK call mapping, iterator failures, verified stream lifetime, and retry classification.
# - NOT: Live GCS, credential files, or application orchestration.
# KEYWORDS: unit test, GCS, ADC, publication, retry
# endregion MODULE_CONTRACT

"""Focused, network-free tests for the native GCS target."""

from __future__ import annotations

import io
import json
import logging
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import call

import pytest
import requests
from google.api_core.exceptions import (
    BadRequest,
    Forbidden,
    GoogleAPICallError,
    InternalServerError,
    NotFound,
    PreconditionFailed,
    ServiceUnavailable,
    TooManyRequests,
    Unauthorized,
)
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from google.cloud import storage
from google.cloud.storage.exceptions import DataCorruption, InvalidResponse

from arduino_mirror.domain import (
    Archive,
    ArchiveUnavailableError,
    IndexFamily,
    PublicationCancellation,
    PublicationPlan,
)
from arduino_mirror.infra.archive_tempfile import VerifiedArchive
from arduino_mirror.infra.gcs_target import GcsPublicationTarget
from arduino_mirror.infra.retry import RetryPolicy
from tests.log_assertions import extra_fields

__all__ = []

_INDEX_KEY = "l/libraries/library_index.json"
_BODY = b"archive"
_SIZE = len(_BODY)
_ATTEMPTS = 3
_RECOVERY_ATTEMPTS = 2


# region FUNC__plan
# PURPOSE: Provide a small immutable family plan with a selected archive and one stale archive.
def _plan(*, family=IndexFamily.LIBRARIES, size=_SIZE):
    prefix = family.archive_prefix
    return PublicationPlan(
        family=family,
        releases=("Servo@1.0.0",),
        archives=(
            Archive(
                key=f"{prefix}/libraries/Servo-1.0.0.zip",
                source_url="https://origin.test.invalid/libraries/Servo-1.0.0.zip",
                size=size,
            ),
        ),
        index={"libraries": [{"name": "Серво"}]},
        stale_keys=(f"{prefix}/obsolete.zip",),
    )


# endregion FUNC__plan


# region FUNC__verified_archive
# PURPOSE: Substitute verified temporary bytes while preserving deterministic context cleanup.
@contextmanager
def _verified_archive(*args, **kwargs):
    with io.BytesIO(_BODY) as stream:
        yield VerifiedArchive(stream=stream, size=len(_BODY))


# endregion FUNC__verified_archive


@pytest.fixture
def cancellation(mocker):
    return mocker.create_autospec(PublicationCancellation, instance=True)


@pytest.fixture
def gcs(mocker):
    bucket = mocker.create_autospec(storage.Bucket, instance=True)
    blob = mocker.create_autospec(storage.Blob, instance=True)
    bucket.blob.return_value = blob
    bucket.list_blobs.return_value = []
    constructor = mocker.patch(
        "arduino_mirror.infra.gcs_target.storage.Client", autospec=True
    )
    constructor.return_value.bucket.return_value = bucket
    download = mocker.patch(
        "arduino_mirror.infra.gcs_target.download_verified", wraps=_verified_archive
    )
    target = GcsPublicationTarget(
        bucket="mirror",
        index_key=_INDEX_KEY,
        prefix="/managed/",
        timeout_seconds=17,
        retry_policy=RetryPolicy(max_attempts=_ATTEMPTS, base_delay=0),
    )
    return SimpleNamespace(
        target=target,
        constructor=constructor,
        bucket=bucket,
        blob=blob,
        download=download,
    )


# region FUNC_test_gcs_maps_family_operations_and_trace_boundaries
# PURPOSE: Verify stable logical keys, family-only stale inventory, UTF-8 JSON, and native SDK retry/timeout mapping.
def test_gcs_maps_family_operations_and_trace_boundaries(gcs, cancellation, caplog):
    caplog.set_level(logging.DEBUG, logger="arduino_mirror.infra.gcs_target")
    gcs.bucket.list_blobs.return_value = [
        SimpleNamespace(name="managed/l/z-obsolete.zip", size=3),
        SimpleNamespace(name="managed/l/obsolete.zip", size=3),
        SimpleNamespace(name=f"managed/{_INDEX_KEY}", size=20),
        SimpleNamespace(name="managed/p/protected.tar.bz2", size=9),
        SimpleNamespace(name="unmanaged/l/foreign.zip", size=9),
        SimpleNamespace(name=None, size=9),
    ]
    plan = _plan()
    reconciled = gcs.target.reconcile(plan)
    assert reconciled.stale_keys == ("l/obsolete.zip", "l/z-obsolete.zip")
    assert reconciled.archives_to_publish == plan.archives
    assert reconciled.archive_keys == plan.archive_keys

    bodies = []
    streams = []

    def upload(stream, **kwargs):
        bodies.append(stream.read())
        streams.append(stream)

    gcs.blob.upload_from_file.side_effect = upload
    gcs.target.publish_archives(reconciled, cancellation=cancellation)
    gcs.target.replace_index(reconciled, cancellation=cancellation)
    gcs.target.cleanup_stale(reconciled, cancellation=cancellation)

    gcs.constructor.assert_called_once_with(project=None)
    gcs.constructor.return_value.bucket.assert_called_once_with("mirror")
    gcs.bucket.list_blobs.assert_called_once_with(
        prefix="managed/l/", timeout=17, retry=None
    )
    assert gcs.bucket.blob.call_args_list == [
        call("managed/l/libraries/Servo-1.0.0.zip"),
        call(f"managed/{_INDEX_KEY}"),
        call("managed/l/obsolete.zip"),
        call("managed/l/z-obsolete.zip"),
    ]
    assert gcs.blob.upload_from_file.call_args.kwargs == {
        "size": len(_BODY),
        "rewind": True,
        "timeout": 17,
        "retry": None,
    }
    assert bodies == [_BODY]
    assert streams[0].closed
    body = json.dumps(plan.index, ensure_ascii=False, indent=2).encode("utf-8")
    gcs.blob.upload_from_string.assert_called_once_with(
        body, content_type="application/json", timeout=17, retry=None
    )
    assert json.loads(body) == plan.index
    assert "Серво".encode() in body
    assert gcs.blob.delete.call_args_list == [
        call(timeout=17, retry=None),
        call(timeout=17, retry=None),
    ]
    gcs.download.assert_called_once_with(
        plan.archives[0],
        17,
        plan.family,
        cancellation,
        retry_policy=RetryPolicy(max_attempts=_ATTEMPTS, base_delay=0),
    )
    records = [record for record in caplog.records if record.levelno == logging.DEBUG]
    assert [record.getMessage() for record in records] == [
        "TARGET_ARCHIVE_KEYS_LISTED",
        "TARGET_ARCHIVES_PUBLISHED",
        "TARGET_INDEX_REPLACED",
        "TARGET_STALE_CLEANED",
    ]
    assert [extra_fields(record) for record in records] == [
        {"archive_count": 2, "family": plan.family},
        {"archive_count": 1, "family": plan.family},
        {"family": plan.family, "index_key": _INDEX_KEY},
        {"family": plan.family, "stale_count": 2},
    ]


# endregion FUNC_test_gcs_maps_family_operations_and_trace_boundaries


# region FUNC_test_gcs_defaults_are_lazy_and_unprefixed
# PURPOSE: Ensure dry-run composition performs no ADC discovery and first storage access caches a projectless bucket handle.
def test_gcs_defaults_are_lazy_and_unprefixed(gcs):
    target = GcsPublicationTarget(bucket="other", index_key=_INDEX_KEY)
    gcs.constructor.assert_not_called()
    gcs.bucket.list_blobs.assert_not_called()
    for _ in range(2):
        target.reconcile(_plan())
    gcs.constructor.assert_called_once_with(project=None)
    gcs.constructor.return_value.bucket.assert_called_once_with("other")
    assert gcs.bucket.list_blobs.call_args_list == [
        call(prefix="l/", timeout=600.0, retry=None),
        call(prefix="l/", timeout=600.0, retry=None),
    ]


# endregion FUNC_test_gcs_defaults_are_lazy_and_unprefixed


# region FUNC_test_gcs_reconciles_by_declared_size_without_object_reads
# PURPOSE: Skip only selected keys confirmed by size while preserving the complete logical archive set.
@pytest.mark.parametrize("family", [IndexFamily.PACKAGES, IndexFamily.LIBRARIES])
@pytest.mark.parametrize(
    ("declared_size", "listed_size", "pending"),
    [
        (len(_BODY), len(_BODY), False),
        (len(_BODY), 1, True),
        (len(_BODY), None, True),
        (None, len(_BODY), True),
        (None, None, True),
        (0, 0, False),
    ],
)
def test_gcs_reconciles_by_declared_size_without_object_reads(  # noqa: PLR0913, PLR0917
    gcs, cancellation, family, declared_size, listed_size, pending
):
    plan = _plan(family=family, size=declared_size)
    gcs.bucket.list_blobs.return_value = [
        SimpleNamespace(name=f"managed/{plan.archive_keys[0]}", size=listed_size)
    ]
    reconciled = gcs.target.reconcile(plan)
    assert reconciled.archives_to_publish == (plan.archives if pending else ())
    assert reconciled.archive_keys == plan.archive_keys
    assert reconciled.stale_keys == ()
    gcs.bucket.list_blobs.assert_called_once_with(
        prefix=f"managed/{family.archive_prefix}/", timeout=17, retry=None
    )
    gcs.bucket.blob.assert_not_called()
    if not pending:
        gcs.target.publish_archives(reconciled, cancellation=cancellation)
        gcs.download.assert_not_called()
        gcs.bucket.blob.assert_not_called()


# endregion FUNC_test_gcs_reconciles_by_declared_size_without_object_reads


# region FUNC_test_gcs_retries_partial_listing_from_a_fresh_inventory
# PURPOSE: Retry mid-pagination failures without retaining stale keys from the failed partial inventory.
def test_gcs_retries_partial_listing_from_a_fresh_inventory(gcs):
    def broken_page():
        yield SimpleNamespace(name="managed/l/partial-only.zip", size=2)
        raise ServiceUnavailable("next page unavailable")

    gcs.bucket.list_blobs.side_effect = [
        broken_page(),
        iter([SimpleNamespace(name="managed/l/final-only.zip", size=2)]),
    ]
    result = gcs.target.reconcile(_plan())
    assert result.stale_keys == ("l/final-only.zip",)
    assert gcs.bucket.list_blobs.call_count == _RECOVERY_ATTEMPTS


# endregion FUNC_test_gcs_retries_partial_listing_from_a_fresh_inventory


# region FUNC_test_gcs_listing_failure_obeys_attempt_budget
# PURPOSE: Propagate the last listing failure after the configured budget and stop permanent failures immediately.
@pytest.mark.parametrize(
    ("error", "attempts"),
    [(ServiceUnavailable("unavailable"), _ATTEMPTS), (Forbidden("denied"), 1)],
)
def test_gcs_listing_failure_obeys_attempt_budget(gcs, error, attempts):
    def broken_page(**kwargs):
        yield SimpleNamespace(name="managed/l/partial.zip", size=2)
        raise error

    gcs.bucket.list_blobs.side_effect = broken_page
    with pytest.raises(type(error)) as raised:
        gcs.target.reconcile(_plan())
    assert raised.value is error
    assert gcs.bucket.list_blobs.call_count == attempts
    gcs.bucket.blob.assert_not_called()


# endregion FUNC_test_gcs_listing_failure_obeys_attempt_budget


# region FUNC_test_gcs_upload_rewinds_without_redownloading
# PURPOSE: Retries resend all verified archive bytes and release the stream after success or exhaustion.
@pytest.mark.parametrize("succeeds", [True, False])
def test_gcs_upload_rewinds_without_redownloading(gcs, cancellation, succeeds):
    bodies = []
    streams = []
    failure = ServiceUnavailable("upload interrupted")

    def upload(stream, **kwargs):
        streams.append(stream)
        bodies.append(stream.read())
        if not succeeds or len(bodies) < _ATTEMPTS:
            raise failure

    gcs.blob.upload_from_file.side_effect = upload
    if succeeds:
        gcs.target.publish_archives(_plan(), cancellation=cancellation)
    else:
        with pytest.raises(ArchiveUnavailableError) as raised:
            gcs.target.publish_archives(_plan(), cancellation=cancellation)
        assert raised.value.archive_key == _plan().archive_keys[0]
        assert raised.value.__cause__ is failure
    assert bodies == [_BODY] * _ATTEMPTS
    assert all(stream is streams[0] for stream in streams)
    assert streams[0].closed
    gcs.download.assert_called_once()
    assert gcs.blob.upload_from_file.call_count == _ATTEMPTS


# endregion FUNC_test_gcs_upload_rewinds_without_redownloading


# region FUNC_test_gcs_archive_failures_are_wrapped_without_retrying_permanent_errors
# PURPOSE: Identify the failed logical archive for fallback and preserve the permanent cause.
def test_gcs_archive_failures_are_wrapped_without_retrying_permanent_errors(
    gcs, cancellation
):
    failure = Forbidden("denied")
    gcs.blob.upload_from_file.side_effect = failure
    with pytest.raises(ArchiveUnavailableError) as raised:
        gcs.target.publish_archives(_plan(), cancellation=cancellation)
    assert raised.value.archive_key == _plan().archive_keys[0]
    assert raised.value.__cause__ is failure
    gcs.blob.upload_from_file.assert_called_once()


# endregion FUNC_test_gcs_archive_failures_are_wrapped_without_retrying_permanent_errors


# region FUNC_test_gcs_download_failures_keep_the_archive_identity
# PURPOSE: Keep source and integrity failures archive-specific without attempting a storage write.
def test_gcs_download_failures_keep_the_archive_identity(gcs, cancellation):
    failure = ValueError("declared archive size mismatch")
    gcs.download.side_effect = failure
    with pytest.raises(ArchiveUnavailableError) as raised:
        gcs.target.publish_archives(_plan(), cancellation=cancellation)
    assert raised.value.archive_key == _plan().archive_keys[0]
    assert raised.value.__cause__ is failure
    gcs.download.assert_called_once()
    gcs.constructor.assert_not_called()


# endregion FUNC_test_gcs_download_failures_keep_the_archive_identity


# region FUNC_test_gcs_index_retries_only_transient_public_errors
# PURPOSE: Exercise public Google status codes, resumable-media responses, and requests transport errors through the bounded index operation.
@pytest.mark.parametrize(
    "error",
    [
        TooManyRequests("throttle"),
        ServiceUnavailable("unavailable"),
        InternalServerError("server"),
        InvalidResponse(SimpleNamespace(status_code=429), "throttle"),
        InvalidResponse(SimpleNamespace(status_code=503), "server"),
        requests.ConnectionError("connection lost"),
        requests.Timeout("timeout"),
        requests.exceptions.ChunkedEncodingError("partial body"),
    ],
)
def test_gcs_index_retries_only_transient_public_errors(gcs, cancellation, error):
    gcs.blob.upload_from_string.side_effect = [error, None]
    gcs.target.replace_index(_plan(), cancellation=cancellation)
    assert gcs.blob.upload_from_string.call_count == _RECOVERY_ATTEMPTS
    first, second = gcs.blob.upload_from_string.call_args_list
    assert first == second
    assert first.kwargs == {
        "content_type": "application/json",
        "timeout": 17,
        "retry": None,
    }


# endregion FUNC_test_gcs_index_retries_only_transient_public_errors


# region FUNC_test_gcs_index_auth_and_permanent_errors_are_not_retried
# PURPOSE: Propagate authentication, permission, integrity, and malformed-request failures on the first attempt.
@pytest.mark.parametrize(
    "error",
    [
        BadRequest("bad request"),
        Unauthorized("unauthorized"),
        Forbidden("forbidden"),
        NotFound("missing bucket"),
        PreconditionFailed("precondition"),
        GoogleAPICallError("unknown status"),
        InvalidResponse(SimpleNamespace(status_code=400), "bad request"),
        InvalidResponse(SimpleNamespace(status_code=None), "no status"),
        RefreshError("invalid identity"),
        DefaultCredentialsError("missing ADC"),
        DataCorruption(SimpleNamespace(status_code=200), "checksum"),
        requests.exceptions.InvalidURL("invalid URL"),
        ValueError("invalid payload"),
    ],
)
def test_gcs_index_auth_and_permanent_errors_are_not_retried(gcs, cancellation, error):
    gcs.blob.upload_from_string.side_effect = error
    with pytest.raises(type(error)) as raised:
        gcs.target.replace_index(_plan(), cancellation=cancellation)
    assert raised.value is error
    gcs.blob.upload_from_string.assert_called_once()


# endregion FUNC_test_gcs_index_auth_and_permanent_errors_are_not_retried


# region FUNC_test_gcs_index_exhaustion_propagates_last_failure
# PURPOSE: Keep index failure outside archive fallback and bound its total attempts.
def test_gcs_index_exhaustion_propagates_last_failure(gcs, cancellation):
    failures = [ServiceUnavailable(str(i)) for i in range(_ATTEMPTS)]
    gcs.blob.upload_from_string.side_effect = failures
    with pytest.raises(ServiceUnavailable) as raised:
        gcs.target.replace_index(_plan(), cancellation=cancellation)
    assert raised.value is failures[-1]
    assert gcs.blob.upload_from_string.call_count == _ATTEMPTS


# endregion FUNC_test_gcs_index_exhaustion_propagates_last_failure


# region FUNC_test_gcs_delete_accepts_not_found_after_transient_failure
# PURPOSE: Treat an absent stale object as success even after retrying a lost deletion response.
def test_gcs_delete_accepts_not_found_after_transient_failure(gcs, cancellation):
    gcs.blob.delete.side_effect = [
        ServiceUnavailable("lost response"),
        NotFound("gone"),
    ]
    gcs.target.cleanup_stale(_plan(), cancellation=cancellation)
    assert gcs.blob.delete.call_args_list == [
        call(timeout=17, retry=None),
        call(timeout=17, retry=None),
    ]


# endregion FUNC_test_gcs_delete_accepts_not_found_after_transient_failure


# region FUNC_test_gcs_delete_permanent_failure_is_not_retried
# PURPOSE: Stop stale cleanup immediately when GCS rejects deletion permanently.
def test_gcs_delete_permanent_failure_is_not_retried(gcs, cancellation):
    failure = Forbidden("denied")
    gcs.blob.delete.side_effect = failure
    with pytest.raises(Forbidden) as raised:
        gcs.target.cleanup_stale(_plan(), cancellation=cancellation)
    assert raised.value is failure
    gcs.blob.delete.assert_called_once()


# endregion FUNC_test_gcs_delete_permanent_failure_is_not_retried


class _CancelledError(Exception):
    """Model a cooperative cancellation observed at operation boundaries."""


# region FUNC_test_gcs_cancellation_prevents_adc_and_mutation
# PURPOSE: Refuse to start any mutation or credential discovery after cancellation was observed.
@pytest.mark.parametrize(
    "method", ["publish_archives", "replace_index", "cleanup_stale"]
)
def test_gcs_cancellation_prevents_adc_and_mutation(gcs, cancellation, method):
    cancellation.check.side_effect = _CancelledError
    with pytest.raises(_CancelledError):
        getattr(gcs.target, method)(_plan(), cancellation=cancellation)
    gcs.constructor.assert_not_called()
    gcs.download.assert_not_called()


# endregion FUNC_test_gcs_cancellation_prevents_adc_and_mutation


# region FUNC_test_gcs_cancelled_retry_does_not_wrap_or_start_another_upload
# PURPOSE: Stop upload backoff immediately and propagate cancellation instead of archive fallback.
def test_gcs_cancelled_retry_does_not_wrap_or_start_another_upload(gcs, cancellation):
    cancelled = False
    streams = []

    def check():
        if cancelled:
            raise _CancelledError

    def upload(stream, **kwargs):
        nonlocal cancelled
        streams.append(stream)
        stream.read()
        cancelled = True
        raise ServiceUnavailable("interrupted upload")

    cancellation.check.side_effect = check
    gcs.blob.upload_from_file.side_effect = upload
    with pytest.raises(_CancelledError):
        gcs.target.publish_archives(_plan(), cancellation=cancellation)
    gcs.blob.upload_from_file.assert_called_once()
    assert streams[0].closed


# endregion FUNC_test_gcs_cancelled_retry_does_not_wrap_or_start_another_upload


# region FUNC_test_gcs_cancellation_stops_later_archive_operations
# PURPOSE: Allow an in-flight upload to finish but prevent a later archive from starting after cancellation.
def test_gcs_cancellation_stops_later_archive_operations(gcs, cancellation):
    cancelled = False

    def check():
        if cancelled:
            raise _CancelledError

    def upload(stream, **kwargs):
        nonlocal cancelled
        stream.read()
        cancelled = True

    plan = _plan()
    plan = PublicationPlan(
        family=plan.family,
        releases=plan.releases,
        archives=(
            *plan.archives,
            Archive(
                key="l/second.zip", source_url="https://origin.test.invalid/second.zip"
            ),
        ),
        index=plan.index,
    )
    cancellation.check.side_effect = check
    gcs.blob.upload_from_file.side_effect = upload
    with pytest.raises(_CancelledError):
        gcs.target.publish_archives(plan, cancellation=cancellation)
    gcs.blob.upload_from_file.assert_called_once()
    gcs.download.assert_called_once()


# endregion FUNC_test_gcs_cancellation_stops_later_archive_operations
