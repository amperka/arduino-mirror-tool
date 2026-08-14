# region MODULE_CONTRACT
# PURPOSE: Verify the retry engine, transient-error classifiers, and retry-configuration resolution without network I/O.
# SCOPE:
# - Engine retry/backoff/cancellation behavior, HTTP and S3 classifier branches, and retry config precedence.
# - NOT: HTTP requests, archive streaming, S3 client behavior, or application orchestration.
# KEYWORDS: unit test, retry, backoff, transient, classifier, configuration
# endregion MODULE_CONTRACT

"""Unit tests for the transient-network retry engine and classifiers."""

from __future__ import annotations

import logging
import random
import signal
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast

import pytest
import requests
import urllib3.exceptions
from minio.error import S3Error

from arduino_mirror.domain import IndexFamily
from arduino_mirror.entrypoints.config import (
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_RETRY_BASE_DELAY,
    Config,
)
from arduino_mirror.entrypoints.signals import PublicationCancelledError
from arduino_mirror.infra.retry import (
    RetryContext,
    RetryPolicy,
    is_transient_http,
    is_transient_s3,
    retry_call,
)
from tests.log_assertions import extra_fields


# region CLASS_CountingCancellation
# PURPOSE: Drive the retry engine's cancellation port from a test so cancellation timing is deterministic.
class CountingCancellation:
    """Cooperative-cancellation double that raises after a configured check count."""

    def __init__(self, raise_on_check: int) -> None:
        """Raise ``PublicationCancelledError`` once the check count reaches the limit."""
        self._raise_on = raise_on_check
        self._checks = 0

    def check(self) -> None:
        """Count one boundary and raise when the configured threshold is reached."""
        self._checks += 1
        if self._checks >= self._raise_on:
            raise PublicationCancelledError(signal.SIGTERM)

    def interrupt_download(self):
        """Keep test archive downloads cooperative without requesting a stop."""
        return nullcontext()


# endregion CLASS_CountingCancellation


# region CLASS_CapRandom
# PURPOSE: Make backoff deterministic by returning the upper bound so retry delays equal the policy ceiling.
class CapRandom(random.Random):
    """Deterministic RNG that returns the backoff ceiling instead of jittering."""

    def uniform(self, low: float, high: float) -> float:  # type: ignore[override]
        """Return ``high`` so a retry delay equals the policy ceiling for that attempt."""
        return high


# endregion CLASS_CapRandom


def _http_error(status: int) -> requests.exceptions.HTTPError:
    """Build one HTTPError carrying a response status for classifier tests."""
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(response=response)


def _s3_error(code: str, *, status: int | None = None) -> S3Error:
    """Build one S3Error carrying a code and optional response status for classifier tests."""
    return S3Error(
        cast(Any, SimpleNamespace(status=status)),
        code,
        "message",
        "resource",
        "req",
        "host",
    )


_TRANSIENT_FAILURES_BEFORE_RECOVERY = 2


# region FUNC_test_retry_call_succeeds_without_retry
# PURPOSE: Verify a first-attempt success returns immediately without sleeping or logging.
def test_retry_call_succeeds_without_retry() -> None:
    """A successful first attempt never invokes the sleep hook."""
    sleeps: list[float] = []

    def invoke() -> str:
        return "ok"

    assert (
        retry_call(invoke, is_retriable=is_transient_http, sleep=sleeps.append) == "ok"
    )
    assert sleeps == []


# endregion FUNC_test_retry_call_succeeds_without_retry


# region FUNC_test_retry_call_retries_transient_then_succeeds
# PURPOSE: Verify the engine retries classified transient failures until an attempt succeeds.
def test_retry_call_retries_transient_then_succeeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two transient failures precede a successful third attempt with two scheduled-retry records."""
    attempts: list[int] = []

    def invoke() -> str:
        attempts.append(1)
        if len(attempts) < _TRANSIENT_FAILURES_BEFORE_RECOVERY + 1:
            raise requests.exceptions.ConnectionError("down")
        return "ok"

    caplog.set_level(logging.DEBUG, logger="arduino_mirror.infra.retry")
    result = retry_call(
        invoke,
        is_retriable=is_transient_http,
        policy=RetryPolicy(max_attempts=5, base_delay=0),
        context=RetryContext(sleep=lambda _: None),
    )
    assert result == "ok"
    assert len(attempts) == _TRANSIENT_FAILURES_BEFORE_RECOVERY + 1
    scheduled = [
        record for record in caplog.records if record.getMessage() == "RETRY_SCHEDULED"
    ]
    assert [extra_fields(record)["attempt"] for record in scheduled] == list(
        range(1, _TRANSIENT_FAILURES_BEFORE_RECOVERY + 1)
    )


# endregion FUNC_test_retry_call_retries_transient_then_succeeds


# region FUNC_test_retry_call_propagates_non_retriable_immediately
# PURPOSE: Verify a non-transient failure propagates after a single attempt without retry.
def test_retry_call_propagates_non_retriable_immediately() -> None:
    """A permanent error is never retried."""
    attempts: list[int] = []

    def invoke() -> str:
        attempts.append(1)
        raise ValueError("permanent")

    with pytest.raises(ValueError, match="permanent"):
        retry_call(
            invoke,
            is_retriable=is_transient_http,
            policy=RetryPolicy(max_attempts=5, base_delay=0),
            context=RetryContext(sleep=lambda _: None),
        )
    assert len(attempts) == 1


# endregion FUNC_test_retry_call_propagates_non_retriable_immediately


# region FUNC_test_retry_call_exhausts_and_raises_last
# PURPOSE: Verify the engine retries up to the attempt budget and then propagates the final failure.
def test_retry_call_exhausts_and_raises_last(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A persistent transient failure is retried to the budget and then propagated."""
    attempts: list[int] = []

    def invoke() -> str:
        attempts.append(1)
        raise requests.exceptions.ConnectionError("down")

    caplog.set_level(logging.DEBUG, logger="arduino_mirror.infra.retry")
    policy = RetryPolicy(max_attempts=3, base_delay=0)
    with pytest.raises(requests.exceptions.ConnectionError):
        retry_call(
            invoke,
            is_retriable=is_transient_http,
            policy=policy,
            context=RetryContext(sleep=lambda _: None),
        )
    assert len(attempts) == policy.max_attempts
    exhausted = [
        record for record in caplog.records if record.getMessage() == "RETRY_EXHAUSTED"
    ]
    assert exhausted
    assert extra_fields(exhausted[0])["attempts"] == policy.max_attempts


# endregion FUNC_test_retry_call_exhausts_and_raises_last


# region FUNC_test_retry_call_applies_exponential_backoff
# PURPOSE: Verify the engine backs off exponentially with full jitter between failed attempts.
def test_retry_call_applies_exponential_backoff() -> None:
    """The total slept delay equals the first two capped exponential ceilings."""
    sleeps: list[float] = []

    def invoke() -> str:
        raise requests.exceptions.ConnectionError("down")

    with pytest.raises(requests.exceptions.ConnectionError):
        retry_call(
            invoke,
            is_retriable=is_transient_http,
            policy=RetryPolicy(max_attempts=3, base_delay=1.0, max_delay=30.0),
            context=RetryContext(sleep=sleeps.append, rng=CapRandom()),
        )
    assert sum(sleeps) == pytest.approx(3.0)


# endregion FUNC_test_retry_call_applies_exponential_backoff


# region FUNC_test_retry_call_stops_on_cancellation_between_attempts
# PURPOSE: Verify a cancellation requested between attempts aborts the retry loop.
def test_retry_call_stops_on_cancellation_between_attempts() -> None:
    """A cancellation at the next attempt boundary stops retrying after one failed attempt."""
    cancellation = CountingCancellation(raise_on_check=3)
    attempts: list[int] = []

    def invoke() -> str:
        attempts.append(1)
        raise requests.exceptions.ConnectionError("down")

    with pytest.raises(PublicationCancelledError):
        retry_call(
            invoke,
            is_retriable=is_transient_http,
            policy=RetryPolicy(max_attempts=5, base_delay=0),
            context=RetryContext(cancellation=cancellation, sleep=lambda _: None),
        )
    assert len(attempts) == 1


# endregion FUNC_test_retry_call_stops_on_cancellation_between_attempts


# region FUNC_test_retry_call_sleep_is_interruptible_by_cancellation
# PURPOSE: Verify the inter-attempt backoff aborts promptly when cancellation is requested mid-sleep.
def test_retry_call_sleep_is_interruptible_by_cancellation() -> None:
    """Cancellation during the first backoff chunk stops retrying after one slept chunk."""
    cancellation = CountingCancellation(raise_on_check=3)
    sleeps: list[float] = []
    attempts: list[int] = []

    def invoke() -> str:
        attempts.append(1)
        raise requests.exceptions.ConnectionError("down")

    with pytest.raises(PublicationCancelledError):
        retry_call(
            invoke,
            is_retriable=is_transient_http,
            policy=RetryPolicy(max_attempts=5, base_delay=10.0),
            context=RetryContext(
                cancellation=cancellation,
                sleep=sleeps.append,
                rng=CapRandom(),
            ),
        )
    assert len(attempts) == 1
    assert sleeps == [pytest.approx(0.2)]


# endregion FUNC_test_retry_call_sleep_is_interruptible_by_cancellation


# region FUNC_test_retry_policy_rejects_invalid_schedule
# PURPOSE: Verify the policy rejects schedules that cannot represent a valid bounded retry.
def test_retry_policy_rejects_invalid_schedule() -> None:
    """Non-positive attempts and negative delays are rejected at construction."""
    with pytest.raises(ValueError, match="at least 1"):
        RetryPolicy(max_attempts=0)
    with pytest.raises(ValueError, match="non-negative"):
        RetryPolicy(base_delay=-1.0)


# endregion FUNC_test_retry_policy_rejects_invalid_schedule


# region FUNC_test_retry_policy_delay_for_exponential_capped
# PURPOSE: Verify the policy computes exponential ceilings capped at the configured maximum.
def test_retry_policy_delay_for_exponential_capped() -> None:
    """The delay ceiling grows exponentially until it hits the configured cap."""
    policy = RetryPolicy(max_attempts=10, base_delay=1.0, max_delay=30.0)
    assert policy.delay_for(1) == 1.0
    assert policy.delay_for(2) == policy.base_delay * policy.multiplier
    assert policy.delay_for(5) == policy.base_delay * policy.multiplier**4
    assert policy.delay_for(6) == policy.max_delay
    assert policy.delay_for(0) == 0.0


# endregion FUNC_test_retry_policy_delay_for_exponential_capped


# region FUNC_test_is_transient_http_classifies_transport_and_server
# PURPOSE: Verify the HTTP classifier retries transport, server, and throttle failures.
def test_is_transient_http_classifies_transport_and_server() -> None:
    """Transport, timeout, mid-stream, 5xx, and 429 failures retry; 4xx, malformed-request, and non-HTTP errors do not."""
    assert is_transient_http(requests.exceptions.ConnectionError("x")) is True
    assert is_transient_http(requests.exceptions.Timeout("x")) is True
    assert is_transient_http(requests.exceptions.SSLError("x")) is True
    assert is_transient_http(requests.exceptions.ChunkedEncodingError("broken")) is True
    assert is_transient_http(_http_error(500)) is True
    assert is_transient_http(_http_error(503)) is True
    assert is_transient_http(_http_error(429)) is True
    assert is_transient_http(_http_error(404)) is False
    assert is_transient_http(_http_error(403)) is False
    assert is_transient_http(requests.exceptions.HTTPError()) is False
    assert is_transient_http(requests.exceptions.InvalidURL("bad")) is False
    assert is_transient_http(requests.exceptions.MissingSchema("bad")) is False
    assert is_transient_http(ValueError("x")) is False
    assert is_transient_http(PublicationCancelledError(signal.SIGTERM)) is False


# endregion FUNC_test_is_transient_http_classifies_transport_and_server


# region FUNC_test_is_transient_s3_classifies_server_and_connection
# PURPOSE: Verify the S3 classifier retries transient S3 codes, 5xx status, and connection failures.
def test_is_transient_s3_classifies_server_and_connection() -> None:
    """Transient codes, 5xx responses, and transport failures retry; permanent codes and misconfiguration do not."""
    assert is_transient_s3(_s3_error("InternalError")) is True
    assert is_transient_s3(_s3_error("ServiceUnavailable")) is True
    assert is_transient_s3(_s3_error("SlowDown")) is True
    assert is_transient_s3(_s3_error("RequestThrottled")) is True
    assert is_transient_s3(_s3_error("UnknownCode", status=503)) is True
    assert is_transient_s3(_s3_error("UnknownCode", status=429)) is True
    assert is_transient_s3(_s3_error("AccessDenied", status=403)) is False
    assert is_transient_s3(_s3_error("NoSuchKey", status=404)) is False
    assert (
        is_transient_s3(
            urllib3.exceptions.MaxRetryError(
                cast(Any, None), "http://x", reason=ConnectionError("x")
            )
        )
        is True
    )
    assert (
        is_transient_s3(
            urllib3.exceptions.ProtocolError("conn", ConnectionError("broken"))
        )
        is True
    )
    assert is_transient_s3(urllib3.exceptions.LocationValueError("bad host")) is False
    assert is_transient_s3(RuntimeError("x")) is False


# endregion FUNC_test_is_transient_s3_classifies_server_and_connection


# region FUNC_test_config_retry_defaults
# PURPOSE: Verify retry configuration resolves to the documented defaults.
def test_config_retry_defaults() -> None:
    """Omitted retry options resolve to 10 attempts and a 1.0-second base delay."""
    config = Config.from_values(family=IndexFamily.PACKAGES, values={}, environment={})
    assert config.retry_attempts == DEFAULT_RETRY_ATTEMPTS
    assert config.retry_base_delay == DEFAULT_RETRY_BASE_DELAY


# endregion FUNC_test_config_retry_defaults


# region FUNC_test_config_retry_cli_wins_over_environment
# PURPOSE: Verify an explicit CLI retry value takes precedence over the environment.
def test_config_retry_cli_wins_over_environment() -> None:
    """Explicit CLI retry options override matching environment variables."""
    configured_attempts = 4
    configured_base_delay = 2.5
    config = Config.from_values(
        family=IndexFamily.PACKAGES,
        values={
            "retry_attempts": configured_attempts,
            "retry_base_delay": configured_base_delay,
        },
        environment={"RETRY_ATTEMPTS": "9"},
    )
    assert config.retry_attempts == configured_attempts
    assert config.retry_base_delay == configured_base_delay


# endregion FUNC_test_config_retry_cli_wins_over_environment


# region FUNC_test_config_retry_environment_fallback
# PURPOSE: Verify retry options fall back to the environment when the CLI omits them.
def test_config_retry_environment_fallback() -> None:
    """Environment retry variables apply when the CLI does not supply retry options."""
    env_attempts = 7
    env_base_delay = 0.5
    config = Config.from_values(
        family=IndexFamily.PACKAGES,
        values={},
        environment={
            "RETRY_ATTEMPTS": str(env_attempts),
            "RETRY_BASE_DELAY": str(env_base_delay),
        },
    )
    assert config.retry_attempts == env_attempts
    assert config.retry_base_delay == env_base_delay


# endregion FUNC_test_config_retry_environment_fallback
