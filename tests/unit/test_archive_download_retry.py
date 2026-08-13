# region MODULE_CONTRACT
# PURPOSE: Verify a verified archive download recovers from a transient connection failure without real HTTP.
# SCOPE:
# - Archive download retry recovery and full-byte delivery after a transient failure.
# - NOT: Real HTTP, target publication, index replacement, or stale cleanup.
# KEYWORDS: unit test, archive, download, retry
# endregion MODULE_CONTRACT

"""Unit tests for transient retry in the verified archive download."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Self

import pytest
import requests

from arduino_mirror.domain import Archive, IndexFamily
from arduino_mirror.infra.archive_tempfile import download_verified
from arduino_mirror.infra.retry import RetryPolicy

_CONNECTION_FAILURES_BEFORE_RECOVERY = 1


# region CLASS_CompleteResponse
# PURPOSE: Stand in for one streamed source response that yields complete archive bytes.
class CompleteResponse:
    """Minimal streamed response that yields a deterministic complete archive body."""

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


# region CLASS_NoCancellation
# PURPOSE: Supply the download's cancellation protocol without requesting a stop.
class NoCancellation:
    """Cooperative-cancellation double that never requests cancellation."""

    def check(self) -> None:
        """Allow the next download operation."""

    def interrupt_download(self):
        """Keep test downloads cooperative without interrupting."""
        return nullcontext()


# endregion CLASS_NoCancellation


# region FUNC_test_download_retries_connection_error_then_succeeds
# PURPOSE: Verify a transient connection failure during archive retrieval is retried and the archive is delivered whole.
def test_download_retries_connection_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One transient connection failure precedes a successful archive download."""
    state: dict[str, int] = {"calls": 0}

    def fake_get(*args: object, **kwargs: object) -> CompleteResponse:
        state["calls"] += 1
        if state["calls"] < _CONNECTION_FAILURES_BEFORE_RECOVERY + 1:
            raise requests.exceptions.ConnectionError("down")
        return CompleteResponse()

    monkeypatch.setattr("arduino_mirror.infra.archive_tempfile.requests.get", fake_get)
    archive = Archive(
        key="libraries/Servo.zip",
        source_url="https://origin.test.invalid/Servo.zip",
        size=len(b"complete"),
    )

    with download_verified(
        archive,
        600.0,
        IndexFamily.LIBRARIES,
        NoCancellation(),
        RetryPolicy(max_attempts=3, base_delay=0),
    ) as verified:
        assert verified.stream.read() == b"complete"

    assert state["calls"] == _CONNECTION_FAILURES_BEFORE_RECOVERY + 1


# endregion FUNC_test_download_retries_connection_error_then_succeeds


# region CLASS_BreakingResponse
# PURPOSE: Simulate a source transfer whose body breaks mid-stream with a chunked-encoding error.
class BreakingResponse:
    """Minimal streamed response that yields one chunk then drops the connection."""

    def __enter__(self) -> Self:
        """Return the streamed response context."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the fake response context."""

    def raise_for_status(self) -> None:
        """Accept the simulated HTTP response."""

    def iter_content(self, *, chunk_size: int) -> object:
        """Yield one partial chunk, then break the transfer mid-stream."""
        del chunk_size
        yield b"partial"
        raise requests.exceptions.ChunkedEncodingError("IncompleteRead")


# endregion CLASS_BreakingResponse


# region FUNC_test_download_retries_chunked_encoding_error_then_succeeds
# PURPOSE: Verify a mid-stream chunked-encoding error is retried and the archive is delivered whole.
def test_download_retries_chunked_encoding_error_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connection that breaks mid-transfer is retried with a fresh complete response."""
    state: dict[str, int] = {"calls": 0}

    def fake_get(*args: object, **kwargs: object) -> object:
        state["calls"] += 1
        if state["calls"] < _CONNECTION_FAILURES_BEFORE_RECOVERY + 1:
            return BreakingResponse()
        return CompleteResponse()

    monkeypatch.setattr("arduino_mirror.infra.archive_tempfile.requests.get", fake_get)
    archive = Archive(
        key="libraries/Servo.zip",
        source_url="https://origin.test.invalid/Servo.zip",
        size=len(b"complete"),
    )

    with download_verified(
        archive,
        600.0,
        IndexFamily.LIBRARIES,
        NoCancellation(),
        RetryPolicy(max_attempts=3, base_delay=0),
    ) as verified:
        assert verified.stream.read() == b"complete"

    assert state["calls"] == _CONNECTION_FAILURES_BEFORE_RECOVERY + 1


# endregion FUNC_test_download_retries_chunked_encoding_error_then_succeeds
