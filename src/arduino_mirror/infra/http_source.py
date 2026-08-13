# region MODULE_CONTRACT
# PURPOSE: Retrieve configured Arduino JSON indexes over HTTP for application publication flows.
# SCOPE:
# - HTTP JSON retrieval and source response validation.
# - NOT: Archive transfer, selection, storage, or CLI parsing.
# KEYWORDS: HTTP, index source, Arduino
# endregion MODULE_CONTRACT

"""HTTP adapter for Arduino index retrieval."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import requests

from .retry import DEFAULT_RETRY_POLICY, RetryPolicy, is_transient_http, retry_call

if TYPE_CHECKING:
    from collections.abc import Mapping

    from arduino_mirror.domain import IndexFamily

__all__ = ["HttpIndexSource"]

logger = logging.getLogger(__name__)


# region CLASS_HttpIndexSource
# PURPOSE: Retrieve one configured Arduino JSON index while keeping HTTP details outside the application use case.
@dataclass(frozen=True)
class HttpIndexSource:
    """Fetch configured JSON index URLs by family."""

    urls: Mapping[IndexFamily, str]
    timeout_seconds: float = 60.0
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY

    # region METHOD_fetch
    # PURPOSE: Return validated JSON object data for the requested family.
    def fetch(self, family: IndexFamily) -> dict[str, object]:
        """Fetch and decode the configured index for ``family``."""
        try:
            url = self.urls[family]
        except KeyError as error:
            msg = f"no source URL configured for {family}"
            raise ValueError(msg) from error
        logger.info("Fetching %s index", family.value)
        logger.debug("SOURCE_REQUESTED", extra={"family": family})

        def request() -> requests.Response:
            response = requests.get(url, timeout=self.timeout_seconds)
            response.raise_for_status()
            return response

        try:
            response = retry_call(
                request,
                is_retriable=is_transient_http,
                policy=self.retry_policy,
            )
        except requests.RequestException as error:
            logger.debug(
                "SOURCE_REQUEST_FAILED",
                extra={"error_type": type(error).__name__, "family": family},
            )
            raise
        try:
            payload: Any = response.json()
        except ValueError as error:
            logger.debug(
                "SOURCE_INVALID",
                extra={"error_type": type(error).__name__, "family": family},
            )
            raise
        if not isinstance(payload, dict):
            logger.debug(
                "SOURCE_INVALID",
                extra={"actual_type": type(payload).__name__, "family": family},
            )
            msg = f"source index for {family} is not a JSON object"
            raise TypeError(msg)
        logger.info("Fetched %s index", family.value)
        logger.debug("SOURCE_RECEIVED", extra={"family": family})
        return payload

    # endregion METHOD_fetch


# endregion CLASS_HttpIndexSource
