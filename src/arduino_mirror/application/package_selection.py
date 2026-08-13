# region MODULE_CONTRACT
# PURPOSE: Apply Arduino Boards Manager latest-release rules so configured package platforms and their required tools become a safe package publication plan.
# SCOPE:
# - Package-index schema, configured package and architecture filtering, latest platform selection, and tool dependency selection.
# - NOT: Library-index rules, HTTP, storage, CLI parsing, and shared archive transformations.
# INVARIANTS: Selected origin platforms are latest per architecture; external platforms remain visible without creating archive work.
# KEYWORDS: selection, packages, Boards Manager, latest release, tool dependency
# endregion MODULE_CONTRACT

"""Boards Manager selection policy."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from arduino_mirror.application.selection_common import (
    dict_list,
    origin_relative_path,
    transform_archive_record,
    transform_tool,
)
from arduino_mirror.domain import Archive, IndexFamily, PublicationPlan

__all__ = ["LatestPackagesPolicy"]


# region CLASS_LatestPackagesPolicy
# PURPOSE: Select configured latest Arduino platforms and their required tool archives while keeping package policy replaceable.
@dataclass(frozen=True)
class LatestPackagesPolicy:
    """Transform a Boards Manager index into a latest-only package plan."""

    mirror_host: str
    origin_host: str
    architectures: tuple[str, ...]
    package_names: tuple[str, ...]

    # region METHOD_select
    # PURPOSE: Transform configured Boards Manager releases into a package-only publication plan.
    def select(self, raw_index: dict[str, object]) -> PublicationPlan:  # noqa: PLR0912, PLR0915
        """Select filtered latest platforms and tool releases from a package index."""
        packages = dict_list(raw_index.get("packages"))
        retained: dict[str, dict[str, Any]] = {}
        tools_needed: set[tuple[str, str, str]] = set()
        archive_keys: set[str] = set()
        archives: dict[str, Archive] = {}
        releases: list[str] = []

        # region BLOCK_select_platforms
        for package in packages:
            name = package.get("name")
            if not isinstance(name, str) or name not in self.package_names:
                continue
            platforms = dict_list(package.get("platforms"))
            output = deepcopy(package)
            output["platforms"] = []
            output["tools"] = []
            retained[name] = output
            if not platforms:
                for tool in _latest_by_name(dict_list(package.get("tools"))):
                    tool_name = tool.get("name")
                    tool_version = tool.get("version")
                    if isinstance(tool_name, str) and isinstance(tool_version, str):
                        tools_needed.add((name, tool_name, tool_version))
                continue
            configured_platforms = [
                platform
                for platform in platforms
                if platform.get("architecture") in self.architectures
            ]
            selected_platforms = _latest_by_name(
                [
                    platform
                    for platform in configured_platforms
                    if origin_relative_path(platform.get("url"), self.origin_host)
                    is not None
                ],
                name_key="architecture",
            )
            selected_platforms.extend(
                platform
                for platform in configured_platforms
                if origin_relative_path(platform.get("url"), self.origin_host) is None
            )
            for platform in selected_platforms:
                transformed, keys, descriptors = transform_archive_record(
                    IndexFamily.PACKAGES,
                    platform,
                    mirror_host=self.mirror_host,
                    origin_host=self.origin_host,
                )
                output["platforms"].append(transformed)
                archive_keys.update(keys)
                archives.update(
                    {descriptor.key: descriptor for descriptor in descriptors}
                )
                architecture = platform.get("architecture")
                version = platform.get("version")
                if isinstance(architecture, str) and isinstance(version, str):
                    releases.append(f"{name}:{architecture}@{version}")
                for dependency in dict_list(platform.get("toolsDependencies")):
                    owner = dependency.get("packager")
                    tool_name = dependency.get("name")
                    tool_version = dependency.get("version")
                    if (
                        isinstance(owner, str)
                        and isinstance(tool_name, str)
                        and isinstance(tool_version, str)
                    ):
                        tools_needed.add((owner, tool_name, tool_version))
        # endregion BLOCK_select_platforms

        # region BLOCK_select_tools
        for package in packages:
            package_name = package.get("name")
            if not isinstance(package_name, str):
                continue
            selected_tools = [
                tool
                for tool in dict_list(package.get("tools"))
                if (package_name, tool.get("name"), tool.get("version")) in tools_needed
            ]
            if not selected_tools:
                continue
            output = retained.setdefault(package_name, deepcopy(package))
            output.setdefault("platforms", [])
            output["tools"] = []
            for tool in selected_tools:
                transformed, keys, descriptors = transform_tool(
                    tool, mirror_host=self.mirror_host, origin_host=self.origin_host
                )
                output["tools"].append(transformed)
                archive_keys.update(keys)
                archives.update(
                    {descriptor.key: descriptor for descriptor in descriptors}
                )
        # endregion BLOCK_select_tools

        index_packages = [
            package
            for package in retained.values()
            if package.get("platforms") or package.get("tools")
        ]
        return PublicationPlan(
            family=IndexFamily.PACKAGES,
            releases=tuple(sorted(releases)),
            archives=tuple(archives[key] for key in sorted(archive_keys)),
            index={"packages": index_packages},
        )

    # endregion METHOD_select


# endregion CLASS_LatestPackagesPolicy


# region FUNC__latest_by_name
# PURPOSE: Keep the highest Arduino-compatible version for each exact package record name.
def _latest_by_name(
    records: list[dict[str, Any]], *, name_key: str = "name"
) -> list[dict[str, Any]]:
    """Keep the highest version for each exact record name."""
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        name = record.get(name_key)
        if not isinstance(name, str):
            continue
        current = latest.get(name)
        if current is None or _version_key(record.get("version")) > _version_key(
            current.get("version")
        ):
            latest[name] = record
    return list(latest.values())


# endregion FUNC__latest_by_name


# region FUNC__version_key
# PURPOSE: Compare package release versions deterministically without a runtime dependency.
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
