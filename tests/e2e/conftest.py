# region MODULE_CONTRACT
# PURPOSE: Provide E2E-only pytest fixtures so the suite can observe application log records without external services.
# SCOPE:
# - Mark E2E tests and capture `arduino_mirror` log records.
# - NOT: Production logging configuration or application behavior.
# DEPENDENCIES:
# - USES API: pytest fixtures and markers; stdlib logging.
# KEYWORDS: e2e, fixtures, log capture
# endregion MODULE_CONTRACT

import logging
from collections.abc import Generator

import pytest

_LOGGER = "arduino_mirror"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "/tests/e2e/" in str(item.path):
            item.add_marker("e2e")


class LogCaptureHandler(logging.Handler):
    def __init__(self, records: list[logging.LogRecord]) -> None:
        super().__init__(level=logging.DEBUG)
        self._records = records

    def emit(self, record: logging.LogRecord) -> None:
        self._records.append(record)


@pytest.fixture
def log_records() -> Generator[list[logging.LogRecord], None, None]:
    logger = logging.getLogger(_LOGGER)
    records: list[logging.LogRecord] = []
    handler = LogCaptureHandler(records)
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
