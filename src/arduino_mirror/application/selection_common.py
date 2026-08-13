# region MODULE_CONTRACT
# PURPOSE: Keep archive transformations identical across index-family policies so each policy can focus on its own source schema.
# SCOPE:
# - Safe JSON-record parsing, origin archive eligibility, mirror URL rewriting, and immutable archive descriptor construction.
# - NOT: Family-specific latest-version rules, HTTP, storage, CLI parsing, and publication orchestration.
# INVARIANTS: Only configured-origin archives create descriptors or rewritten URLs; transformed records do not mutate source records.
# KEYWORDS: selection, archive, origin, URL rewrite, descriptor
# endregion MODULE_CONTRACT

"""Internal pure archive transformation helpers shared by selection policies."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from arduino_mirror.domain import Archive, IndexFamily

__all__: list[str] = []


# region FUNC_archive_key_and_url
# PURPOSE: Derive a family-owned mirror key and rewritten public archive URL only for an origin-host archive.
def archive_key_and_url(
    family: IndexFamily, url: object, *, mirror_host: str, origin_host: str
) -> tuple[str, str] | None:
    """Return the family-owned key and mirror URL for one eligible archive URL."""
    relative = origin_relative_path(url, origin_host)
    if relative is None:
        return None
    key = f"{family.archive_prefix}/{relative}"
    target = urlsplit(mirror_host)
    rewritten = urlunsplit(
        (target.scheme, target.netloc, f"{target.path.rstrip('/')}/{key}", "", "")
    )
    return key, rewritten


# endregion FUNC_archive_key_and_url


# region FUNC_dict_list
# PURPOSE: Safely retain only dictionary records from an untrusted JSON list.
def dict_list(value: object) -> list[dict[str, Any]]:
    """Return dictionary entries from an untrusted JSON list."""
    return (
        [entry for entry in value if isinstance(entry, dict)]
        if isinstance(value, list)
        else []
    )


# endregion FUNC_dict_list


# region FUNC_transform_archive_record
# PURPOSE: Copy one archive-bearing record and describe its eligible origin archive without mutating the source index.
def transform_archive_record(
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
    return transformed, {key}, (archive_from_record(key, record),)


# endregion FUNC_transform_archive_record


# region FUNC_transform_tool
# PURPOSE: Copy one package tool and describe every eligible origin-host system archive it contains.
def transform_tool(
    tool: dict[str, Any], *, mirror_host: str, origin_host: str
) -> tuple[dict[str, Any], set[str], tuple[Archive, ...]]:
    """Copy a package tool and describe every eligible system archive."""
    transformed = deepcopy(tool)
    keys: set[str] = set()
    archives: list[Archive] = []
    systems = dict_list(transformed.get("systems"))
    source_systems = dict_list(tool.get("systems"))
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
            archives.append(archive_from_record(key, source))
    return transformed, keys, tuple(archives)


# endregion FUNC_transform_tool


# region FUNC_origin_relative_path
# PURPOSE: Recognize a source URL owned by the configured origin and return its non-empty relative archive path.
def origin_relative_path(url: object, origin_host: str) -> str | None:
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


# endregion FUNC_origin_relative_path


# region FUNC_archive_from_record
# PURPOSE: Build an immutable archive descriptor with optional normalized integrity metadata from a selected source record.
def archive_from_record(key: str, record: dict[str, Any]) -> Archive:
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


# endregion FUNC_archive_from_record
