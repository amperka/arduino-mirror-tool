"""Local JSON overlay adapter for Arduino indexes."""
# region MODULE_CONTRACT
# PURPOSE: Let operators supplement one remote Arduino index with a validated local overlay before existing selection rules consume it.
# SCOPE:
# - Local JSON loading, selected-family validation, and schema-aware remote-first index merging.
# - NOT: HTTP retrieval, release selection, archive transfer, target storage, or configuration parsing.
# INVARIANTS: The wrapped source fetches the remote index first; matching local fields win without discarding unspecified remote fields; only the selected family's collection is merged.
# KEYWORDS: local overlay, index source, JSON, packages, libraries
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from arduino_mirror.domain import IndexFamily

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from arduino_mirror.domain import IndexSource

__all__ = ["LocalOverlayIndexSource"]

logger = logging.getLogger(__name__)

_Record = dict[str, Any]
_Identity = tuple[str, ...]


# region CLASS_LocalOverlayIndexSource
# PURPOSE: Return one remote index supplemented by a local file without exposing filesystem behavior to publication use cases.
@dataclass(frozen=True)
class LocalOverlayIndexSource:
    """Merge one configured local index overlay after fetching its remote source."""

    source: IndexSource
    path: Path

    # region METHOD_fetch
    # PURPOSE: Load and merge one selected-family overlay only after the wrapped source provides its remote snapshot.
    def fetch(self, family: IndexFamily) -> dict[str, object]:
        """Return the wrapped remote index overlaid with validated local records."""
        remote = self.source.fetch(family)
        logger.debug(
            "LOCAL_OVERLAY_LOADING",
            extra={"family": family, "path": str(self.path)},
        )
        try:
            overlay = _load_overlay(self.path, family)
        except (OSError, TypeError, ValueError) as error:
            logger.debug(
                "LOCAL_OVERLAY_INVALID",
                extra={"error_type": type(error).__name__, "family": family},
            )
            raise
        merged = _merge_index(remote, overlay, family)
        collection = _collection_name(family)
        logger.debug(
            "LOCAL_OVERLAY_MERGED",
            extra={
                "family": family,
                "merged_record_count": len(_record_list(merged.get(collection))),
                "overlay_record_count": len(_record_list(overlay[collection])),
                "remote_record_count": len(_record_list(remote.get(collection))),
            },
        )
        return merged

    # endregion METHOD_fetch


# endregion CLASS_LocalOverlayIndexSource


# region FUNC__load_overlay
# PURPOSE: Decode one local JSON object and reject a file that cannot contribute records for the selected family.
def _load_overlay(path: Path, family: IndexFamily) -> dict[str, object]:
    """Return a validated overlay object for ``family``."""
    payload: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"local overlay for {family} is not a JSON object"
        raise TypeError(msg)
    collection = _collection_name(family)
    records = payload.get(collection)
    if not isinstance(records, list):
        msg = f"local overlay for {family} requires a {collection} list"
        raise TypeError(msg)
    _validate_overlay_records(records, family)
    return payload


# endregion FUNC__load_overlay


# region FUNC__merge_index
# PURPOSE: Preserve remote top-level data while replacing only the selected family collection with its merged records.
def _merge_index(
    remote: dict[str, object], overlay: dict[str, object], family: IndexFamily
) -> dict[str, object]:
    """Return a remote-first merged raw index for one family."""
    collection = _collection_name(family)
    merged = deepcopy(remote)
    local_records = _record_list(overlay[collection])
    merged[collection] = (
        _merge_by_identity(remote.get(collection), local_records, _library_identity)
        if family is IndexFamily.LIBRARIES
        else _merge_by_identity(
            remote.get(collection), local_records, _package_identity, _merge_package
        )
    )
    return merged


# endregion FUNC__merge_index


def _collection_name(family: IndexFamily) -> str:
    """Return the raw Arduino index collection owned by ``family``."""
    return "packages" if family is IndexFamily.PACKAGES else "libraries"


def _record_list(value: object) -> list[_Record]:
    """Return dictionary records from one untrusted collection."""
    return (
        [entry for entry in value if isinstance(entry, dict)]
        if isinstance(value, list)
        else []
    )


def _validate_overlay_records(records: list[object], family: IndexFamily) -> None:
    """Reject local entries that cannot be merged by their required identity."""
    for record in records:
        if not isinstance(record, dict):
            msg = "local overlay records must be JSON objects"
            raise TypeError(msg)
        if family is IndexFamily.LIBRARIES:
            _require_identity(record, ("name", "version"), "library")
        else:
            _validate_package(record)


def _validate_package(package: _Record) -> None:
    """Validate the identities of one local package and supplied nested collections."""
    _require_identity(package, ("name",), "package")
    if "platforms" in package:
        for platform in _required_list(package["platforms"], "package platforms"):
            _require_identity(platform, ("architecture", "version"), "platform")
    if "tools" not in package:
        return
    for tool in _required_list(package["tools"], "package tools"):
        _require_identity(tool, ("name", "version"), "tool")
        if "systems" in tool:
            for system in _required_list(tool["systems"], "tool systems"):
                _require_identity(system, ("host",), "tool system")


def _required_list(value: object, label: str) -> list[_Record]:
    """Return object records from a required local collection."""
    if not isinstance(value, list):
        msg = f"local overlay {label} must be a list"
        raise TypeError(msg)
    records = _record_list(value)
    if len(records) != len(value):
        msg = f"local overlay {label} records must be JSON objects"
        raise TypeError(msg)
    return records


def _require_identity(
    record: _Record, fields: tuple[str, ...], label: str
) -> _Identity:
    """Return a record identity or reject a local record that omits it."""
    values = tuple(record.get(field) for field in fields)
    if not all(isinstance(value, str) and value for value in values):
        msg = f"local overlay {label} requires {' and '.join(fields)}"
        raise ValueError(msg)
    return tuple(cast("str", value) for value in values)


def _package_identity(record: _Record) -> _Identity | None:
    """Return the optional identity of one package record."""
    return _identity(record, ("name",))


def _library_identity(record: _Record) -> _Identity | None:
    """Return the optional identity of one library release."""
    return _identity(record, ("name", "version"))


def _platform_identity(record: _Record) -> _Identity | None:
    """Return the optional identity of one package platform."""
    return _identity(record, ("architecture", "version"))


def _tool_identity(record: _Record) -> _Identity | None:
    """Return the optional identity of one package tool."""
    return _identity(record, ("name", "version"))


def _system_identity(record: _Record) -> _Identity | None:
    """Return the optional identity of one host-specific tool system."""
    return _identity(record, ("host",))


def _identity(record: _Record, fields: tuple[str, ...]) -> _Identity | None:
    """Return an identity when every field is a non-empty string."""
    values = tuple(record.get(field) for field in fields)
    return (
        tuple(cast("str", value) for value in values)
        if all(isinstance(value, str) and value for value in values)
        else None
    )


def _merge_by_identity(
    remote: object,
    overlay: list[_Record],
    identity: Callable[[_Record], _Identity | None],
    merge: Callable[[_Record, _Record], _Record] | None = None,
) -> list[_Record]:
    """Merge local records into remote order and append local-only records."""
    merged = deepcopy(_record_list(remote))
    positions: dict[_Identity, int] = {}
    for position, record in enumerate(merged):
        record_identity = identity(record)
        if record_identity is not None:
            positions.setdefault(record_identity, position)
    merge_record = merge or _overlay_record
    for record in overlay:
        record_identity = identity(record)
        if record_identity is None:
            msg = "validated local overlay record has no identity"
            raise AssertionError(msg)
        existing_position = positions.get(record_identity)
        if existing_position is None:
            positions[record_identity] = len(merged)
            merged.append(deepcopy(record))
        else:
            merged[existing_position] = merge_record(merged[existing_position], record)
    return merged


def _merge_package(remote: _Record, overlay: _Record) -> _Record:
    """Merge package-owned platforms and tools by their Arduino identities."""
    return _overlay_record(
        remote,
        overlay,
        collections={
            "platforms": lambda current, added: _merge_by_identity(
                current, _record_list(added), _platform_identity
            ),
            "tools": lambda current, added: _merge_by_identity(
                current, _record_list(added), _tool_identity, _merge_tool
            ),
        },
    )


def _merge_tool(remote: _Record, overlay: _Record) -> _Record:
    """Merge tool systems by their host identity."""
    return _overlay_record(
        remote,
        overlay,
        collections={
            "systems": lambda current, added: _merge_by_identity(
                current, _record_list(added), _system_identity
            )
        },
    )


def _overlay_record(
    remote: _Record,
    overlay: _Record,
    *,
    collections: dict[str, Callable[[object, object], list[_Record]]] | None = None,
) -> _Record:
    """Overlay supplied local fields while recursively merging named collections."""
    merged = deepcopy(remote)
    for name, value in overlay.items():
        collection_merge = collections.get(name) if collections is not None else None
        merged[name] = (
            collection_merge(remote.get(name), value)
            if collection_merge is not None
            else deepcopy(value)
        )
    return merged
