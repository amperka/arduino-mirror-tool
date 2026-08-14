# region MODULE_CONTRACT
# PURPOSE: Provide a transport-agnostic retry engine and transient-error classifiers so publication adapters recover from transient network failures instead of crashing the workflow.
# SCOPE:
# - Exponential-backoff retry engine with full jitter, an immutable retry policy, and per-library transient-error classifiers (HTTP and S3-compatible).
# - NOT: HTTP requests, archive streaming, S3 client construction, selection logic, or cancellation signal handling.
# INVARIANTS: The engine retries only when the caller-supplied classifier returns True; non-transient exceptions and cooperative-cancellation exceptions propagate immediately. The engine performs no I/O beyond the caller-supplied invoke and an injectable sleep.
# KEYWORDS: retry, backoff, transient, network, classifier, jitter
# endregion MODULE_CONTRACT

"""Transient-network retry engine and classifiers for infrastructure adapters."""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, NotRequired, TypedDict, Unpack, overload

import requests
import urllib3.exceptions
from minio.error import S3Error

if TYPE_CHECKING:
    from collections.abc import Callable

    from arduino_mirror.domain import PublicationCancellation

__all__ = [
    "DEFAULT_RETRY_POLICY",
    "RetryContext",
    "RetryPolicy",
    "is_transient_http",
    "is_transient_s3",
    "retry_call",
]

logger = logging.getLogger(__name__)

_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR_MIN = 500
_HTTP_SERVER_ERROR_MAX = 599
_SLEEP_STEP_SECONDS = 0.2

_TRANSIENT_S3_CODES = frozenset(
    {
        "InternalError",
        "ServiceUnavailable",
        "SlowDown",
        "RequestThrottled",
        "Throttling",
        "RequestTimeout",
        "RequestTimeTooSkewed",
    }
)

# ``HTTPError`` subclasses that still represent a transport-level failure rather
# than an HTTP status, e.g. a truncated gzip body decoded from a 200 response.
_TRANSPORT_HTTP_ERRORS = (requests.exceptions.ContentDecodingError,)

# Definitively-permanent ``requests`` errors that never reflect a transient
# condition and therefore must not consume the retry budget.
_PERMANENT_HTTP_ERRORS = (
    requests.exceptions.InvalidHeader,
    requests.exceptions.InvalidJSONError,
    requests.exceptions.InvalidSchema,
    requests.exceptions.InvalidURL,
    requests.exceptions.MissingSchema,
    requests.exceptions.StreamConsumedError,
    requests.exceptions.TooManyRedirects,
    requests.exceptions.URLRequired,
)

# urllib3 errors that reflect a misconfiguration (bad endpoint, header, or host)
# rather than a transient transport failure.
_PERMANENT_URLLIB3_ERRORS = (
    urllib3.exceptions.HeaderParsingError,
    urllib3.exceptions.HostChangedError,
    urllib3.exceptions.InvalidHeader,
    urllib3.exceptions.LocationValueError,
)

_module_random = random.Random()  # noqa: S311  # non-cryptographic backoff jitter


# region CLASS_RetryPolicy
# PURPOSE: Describe one exponential-backoff schedule as an immutable value consumed by the retry engine and operator configuration.
@dataclass(frozen=True)
class RetryPolicy:
    """Exponential-backoff retry schedule capped at ``max_delay``."""

    max_attempts: int = 10
    base_delay: float = 1.0
    max_delay: float = 30.0
    multiplier: float = 2.0

    def __post_init__(self) -> None:
        """Reject schedules that cannot represent a valid bounded retry."""
        if self.max_attempts < 1:
            msg = "max_attempts must be at least 1"
            raise ValueError(msg)
        if self.base_delay < 0:
            msg = "base_delay must be non-negative"
            raise ValueError(msg)
        if self.max_delay < 0:
            msg = "max_delay must be non-negative"
            raise ValueError(msg)

    # region METHOD_delay_for
    # PURPOSE: Return the capped exponential backoff ceiling for one failed attempt.
    def delay_for(self, attempt: int) -> float:
        """Return the maximum delay before the retry that follows a failed ``attempt``."""
        if attempt < 1:
            return 0.0
        return min(self.base_delay * self.multiplier ** (attempt - 1), self.max_delay)

    # endregion METHOD_delay_for


# endregion CLASS_RetryPolicy

DEFAULT_RETRY_POLICY = RetryPolicy()


class _RetryContextArguments(TypedDict):
    """Legacy keyword representation of optional retry controls."""

    cancellation: NotRequired[PublicationCancellation | None]
    sleep: NotRequired[Callable[[float], None]]
    rng: NotRequired[random.Random | None]


# region CLASS_RetryContext
# PURPOSE: Group optional retry execution controls so production and deterministic callers share one engine contract.
@dataclass(frozen=True)
class RetryContext:
    """Optional cancellation, sleep, and jitter dependencies for one retry call."""

    cancellation: PublicationCancellation | None = None
    sleep: Callable[[float], None] = time.sleep
    rng: random.Random | None = None


# endregion CLASS_RetryContext

_DEFAULT_RETRY_CONTEXT = RetryContext()


# region FUNC_retry_call
# PURPOSE: Invoke one operation with bounded retries on transient failures while honoring cooperative cancellation.
@overload
def retry_call[T](
    invoke: Callable[[], T],
    *,
    is_retriable: Callable[[BaseException], bool],
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    context: RetryContext,
) -> T: ...


@overload
def retry_call[T](
    invoke: Callable[[], T],
    *,
    is_retriable: Callable[[BaseException], bool],
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    **context_arguments: Unpack[_RetryContextArguments],
) -> T: ...


def retry_call[T](
    invoke: Callable[[], T],
    *,
    is_retriable: Callable[[BaseException], bool],
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    context: RetryContext | None = None,
    **context_arguments: Unpack[_RetryContextArguments],
) -> T:
    """Return ``invoke()``'s result, retrying the transient failures ``is_retriable`` accepts."""
    if context is None:
        context = (
            RetryContext(**context_arguments)
            if context_arguments
            else _DEFAULT_RETRY_CONTEXT
        )
    elif context_arguments:
        msg = "pass either RetryContext or individual retry controls"
        raise TypeError(msg)
    jitter = context.rng or _module_random
    for attempt in range(1, policy.max_attempts + 1):
        if context.cancellation is not None:
            context.cancellation.check()
        try:
            return invoke()
        except Exception as error:
            retriable = attempt < policy.max_attempts and is_retriable(error)
            if not retriable:
                if attempt > 1:
                    logger.debug(
                        "RETRY_EXHAUSTED",
                        extra={
                            "attempts": attempt,
                            "error_type": type(error).__name__,
                        },
                    )
                raise
            delay = jitter.uniform(0.0, policy.delay_for(attempt))
            logger.debug(
                "RETRY_SCHEDULED",
                extra={
                    "attempt": attempt,
                    "delay_seconds": round(delay, 3),
                    "error_type": type(error).__name__,
                    "max_attempts": policy.max_attempts,
                },
            )
            _interruptible_sleep(delay, context)
    unreachable = "retry loop terminated without a result"
    raise RuntimeError(unreachable)  # pragma: no cover


# endregion FUNC_retry_call


def _interruptible_sleep(delay: float, context: RetryContext) -> None:
    """Sleep for ``delay`` seconds in small chunks so cancellation stays responsive."""
    if context.cancellation is not None:
        context.cancellation.check()
    if delay <= 0:
        return
    remaining = delay
    while remaining > 0:
        chunk = min(_SLEEP_STEP_SECONDS, remaining)
        context.sleep(chunk)
        remaining -= chunk
        if context.cancellation is not None:
            context.cancellation.check()


# region FUNC_is_transient_http
# PURPOSE: Classify one ``requests`` exception as a transient HTTP failure eligible for retry.
def is_transient_http(error: BaseException) -> bool:
    """Return True for transport, timeout, throttle (429), and server (5xx) HTTP failures.

    Any ``RequestException`` is treated as transient except a small denylist of
    definitively-permanent request errors; ``HTTPError`` responses are classified
    by status. This denylist posture covers mid-stream transport failures such
    as ``ChunkedEncodingError`` that an explicit transient enumeration would miss.
    """
    if isinstance(error, _TRANSPORT_HTTP_ERRORS):
        return True
    if isinstance(error, requests.exceptions.HTTPError):
        return _is_transient_status(_http_status(error))
    if isinstance(error, _PERMANENT_HTTP_ERRORS):
        return False
    return isinstance(error, requests.exceptions.RequestException)


# endregion FUNC_is_transient_http


def _is_transient_status(status: int | None) -> bool:
    """Return True for throttle (429) and server (5xx) HTTP status codes."""
    return status == _HTTP_TOO_MANY_REQUESTS or (
        status is not None
        and _HTTP_SERVER_ERROR_MIN <= status <= _HTTP_SERVER_ERROR_MAX
    )


def _http_status(error: requests.exceptions.HTTPError) -> int | None:
    """Return the HTTP status carried by one HTTP error, if any."""
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


# region FUNC_is_transient_s3
# PURPOSE: Classify one MinIO or urllib3 exception as a transient S3-compatible failure eligible for retry.
def is_transient_s3(error: BaseException) -> bool:
    """Return True for S3 server/throttle failures and urllib3 transport failures.

    ``S3Error`` responses are classified by code or status; any other urllib3
    error is treated as transient except a small denylist of misconfiguration
    errors. This covers mid-stream transport failures during storage operations.
    """
    if isinstance(error, S3Error):
        if error.code in _TRANSIENT_S3_CODES:
            return True
        return _is_transient_status(_s3_status(error))
    if isinstance(error, _PERMANENT_URLLIB3_ERRORS):
        return False
    return isinstance(error, urllib3.exceptions.HTTPError)


# endregion FUNC_is_transient_s3


def _s3_status(error: S3Error) -> int | None:
    """Return the HTTP status carried by one S3 error response, if any."""
    response = getattr(error, "response", None)
    status = getattr(response, "status", None)
    return status if isinstance(status, int) else None
