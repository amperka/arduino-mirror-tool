"""Integration test fixtures."""
# region MODULE_CONTRACT
# PURPOSE: Mark integration tests so pytest can select the suite deterministically.
# SCOPE:
# - Apply the integration marker to tests in this directory.
# - NOT: Provide test fixtures or implement integration behavior.
# DEPENDENCIES:
# - USES API: pytest collection hooks and markers.
# KEYWORDS: integration, pytest, marker
# endregion MODULE_CONTRACT

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "/tests/integration/" in str(item.path):
            item.add_marker("integration")
