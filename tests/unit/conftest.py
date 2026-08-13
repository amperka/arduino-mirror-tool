# region MODULE_CONTRACT
# PURPOSE: Auto-mark all collected tests in this directory as "unit".
# SCOPE:
# - pytest_collection_modifyitems hook, autouse cache-isolation fixture.
# - NOT: Fixture behavior or production code.
# KEYWORDS: pytest auto-mark, unit tests, cache isolation
# endregion MODULE_CONTRACT

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "/tests/unit/" in str(item.path):
            item.add_marker("unit")
