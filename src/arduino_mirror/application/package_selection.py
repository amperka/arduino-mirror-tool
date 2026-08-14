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
from arduino_mirror.domain import (
    Archive,
    IndexFamily,
    PinnedPlatform,
    PinnedPlatformSkip,
    PinnedTool,
    PinnedToolSkip,
    PublicationPlan,
)

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
    pinned_tools: tuple[PinnedTool, ...] = ()
    pinned_platforms: tuple[PinnedPlatform, ...] = ()

    # region METHOD_select
    # PURPOSE: Transform configured Boards Manager releases into a package-only publication plan.
    def select(  # noqa: PLR0912, PLR0915
        self,
        raw_index: dict[str, object],
        *,
        unavailable_archive_keys: frozenset[str] = frozenset(),
    ) -> PublicationPlan:
        """Select filtered latest available platforms and tool releases from a package index."""
        packages = dict_list(raw_index.get("packages"))
        unavailable_tools = _unavailable_tools(
            packages,
            mirror_host=self.mirror_host,
            origin_host=self.origin_host,
            unavailable_archive_keys=unavailable_archive_keys,
        )
        skipped_pinned_tools = _skipped_pinned_tools(
            packages,
            pinned_tools=self.pinned_tools,
            mirror_host=self.mirror_host,
            origin_host=self.origin_host,
            unavailable_archive_keys=unavailable_archive_keys,
        )
        skipped_pinned_platforms = _skipped_pinned_platforms(
            packages,
            pinned_platforms=self.pinned_platforms,
            mirror_host=self.mirror_host,
            origin_host=self.origin_host,
            unavailable_archive_keys=unavailable_archive_keys,
            unavailable_tools=unavailable_tools,
        )
        pinned_platforms = frozenset(self.pinned_platforms)
        retained: dict[str, dict[str, Any]] = {}
        tools_needed = {
            (tool.packager, tool.name, tool.version) for tool in self.pinned_tools
        }
        archive_keys: set[str] = set()
        archives: dict[str, Archive] = {}
        releases: list[str] = []

        # region BLOCK_select_platforms
        for package in packages:
            name = package.get("name")
            if not isinstance(name, str):
                continue
            platforms = dict_list(package.get("platforms"))
            pinned_matches = _pinned_platform_matches(
                name,
                platforms,
                pinned_platforms=pinned_platforms,
                mirror_host=self.mirror_host,
                origin_host=self.origin_host,
                unavailable_archive_keys=unavailable_archive_keys,
                unavailable_tools=unavailable_tools,
            )
            if name not in self.package_names and not pinned_matches:
                continue
            output = _selected_package_output(package)
            retained[name] = output
            if not platforms:
                for tool in _latest_by_name(
                    [
                        tool
                        for tool in dict_list(package.get("tools"))
                        if _tool_is_available(
                            tool,
                            mirror_host=self.mirror_host,
                            origin_host=self.origin_host,
                            unavailable_archive_keys=unavailable_archive_keys,
                        )
                    ]
                ):
                    tool_name = tool.get("name")
                    tool_version = tool.get("version")
                    if isinstance(tool_name, str) and isinstance(tool_version, str):
                        tools_needed.add((name, tool_name, tool_version))
                continue
            configured_platforms = [
                platform
                for platform in platforms
                if name in self.package_names
                and platform.get("architecture") in self.architectures
                and _platform_is_available(
                    platform,
                    mirror_host=self.mirror_host,
                    origin_host=self.origin_host,
                    unavailable_archive_keys=unavailable_archive_keys,
                    unavailable_tools=unavailable_tools,
                )
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
            selected_platforms.extend(
                platform
                for platform in pinned_matches
                if platform not in selected_platforms
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
            selected_tools: list[dict[str, Any]] = []
            selected_identities: set[tuple[str, str, str]] = set()
            for tool in dict_list(package.get("tools")):
                tool_name = tool.get("name")
                tool_version = tool.get("version")
                if not isinstance(tool_name, str) or not isinstance(tool_version, str):
                    continue
                identity = (package_name, tool_name, tool_version)
                if (
                    identity not in tools_needed
                    or identity in selected_identities
                    or not _tool_is_available(
                        tool,
                        mirror_host=self.mirror_host,
                        origin_host=self.origin_host,
                        unavailable_archive_keys=unavailable_archive_keys,
                    )
                ):
                    continue
                selected_tools.append(tool)
                selected_identities.add(identity)
            if not selected_tools:
                continue
            output = retained.setdefault(
                package_name, _selected_package_output(package)
            )
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
            skipped_pinned_tools=skipped_pinned_tools,
            skipped_pinned_platforms=skipped_pinned_platforms,
        )

    # endregion METHOD_select


# endregion CLASS_LatestPackagesPolicy


# region FUNC__selected_package_output
# PURPOSE: Preserve package metadata while preventing unselected platforms and tools from entering a filtered index.
def _selected_package_output(package: dict[str, Any]) -> dict[str, Any]:
    """Return a package copy with only selection-owned collections."""
    output = deepcopy(package)
    output["platforms"] = []
    output["tools"] = []
    return output


# endregion FUNC__selected_package_output


# region FUNC__skipped_pinned_tools
# PURPOSE: Describe exact requested tools absent from the source or excluded by unavailable origin system archives.
def _skipped_pinned_tools(
    packages: list[dict[str, Any]],
    *,
    pinned_tools: tuple[PinnedTool, ...],
    mirror_host: str,
    origin_host: str,
    unavailable_archive_keys: frozenset[str],
) -> tuple[PinnedToolSkip, ...]:
    """Return deterministic diagnostics for pins that selection cannot retain."""
    pins = frozenset(pinned_tools)
    matching: set[PinnedTool] = set()
    available: set[PinnedTool] = set()
    for package in packages:
        package_name = package.get("name")
        if not isinstance(package_name, str):
            continue
        for tool in dict_list(package.get("tools")):
            tool_name = tool.get("name")
            tool_version = tool.get("version")
            if not isinstance(tool_name, str) or not isinstance(tool_version, str):
                continue
            pin = PinnedTool(package_name, tool_name, tool_version)
            if pin not in pins:
                continue
            matching.add(pin)
            if _tool_is_available(
                tool,
                mirror_host=mirror_host,
                origin_host=origin_host,
                unavailable_archive_keys=unavailable_archive_keys,
            ):
                available.add(pin)
    skipped: list[PinnedToolSkip] = []
    for pin in sorted(pins):
        if pin not in matching:
            skipped.append(PinnedToolSkip(pin, "not found in source index"))
        elif pin not in available:
            skipped.append(PinnedToolSkip(pin, "origin system archive unavailable"))
    return tuple(skipped)


# endregion FUNC__skipped_pinned_tools


# region FUNC__skipped_pinned_platforms
# PURPOSE: Describe exact requested platforms absent from the source or excluded by unavailable archives.
def _skipped_pinned_platforms(  # noqa: PLR0913
    packages: list[dict[str, Any]],
    *,
    pinned_platforms: tuple[PinnedPlatform, ...],
    mirror_host: str,
    origin_host: str,
    unavailable_archive_keys: frozenset[str],
    unavailable_tools: frozenset[tuple[str, str, str]],
) -> tuple[PinnedPlatformSkip, ...]:
    """Return deterministic diagnostics for platform pins that selection cannot retain."""
    pins = frozenset(pinned_platforms)
    reasons: dict[PinnedPlatform, str] = {}
    matching: set[PinnedPlatform] = set()
    available: set[PinnedPlatform] = set()
    for package in packages:
        package_name = package.get("name")
        if not isinstance(package_name, str):
            continue
        for platform in dict_list(package.get("platforms")):
            architecture = platform.get("architecture")
            version = platform.get("version")
            if not isinstance(architecture, str) or not isinstance(version, str):
                continue
            pin = PinnedPlatform(package_name, architecture, version)
            if pin not in pins:
                continue
            matching.add(pin)
            reason = _platform_unavailability_reason(
                platform,
                mirror_host=mirror_host,
                origin_host=origin_host,
                unavailable_archive_keys=unavailable_archive_keys,
                unavailable_tools=unavailable_tools,
            )
            if reason is None:
                available.add(pin)
            else:
                reasons.setdefault(pin, reason)
    return tuple(
        PinnedPlatformSkip(pin, "not found in source index")
        if pin not in matching
        else PinnedPlatformSkip(pin, reasons[pin])
        for pin in sorted(pins)
        if pin not in matching or pin not in available
    )


# endregion FUNC__skipped_pinned_platforms


# region FUNC__pinned_platform_matches
# PURPOSE: Find available source platforms whose exact owner, architecture, and version match a configured pin.
def _pinned_platform_matches(  # noqa: PLR0913
    package_name: str,
    platforms: list[dict[str, Any]],
    *,
    pinned_platforms: frozenset[PinnedPlatform],
    mirror_host: str,
    origin_host: str,
    unavailable_archive_keys: frozenset[str],
    unavailable_tools: frozenset[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    """Return available exact platform-pin records from one package owner."""
    return [
        platform
        for platform in platforms
        if isinstance(platform.get("architecture"), str)
        and isinstance(platform.get("version"), str)
        and PinnedPlatform(package_name, platform["architecture"], platform["version"])
        in pinned_platforms
        and _platform_is_available(
            platform,
            mirror_host=mirror_host,
            origin_host=origin_host,
            unavailable_archive_keys=unavailable_archive_keys,
            unavailable_tools=unavailable_tools,
        )
    ]


# endregion FUNC__pinned_platform_matches


# region FUNC__platform_is_available
# PURPOSE: Reject an origin platform when its own archive or an exact required tool archive is unavailable.
def _platform_is_available(
    platform: dict[str, Any],
    *,
    mirror_host: str,
    origin_host: str,
    unavailable_archive_keys: frozenset[str],
    unavailable_tools: frozenset[tuple[str, str, str]],
) -> bool:
    """Return whether the platform can be published with all required known tool archives."""
    return (
        _platform_unavailability_reason(
            platform,
            mirror_host=mirror_host,
            origin_host=origin_host,
            unavailable_archive_keys=unavailable_archive_keys,
            unavailable_tools=unavailable_tools,
        )
        is None
    )


# endregion FUNC__platform_is_available


# region FUNC__platform_unavailability_reason
# PURPOSE: Explain whether a platform's own archive or an exact required tool makes it ineligible.
def _platform_unavailability_reason(
    platform: dict[str, Any],
    *,
    mirror_host: str,
    origin_host: str,
    unavailable_archive_keys: frozenset[str],
    unavailable_tools: frozenset[tuple[str, str, str]],
) -> str | None:
    """Return a non-secret unavailability reason, or ``None`` when eligible."""
    _, keys, _ = transform_archive_record(
        IndexFamily.PACKAGES,
        platform,
        mirror_host=mirror_host,
        origin_host=origin_host,
    )
    if keys.intersection(unavailable_archive_keys):
        return "origin platform archive unavailable"
    if any(
        isinstance(dependency.get("packager"), str)
        and isinstance(dependency.get("name"), str)
        and isinstance(dependency.get("version"), str)
        and (
            dependency["packager"],
            dependency["name"],
            dependency["version"],
        )
        in unavailable_tools
        for dependency in dict_list(platform.get("toolsDependencies"))
    ):
        return "required tool archive unavailable"
    return None


# endregion FUNC__platform_unavailability_reason


# region FUNC__tool_is_available
# PURPOSE: Reject a tool record when any origin system archive in that exact version is unavailable.
def _tool_is_available(
    tool: dict[str, Any],
    *,
    mirror_host: str,
    origin_host: str,
    unavailable_archive_keys: frozenset[str],
) -> bool:
    """Return whether every origin system archive of one tool remains available."""
    _, keys, _ = transform_tool(
        tool,
        mirror_host=mirror_host,
        origin_host=origin_host,
    )
    return not keys.intersection(unavailable_archive_keys)


# endregion FUNC__tool_is_available


# region FUNC__unavailable_tools
# PURPOSE: Map a failed tool archive to every platform dependency that requires its exact tool version.
def _unavailable_tools(
    packages: list[dict[str, Any]],
    *,
    mirror_host: str,
    origin_host: str,
    unavailable_archive_keys: frozenset[str],
) -> frozenset[tuple[str, str, str]]:
    """Return exact package, tool, and version identities with a failed origin system archive."""
    unavailable: set[tuple[str, str, str]] = set()
    for package in packages:
        package_name = package.get("name")
        if not isinstance(package_name, str):
            continue
        for tool in dict_list(package.get("tools")):
            tool_name = tool.get("name")
            version = tool.get("version")
            if (
                isinstance(tool_name, str)
                and isinstance(version, str)
                and not _tool_is_available(
                    tool,
                    mirror_host=mirror_host,
                    origin_host=origin_host,
                    unavailable_archive_keys=unavailable_archive_keys,
                )
            ):
                unavailable.add((package_name, tool_name, version))
    return frozenset(unavailable)


# endregion FUNC__unavailable_tools


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
