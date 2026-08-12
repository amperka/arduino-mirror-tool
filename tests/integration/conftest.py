"""Integration test fixtures."""
# region MODULE_CONTRACT
# PURPOSE: Pytest fixtures for integration tests.
# SCOPE:
# DEPENDENCIES:
# KEYWORDS:
# endregion MODULE_CONTRACT

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "/tests/integration/" in str(item.path):
            item.add_marker("integration")
