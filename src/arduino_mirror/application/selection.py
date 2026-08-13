# region MODULE_CONTRACT
# PURPOSE: Apply Arduino package and library latest-release rules to source indexes and create immutable publication plans.
# SCOPE: Package and library selection, origin archive eligibility, URL rewriting, and archive metadata extraction.
# NOT: HTTP, storage, CLI parsing, and test doubles.
# INVARIANTS: Only configured-origin archives become mirror objects; external records remain unchanged.
# KEYWORDS: selection, packages, libraries, latest release, archive
# endregion MODULE_CONTRACT

"""Family-specific selection policies for Arduino indexes."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from arduino_mirror.domain import Archive, IndexFamily, PublicationPlan

__all__ = ["LatestLibrariesPolicy", "LatestPackagesPolicy"]


# region FUNC_version_key
# PURPOSE: Compare supported SemVer-like versions deterministically without introducing a runtime dependency.
def version_key(
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


# endregion FUNC_version_key


# region FUNC_archive_key_and_url
# PURPOSE: Derive a family-owned mirror key and rewritten public archive URL only for an origin-host archive.
def archive_key_and_url(
    family: IndexFamily, url: object, *, mirror_host: str, origin_host: str
) -> tuple[str, str] | None:
    """Return the family-owned key and mirror URL for one eligible archive URL."""
    relative = _origin_relative_path(url, origin_host)
    if relative is None:
        return None
    key = f"{family}/{relative}"
    target = urlsplit(mirror_host)
    rewritten = urlunsplit(
        (target.scheme, target.netloc, f"{target.path.rstrip('/')}/{key}", "", "")
    )
    return key, rewritten


# endregion FUNC_archive_key_and_url


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
        packages = _dict_list(raw_index.get("packages"))
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
            platforms = _dict_list(package.get("platforms"))
            output = deepcopy(package)
            output["platforms"] = []
            output["tools"] = []
            retained[name] = output
            if not platforms:
                for tool in _latest_by_name(_dict_list(package.get("tools"))):
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
                    if _origin_relative_path(platform.get("url"), self.origin_host)
                    is not None
                ],
                name_key="architecture",
            )
            selected_platforms.extend(
                platform
                for platform in configured_platforms
                if _origin_relative_path(platform.get("url"), self.origin_host) is None
            )
            for platform in selected_platforms:
                transformed, keys, descriptors = _transform_archive_record(
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
                for dependency in _dict_list(platform.get("toolsDependencies")):
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
                for tool in _dict_list(package.get("tools"))
                if (package_name, tool.get("name"), tool.get("version")) in tools_needed
            ]
            if not selected_tools:
                continue
            output = retained.setdefault(package_name, deepcopy(package))
            output.setdefault("platforms", [])
            output["tools"] = []
            for tool in selected_tools:
                transformed, keys, descriptors = _transform_tool(
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
            for library in _dict_list(raw_index.get("libraries"))
            if _origin_relative_path(library.get("url"), self.origin_host) is not None
        ]
        external_libraries = [
            library
            for library in _dict_list(raw_index.get("libraries"))
            if _origin_relative_path(library.get("url"), self.origin_host) is None
        ]
        latest: dict[str, dict[str, Any]] = {}
        for library in origin_libraries:
            name = library.get("name")
            if not isinstance(name, str):
                continue
            existing = latest.get(name)
            if existing is None or version_key(library.get("version")) > version_key(
                existing.get("version")
            ):
                latest[name] = library

        archive_keys: set[str] = set()
        archives: dict[str, Archive] = {}
        selected: list[dict[str, Any]] = []
        releases: list[str] = []
        for name, library in sorted(latest.items()):
            transformed, keys, descriptors = _transform_archive_record(
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


def _dict_list(value: object) -> list[dict[str, Any]]:
    """Return dictionary entries from an untrusted JSON list."""
    return (
        [entry for entry in value if isinstance(entry, dict)]
        if isinstance(value, list)
        else []
    )


def _origin_relative_path(url: object, origin_host: str) -> str | None:
    """Return an origin-host archive path relative to origin_host, or None."""
    if not isinstance(url, str) or not url:
        return None
    source = urlsplit(url)
    origin = urlsplit(origin_host)
    if source.netloc != origin.netloc:
        return None
    origin_path = origin.path.rstrip("/")
    if origin_path and not (
        source.path == origin_path or source.path.startswith(origin_path + "/")
    ):
        return None
    relative = source.path.removeprefix(origin_path).lstrip("/")
    return relative or None


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
        if current is None or version_key(record.get("version")) > version_key(
            current.get("version")
        ):
            latest[name] = record
    return list(latest.values())


def _transform_archive_record(
    family: IndexFamily,
    record: dict[str, Any],
    *,
    mirror_host: str,
    origin_host: str,
) -> tuple[dict[str, Any], set[str], tuple[Archive, ...]]:
    """Copy one archive-bearing record and describe its eligible origin archive."""
    transformed = deepcopy(record)
    rewritten = archive_key_and_url(
        family, record.get("url"), mirror_host=mirror_host, origin_host=origin_host
    )
    if rewritten is None:
        return transformed, set(), ()
    key, url = rewritten
    transformed["url"] = url
    return transformed, {key}, (_archive_from_record(key, record),)


def _transform_tool(
    tool: dict[str, Any], *, mirror_host: str, origin_host: str
) -> tuple[dict[str, Any], set[str], tuple[Archive, ...]]:
    """Copy a package tool and describe every eligible system archive."""
    transformed = deepcopy(tool)
    keys: set[str] = set()
    archives: list[Archive] = []
    systems = _dict_list(transformed.get("systems"))
    source_systems = _dict_list(tool.get("systems"))
    transformed["systems"] = systems
    for system, source in zip(systems, source_systems, strict=True):
        rewritten = archive_key_and_url(
            IndexFamily.PACKAGES,
            source.get("url"),
            mirror_host=mirror_host,
            origin_host=origin_host,
        )
        if rewritten is not None:
            key, url = rewritten
            system["url"] = url
            keys.add(key)
            archives.append(_archive_from_record(key, source))
    return transformed, keys, tuple(archives)


def _archive_from_record(key: str, record: dict[str, Any]) -> Archive:
    """Create an archive descriptor from one selected origin record."""
    url = record.get("url")
    if not isinstance(url, str):
        msg = f"archive {key} has no source URL"
        raise TypeError(msg)
    checksum = record.get("checksum")
    matched = re.search(r"([0-9a-fA-F]{64})", str(checksum))
    size = record.get("size")
    return Archive(
        key=key,
        source_url=url,
        sha256=matched.group(1).lower() if matched is not None else None,
        size=int(size) if isinstance(size, int | str) and str(size).isdigit() else None,
    )
