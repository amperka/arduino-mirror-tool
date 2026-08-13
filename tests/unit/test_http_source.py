# region MODULE_CONTRACT
# PURPOSE: Verify the HTTP index source decodes indexes and recovers from transient failures without a real network.
# SCOPE:
# - Index retrieval, transient retry recovery, permanent-error propagation, and payload validation.
# - NOT: Real HTTP, selection, storage, or application orchestration.
# KEYWORDS: unit test, HTTP, index source, retry
# endregion MODULE_CONTRACT

"""Unit tests for the HTTP index source and its transient retry behavior."""

from __future__ import annotations

from collections.abc import Sequence

import pytest
import requests

from arduino_mirror.domain import IndexFamily
from arduino_mirror.infra.http_source import HttpIndexSource
from arduino_mirror.infra.retry import RetryPolicy

_PACKAGES_URL = "https://origin.test.invalid/package_index.json"
_HTTP_ERROR_STATUS_MIN = 400


# region CLASS_FakeResponse
# PURPOSE: Stand in for one Requests response so the index source is exercised without HTTP I/O.
class FakeResponse:
    """Minimal response that raises for an error status and decodes a configured payload."""

    def __init__(self, payload: object, *, status: int = 200) -> None:
        """Store the decoded payload and the response status."""
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        """Raise an HTTPError with a status when the configured status is an error."""
        if self.status_code >= _HTTP_ERROR_STATUS_MIN:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.exceptions.HTTPError(response=response)

    def json(self) -> object:
        """Return the payload or raise it when it models a decode failure."""
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


# endregion CLASS_FakeResponse


def _patch_get(
    monkeypatch: pytest.MonkeyPatch, items: Sequence[object]
) -> dict[str, int]:
    """Patch ``requests.get`` to return or raise the supplied items in order."""
    state: dict[str, int] = {"count": 0}

    def fake_get(*args: object, **kwargs: object) -> FakeResponse:
        index = state["count"]
        state["count"] += 1
        item = items[index]
        if isinstance(item, BaseException):
            raise item
        if not isinstance(item, FakeResponse):
            msg = "test supplied a non-response item"
            raise TypeError(msg)
        return item

    monkeypatch.setattr("arduino_mirror.infra.http_source.requests.get", fake_get)
    return state


# region FUNC_test_fetch_decodes_index
# PURPOSE: Verify the index source returns a decoded JSON object for a successful response.
def test_fetch_decodes_index(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful response decodes to its JSON object payload."""
    state = _patch_get(monkeypatch, [FakeResponse({"packages": []})])
    source = HttpIndexSource(urls={IndexFamily.PACKAGES: _PACKAGES_URL})

    assert source.fetch(IndexFamily.PACKAGES) == {"packages": []}
    assert state["count"] == 1


# endregion FUNC_test_fetch_decodes_index


# region FUNC_test_fetch_retries_transient_then_succeeds
# PURPOSE: Verify the index source retries transient transport failures until a response succeeds.
def test_fetch_retries_transient_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two transient failures precede a successful third retrieval."""
    items = [
        requests.exceptions.ConnectionError("down"),
        requests.exceptions.Timeout("slow"),
        FakeResponse({"packages": []}),
    ]
    state = _patch_get(monkeypatch, items)
    source = HttpIndexSource(
        urls={IndexFamily.PACKAGES: _PACKAGES_URL},
        retry_policy=RetryPolicy(max_attempts=5, base_delay=0),
    )

    assert source.fetch(IndexFamily.PACKAGES) == {"packages": []}
    assert state["count"] == len(items)


# endregion FUNC_test_fetch_retries_transient_then_succeeds


# region FUNC_test_fetch_propagates_permanent_http_error_without_retry
# PURPOSE: Verify a permanent HTTP error propagates immediately without consuming the retry budget.
def test_fetch_propagates_permanent_http_error_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 404 response is not retried."""
    state = _patch_get(monkeypatch, [FakeResponse(None, status=404)])
    source = HttpIndexSource(
        urls={IndexFamily.PACKAGES: _PACKAGES_URL},
        retry_policy=RetryPolicy(max_attempts=5, base_delay=0),
    )

    with pytest.raises(requests.exceptions.HTTPError):
        source.fetch(IndexFamily.PACKAGES)
    assert state["count"] == 1


# endregion FUNC_test_fetch_propagates_permanent_http_error_without_retry


# region FUNC_test_fetch_invalid_payload_not_retried
# PURPOSE: Verify an invalid index payload propagates without retrying the request.
def test_fetch_invalid_payload_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A JSON decode failure surfaces after a single retrieval."""
    state = _patch_get(monkeypatch, [FakeResponse(ValueError("bad json"))])
    source = HttpIndexSource(
        urls={IndexFamily.PACKAGES: _PACKAGES_URL},
        retry_policy=RetryPolicy(max_attempts=5, base_delay=0),
    )

    with pytest.raises(ValueError, match="bad json"):
        source.fetch(IndexFamily.PACKAGES)
    assert state["count"] == 1


# endregion FUNC_test_fetch_invalid_payload_not_retried
