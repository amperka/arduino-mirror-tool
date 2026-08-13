# region MODULE_CONTRACT
# PURPOSE: Prove an interruption signal immediately aborts an active archive download and closes its temporary archive stream.
# SCOPE: Signal-driven archive-transfer cancellation and temporary-resource cleanup.
# NOT: Real HTTP, target publication, index replacement, or stale cleanup.
# KEYWORDS: unit test, signal, download, temporary file, cancellation
# endregion MODULE_CONTRACT

"""Unit tests for interruptible verified archive downloads."""

from __future__ import annotations

import os
import signal
import tempfile
from typing import Self

import pytest

from arduino_mirror.domain import Archive, IndexFamily
from arduino_mirror.entrypoints.signals import (
    PublicationCancelledError,
    SignalCancellation,
)
from arduino_mirror.infra.archive_tempfile import download_verified


# region CLASS_SignalSendingResponse
# PURPOSE: Simulate a source transfer that receives SIGTERM after it has written partial temporary archive bytes.
class SignalSendingResponse:
    """Minimal streamed response that sends SIGTERM from archive iteration."""

    def __enter__(self) -> Self:
        """Return the streamed response context."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the fake response context."""

    def raise_for_status(self) -> None:
        """Accept the simulated HTTP response."""

    def iter_content(self, *, chunk_size: int) -> object:
        """Write one partial chunk, then request immediate cancellation."""
        del chunk_size
        yield b"partial"
        os.kill(os.getpid(), signal.SIGTERM)
        yield b"unreachable"


# endregion CLASS_SignalSendingResponse


# region CLASS_CompleteResponse
# PURPOSE: Simulate a complete source transfer for verifying cleanup after a successful archive publication handoff.
class CompleteResponse:
    """Minimal streamed response that yields complete archive bytes."""

    def __enter__(self) -> Self:
        """Return the streamed response context."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the fake response context."""

    def raise_for_status(self) -> None:
        """Accept the simulated HTTP response."""

    def iter_content(self, *, chunk_size: int) -> object:
        """Yield the complete deterministic archive body."""
        del chunk_size
        yield b"complete"


# endregion CLASS_CompleteResponse


# region FUNC_test_sigterm_aborts_active_download_and_closes_temporary_stream
# PURPOSE: Verify SIGTERM escapes archive streaming immediately and closes the partially written temporary stream.
def test_sigterm_aborts_active_download_and_closes_temporary_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The download boundary closes temporary bytes while preserving the signal cancellation result."""
    response = SignalSendingResponse()
    temporary_files = []
    original_spooled_file = tempfile.SpooledTemporaryFile

    def record_temporary_file(*args, **kwargs):
        temporary = original_spooled_file(*args, **kwargs)
        temporary_files.append(temporary)
        return temporary

    monkeypatch.setattr(
        "arduino_mirror.infra.archive_tempfile.requests.get",
        lambda *_args, **_kwargs: response,
    )
    monkeypatch.setattr(
        "arduino_mirror.infra.archive_tempfile.tempfile.SpooledTemporaryFile",
        record_temporary_file,
    )
    archive = Archive(
        key="libraries/Servo.zip",
        source_url="https://origin.test.invalid/Servo.zip",
    )

    with (
        SignalCancellation() as cancellation,
        pytest.raises(PublicationCancelledError),
        download_verified(archive, 600.0, IndexFamily.LIBRARIES, cancellation),
    ):
        pytest.fail("cancelled download must not yield a verified stream")

    assert len(temporary_files) == 1
    assert temporary_files[0].closed


# endregion FUNC_test_sigterm_aborts_active_download_and_closes_temporary_stream


# region FUNC_test_successful_download_closes_temporary_stream
# PURPOSE: Verify a verified archive stream closes after a target has consumed it successfully.
def test_successful_download_closes_temporary_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed download leaves no open temporary archive stream."""
    temporary_files = []
    original_spooled_file = tempfile.SpooledTemporaryFile

    def record_temporary_file(*args, **kwargs):
        temporary = original_spooled_file(*args, **kwargs)
        temporary_files.append(temporary)
        return temporary

    monkeypatch.setattr(
        "arduino_mirror.infra.archive_tempfile.requests.get",
        lambda *_args, **_kwargs: CompleteResponse(),
    )
    monkeypatch.setattr(
        "arduino_mirror.infra.archive_tempfile.tempfile.SpooledTemporaryFile",
        record_temporary_file,
    )
    archive = Archive(
        key="libraries/Servo.zip",
        source_url="https://origin.test.invalid/Servo.zip",
        size=len(b"complete"),
    )

    with (
        SignalCancellation() as cancellation,
        download_verified(
            archive, 600.0, IndexFamily.LIBRARIES, cancellation
        ) as verified,
    ):
        assert verified.stream.read() == b"complete"

    assert len(temporary_files) == 1
    assert temporary_files[0].closed


# endregion FUNC_test_successful_download_closes_temporary_stream
