# region MODULE_CONTRACT
# PURPOSE: Verify the S3-compatible target maps logical family keys to the documented MinIO client operations without a real storage service.
# SCOPE:
# - Prefix-scoped list, upload, index replacement, and stale cleanup calls.
# - NOT: A real S3 service or application orchestration.
# KEYWORDS: unit test, S3, MinIO, publication target
# endregion MODULE_CONTRACT

"""Unit tests for the MinIO-backed publication target."""

from __future__ import annotations

import io
import logging
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest.mock import ANY

import pytest
from minio.error import S3Error

from arduino_mirror.domain import Archive, IndexFamily, PublicationPlan
from arduino_mirror.infra.archive_tempfile import VerifiedArchive
from arduino_mirror.infra.retry import RetryPolicy
from arduino_mirror.infra.s3_target import S3PublicationTarget
from tests.log_assertions import extra_fields


class _NoCancellation:
    """Supply the target's cancellation protocol without requesting a stop."""

    def check(self) -> None:
        """Allow the next test operation."""

    def interrupt_download(self):
        """Keep test downloads cooperative."""
        return nullcontext()


_NO_CANCELLATION = _NoCancellation()

_TRANSIENT_PUT_FAILURES = 2


# region CLASS_FakeMinio
# PURPOSE: Record the MinIO client surface that the target uses so key scoping is deterministic in a unit test.
class FakeMinio:
    """Minimal recording replacement for the documented MinIO client calls."""

    instances: ClassVar[list[FakeMinio]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Record construction and expose per-instance operation lists."""
        self.arguments = args
        self.keyword_arguments = kwargs
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.objects = [
            SimpleNamespace(object_name="managed/l/obsolete.zip", size=3),
            SimpleNamespace(
                object_name="managed/l/libraries/library_index.json", size=9
            ),
            SimpleNamespace(object_name="managed/p/protected.tar.bz2", size=9),
        ]
        type(self).instances.append(self)

    def list_objects(self, *args: Any, **kwargs: Any) -> list[SimpleNamespace]:
        """Record a prefix list and return configured object summaries."""
        self.calls.append(("list_objects", args, kwargs))
        return self.objects

    def put_object(self, *args: Any, **kwargs: Any) -> None:
        """Record index replacement arguments."""
        self.calls.append(("put_object", args, kwargs))

    def remove_object(self, *args: Any, **kwargs: Any) -> None:
        """Record stale-object deletion arguments."""
        self.calls.append(("remove_object", args, kwargs))


# endregion CLASS_FakeMinio


# region FUNC__verified_archive
# PURPOSE: Supply deterministic verified archive bytes to the S3 adapter without HTTP or filesystem I/O.
@contextmanager
def _verified_archive(*_: object):
    """Yield deterministic archive bytes through the temporary-stream contract."""
    with io.BytesIO(b"archive") as stream:
        yield VerifiedArchive(stream=stream, size=len(stream.getvalue()))


# endregion FUNC__verified_archive


# region FUNC_test_s3_target_uses_only_family_owned_keys
# PURPOSE: Verify the target prefixes every MinIO operation and never treats another family key as a library stale key.
def test_s3_target_uses_only_family_owned_keys(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The MinIO adapter maps logical keys under configured prefix and family paths."""
    caplog.set_level(logging.DEBUG, logger="arduino_mirror.infra.s3_target")
    FakeMinio.instances.clear()
    monkeypatch.setattr("arduino_mirror.infra.s3_target.Minio", FakeMinio)
    monkeypatch.setattr(
        "arduino_mirror.infra.s3_target.download_verified", _verified_archive
    )
    target = S3PublicationTarget(
        bucket="mirror",
        endpoint="http://s3.test.invalid",
        access_key="access",
        secret_key="secret",
        index_key="l/libraries/library_index.json",
        prefix="managed",
    )
    plan = PublicationPlan(
        family=IndexFamily.LIBRARIES,
        releases=("Servo@1.0.0",),
        archives=(
            Archive(
                key="l/Servo-1.0.0.zip",
                source_url="https://origin.test.invalid/Servo-1.0.0.zip",
            ),
        ),
        index={"libraries": []},
        stale_keys=("l/obsolete.zip",),
    )

    reconciled = target.reconcile(plan)
    assert reconciled.stale_keys == ("l/obsolete.zip",)
    target.publish_archives(reconciled, cancellation=_NO_CANCELLATION)
    target.replace_index(reconciled, cancellation=_NO_CANCELLATION)
    target.cleanup_stale(reconciled, cancellation=_NO_CANCELLATION)

    client = FakeMinio.instances[0]
    assert client.arguments == ("s3.test.invalid",)
    assert client.keyword_arguments["secure"] is False
    assert client.calls[0] == (
        "list_objects",
        ("mirror",),
        {"prefix": "managed/l/", "recursive": True},
    )
    assert client.calls[1] == (
        "put_object",
        ("mirror", "managed/l/Servo-1.0.0.zip", ANY),
        {"length": len(b"archive")},
    )
    assert client.calls[2][0] == "put_object"
    assert client.calls[2][1][0:2] == (
        "mirror",
        "managed/l/libraries/library_index.json",
    )
    assert client.calls[3] == (
        "remove_object",
        ("mirror", "managed/l/obsolete.zip"),
        {},
    )
    trace_records = [
        record for record in caplog.records if record.levelno == logging.DEBUG
    ]
    assert [record.getMessage() for record in trace_records] == [
        "TARGET_ARCHIVE_KEYS_LISTED",
        "TARGET_ARCHIVES_PUBLISHED",
        "TARGET_INDEX_REPLACED",
        "TARGET_STALE_CLEANED",
    ]
    assert [extra_fields(record) for record in trace_records] == [
        {"archive_count": 1, "family": IndexFamily.LIBRARIES},
        {"archive_count": 1, "family": IndexFamily.LIBRARIES},
        {
            "family": IndexFamily.LIBRARIES,
            "index_key": "l/libraries/library_index.json",
        },
        {"family": IndexFamily.LIBRARIES, "stale_count": 1},
    ]


# endregion FUNC_test_s3_target_uses_only_family_owned_keys


# region FUNC_test_s3_target_skips_archives_confirmed_by_one_listing
# PURPOSE: Verify an S3 target avoids source downloads when one family listing confirms every selected archive size.
def test_s3_target_skips_archives_confirmed_by_one_listing(monkeypatch) -> None:
    """A single S3 listing confirms multiple existing archives without per-object requests."""
    FakeMinio.instances.clear()
    monkeypatch.setattr("arduino_mirror.infra.s3_target.Minio", FakeMinio)
    monkeypatch.setattr(
        "arduino_mirror.infra.s3_target.download_verified",
        lambda *_: pytest.fail("already-published archive was downloaded"),
    )
    target = S3PublicationTarget(
        bucket="mirror",
        access_key="access",
        secret_key="secret",
        index_key="l/libraries/library_index.json",
        prefix="managed",
    )
    client = FakeMinio.instances[0]
    client.objects = [
        SimpleNamespace(object_name="managed/l/size.zip", size=4),
        SimpleNamespace(object_name="managed/l/other.zip", size=1),
    ]
    plan = PublicationPlan(
        family=IndexFamily.LIBRARIES,
        releases=("Size@1.0.0", "Other@1.0.0"),
        archives=(
            Archive(
                key="l/size.zip",
                source_url="https://origin.test.invalid/size.zip",
                size=4,
            ),
            Archive(
                key="l/other.zip",
                source_url="https://origin.test.invalid/other.zip",
                size=1,
            ),
        ),
        index={"libraries": []},
    )

    reconciled = target.reconcile(plan)
    target.publish_archives(reconciled, cancellation=_NO_CANCELLATION)

    assert reconciled.archives_to_publish == ()
    assert [call[0] for call in client.calls] == ["list_objects"]


# endregion FUNC_test_s3_target_skips_archives_confirmed_by_one_listing


# region FUNC_test_s3_target_retries_transient_put_then_succeeds
# PURPOSE: Verify the S3 target retries a transient upload failure and then publishes the archive.
def test_s3_target_retries_transient_put_then_succeeds(monkeypatch) -> None:
    """A transient InternalError on upload is retried until the archive is published."""

    put_attempts: list[int] = []

    class TransientThenSuccessMinio(FakeMinio):
        def put_object(self, *args: Any, **kwargs: Any) -> None:
            put_attempts.append(1)
            if len(put_attempts) < _TRANSIENT_PUT_FAILURES + 1:
                raise S3Error(
                    cast(Any, SimpleNamespace(status=503)),
                    "InternalError",
                    "m",
                    "r",
                    "ri",
                    "hi",
                )
            self.calls.append(("put_object", args, kwargs))

    FakeMinio.instances.clear()
    monkeypatch.setattr(
        "arduino_mirror.infra.s3_target.Minio", TransientThenSuccessMinio
    )
    monkeypatch.setattr(
        "arduino_mirror.infra.s3_target.download_verified", _verified_archive
    )
    target = S3PublicationTarget(
        bucket="mirror",
        access_key="access",
        secret_key="secret",
        index_key="l/libraries/library_index.json",
        prefix="managed",
        retry_policy=RetryPolicy(max_attempts=5, base_delay=0),
    )
    plan = PublicationPlan(
        family=IndexFamily.LIBRARIES,
        releases=("Servo@1.0.0",),
        archives=(
            Archive(
                key="l/Servo-1.0.0.zip",
                source_url="https://origin.test.invalid/Servo-1.0.0.zip",
            ),
        ),
        index={"libraries": []},
    )

    reconciled = target.reconcile(plan)
    target.publish_archives(reconciled, cancellation=_NO_CANCELLATION)

    client = TransientThenSuccessMinio.instances[0]
    assert len(put_attempts) == _TRANSIENT_PUT_FAILURES + 1
    assert [call[0] for call in client.calls] == ["list_objects", "put_object"]


# endregion FUNC_test_s3_target_retries_transient_put_then_succeeds
