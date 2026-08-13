# region MODULE_CONTRACT
# PURPOSE: Preserve the internal split between package and library selection policies while keeping their stable application facade.
# SCOPE:
# - Module ownership and facade identity.
# - NOT: Selection behavior, HTTP, storage, or CLI composition.
# KEYWORDS: unit test, selection, package, library, application facade
# endregion MODULE_CONTRACT

"""Structural tests for family-specific selection modules."""

from arduino_mirror.application import LatestLibrariesPolicy, LatestPackagesPolicy
from arduino_mirror.application.library_selection import (
    LatestLibrariesPolicy as InternalLatestLibrariesPolicy,
)
from arduino_mirror.application.package_selection import (
    LatestPackagesPolicy as InternalLatestPackagesPolicy,
)


# region FUNC_test_application_facade_exposes_family_specific_policy_modules
# PURPOSE: Ensure callers retain the stable facade while package and library schemas evolve in independent internal modules.
def test_application_facade_exposes_family_specific_policy_modules() -> None:
    """Facade policies are the classes implemented by their family modules."""
    assert LatestLibrariesPolicy is InternalLatestLibrariesPolicy
    assert LatestPackagesPolicy is InternalLatestPackagesPolicy


# endregion FUNC_test_application_facade_exposes_family_specific_policy_modules
