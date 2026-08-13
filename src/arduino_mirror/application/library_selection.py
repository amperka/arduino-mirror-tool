# region MODULE_CONTRACT
# PURPOSE: Apply Arduino Library Manager latest-release rules so every library name contributes its newest origin-host release to a safe library publication plan.
# SCOPE:
# - Library-index schema, SemVer-like release ordering, external-record preservation, and library plan construction.
# - NOT: Package-index rules, HTTP, storage, CLI parsing, and shared archive transformations.
# INVARIANTS: Latest selection compares only origin-host releases by exact library name; external records remain unchanged and create no archive work.
# KEYWORDS: selection, libraries, Library Manager, latest release, SemVer
# endregion MODULE_CONTRACT

"""Library Manager selection policy."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from arduino_mirror.application.selection_common import (
    dict_list,
    origin_relative_path,
    transform_archive_record,
)
from arduino_mirror.domain import Archive, IndexFamily, PublicationPlan

__all__ = ["LatestLibrariesPolicy"]


# region CLASS_LatestLibrariesPolicy
# PURPOSE: Select the latest release of every library name while preserving each selected record's public metadata.
@dataclass(frozen=True)
class LatestLibrariesPolicy:
    """Transform a Library Manager index into an all-libraries latest-only plan."""

    mirror_host: str
    origin_host: str

    # region METHOD_select
    # PURPOSE: Transform latest origin-host Library Manager releases into a library-only publication plan.
    def select(self, raw_index: dict[str, object]) -> PublicationPlan:
        """Select the latest SemVer-compatible release for each exact library name."""
        origin_libraries = [
            library
            for library in dict_list(raw_index.get("libraries"))
            if origin_relative_path(library.get("url"), self.origin_host) is not None
        ]
        external_libraries = [
            library
            for library in dict_list(raw_index.get("libraries"))
            if origin_relative_path(library.get("url"), self.origin_host) is None
        ]
        latest: dict[str, dict[str, Any]] = {}
        for library in origin_libraries:
            name = library.get("name")
            if not isinstance(name, str):
                continue
            existing = latest.get(name)
            if existing is None or _version_key(library.get("version")) > _version_key(
                existing.get("version")
            ):
                latest[name] = library

        archive_keys: set[str] = set()
        archives: dict[str, Archive] = {}
        selected: list[dict[str, Any]] = []
        releases: list[str] = []
        for name, library in sorted(latest.items()):
            transformed, keys, descriptors = transform_archive_record(
                IndexFamily.LIBRARIES,
                library,
                mirror_host=self.mirror_host,
                origin_host=self.origin_host,
            )
            selected.append(transformed)
            archive_keys.update(keys)
            archives.update({descriptor.key: descriptor for descriptor in descriptors})
            version = library.get("version")
            if isinstance(version, str):
                releases.append(f"{name}@{version}")
        for library in external_libraries:
            selected.append(deepcopy(library))
            name = library.get("name")
            version = library.get("version")
            if isinstance(name, str) and isinstance(version, str):
                releases.append(f"{name}@{version}")
        return PublicationPlan(
            family=IndexFamily.LIBRARIES,
            releases=tuple(releases),
            archives=tuple(archives[key] for key in sorted(archive_keys)),
            index={"libraries": selected},
        )

    # endregion METHOD_select


# endregion CLASS_LatestLibrariesPolicy


# region FUNC__version_key
# PURPOSE: Compare library SemVer-like versions deterministically without introducing a runtime dependency.
def _version_key(
    value: object,
) -> tuple[tuple[int, ...], int, tuple[tuple[int, object], ...]]:
    """Return a sortable key where stable releases sort after prereleases."""
    match = re.fullmatch(
        r"v?(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?", str(value)
    )
    if match is None:
        return ((-1,), -1, ((0, str(value)),))
    numeric = tuple(int(part) for part in match.group(1).split("."))
    numeric = numeric + (0,) * (3 - len(numeric))
    prerelease = match.group(2)
    if prerelease is None:
        return (numeric, 1, ())
    parts = tuple(
        (1, int(part)) if part.isdigit() else (0, part)
        for part in prerelease.split(".")
    )
    return (numeric, 0, parts)


# endregion FUNC__version_key
