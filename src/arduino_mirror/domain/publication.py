# region MODULE_CONTRACT
# PURPOSE: Define immutable vocabulary for a planned index-family publication so every layer agrees on release identity, transformed indexes, and archive ownership.
# SCOPE:
# - Index-family names and publication plans.
# - NOT: index parsing, release selection, storage I/O, or orchestration.
# INVARIANTS: A plan owns archive and stale keys from exactly one index family.
# KEYWORDS: publication, index family, plan, archives
# endregion MODULE_CONTRACT

"""Immutable publication vocabulary."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

__all__ = [
    "Archive",
    "ArchiveUnavailableError",
    "IndexFamily",
    "PinnedPlatform",
    "PinnedPlatformSkip",
    "PinnedTool",
    "PinnedToolSkip",
    "PublicationPlan",
]


# region CLASS_IndexFamily
# PURPOSE: Name the independently published Arduino index families so storage ownership and operator actions cannot be confused.
class IndexFamily(StrEnum):
    """Arduino index family owned by one publication pipeline."""

    PACKAGES = "packages"
    LIBRARIES = "libraries"

    # region METHOD_archive_prefix
    # PURPOSE: Map the operator-facing family name to its stable short archive namespace.
    @property
    def archive_prefix(self) -> str:
        """Return this family's source-path-independent archive namespace."""
        return "p" if self is IndexFamily.PACKAGES else "l"

    # endregion METHOD_archive_prefix


# endregion CLASS_IndexFamily


# region CLASS_Archive
# PURPOSE: Describe one selected origin archive so a target can verify and publish the exact bytes named by a transformed index.
@dataclass(frozen=True)
class Archive:
    """Immutable origin archive metadata owned by one publication family."""

    key: str
    source_url: str
    sha256: str | None = None
    size: int | None = None


# endregion CLASS_Archive


# region CLASS_PinnedTool
# PURPOSE: Identify one exact Boards Manager tool release independently of a platform dependency.
@dataclass(frozen=True, order=True)
class PinnedTool:
    """One exact package-owner, tool-name, and version identity."""

    packager: str
    name: str
    version: str

    @property
    def identity(self) -> str:
        """Return the stable operator-facing identity for this tool."""
        return f"{self.packager}:{self.name}@{self.version}"


# endregion CLASS_PinnedTool


# region CLASS_PinnedPlatform
# PURPOSE: Identify one exact Boards Manager platform release independently of the latest-platform policy.
@dataclass(frozen=True, order=True)
class PinnedPlatform:
    """One exact package-owner, architecture, and version identity."""

    packager: str
    architecture: str
    version: str

    @property
    def identity(self) -> str:
        """Return the stable operator-facing identity for this platform."""
        return f"{self.packager}:{self.architecture}@{self.version}"


# endregion CLASS_PinnedPlatform


# region CLASS_PinnedToolSkip
# PURPOSE: Carry the non-secret reason an explicitly requested tool cannot appear in a publication plan.
@dataclass(frozen=True)
class PinnedToolSkip:
    """One skipped pinned tool and its selection reason."""

    tool: PinnedTool
    reason: str


# endregion CLASS_PinnedToolSkip


# region CLASS_PinnedPlatformSkip
# PURPOSE: Carry the non-secret reason an explicitly requested platform cannot appear in a publication plan.
@dataclass(frozen=True)
class PinnedPlatformSkip:
    """One skipped pinned platform and its selection reason."""

    platform: PinnedPlatform
    reason: str


# endregion CLASS_PinnedPlatformSkip


# region CLASS_ArchiveUnavailableError
# PURPOSE: Identify one archive that a target could not make available so the application can safely select an older release.
class ArchiveUnavailableError(RuntimeError):
    """A selected archive failed download, verification, or target publication."""

    def __init__(self, archive_key: str) -> None:
        """Describe the unavailable family-owned archive key without leaking transport details."""
        self.archive_key = archive_key
        super().__init__(f"archive unavailable: {archive_key}")


# endregion CLASS_ArchiveUnavailableError


# region CLASS_PublicationPlan
# PURPOSE: Carry one family's selected releases, transformed index, and archive changes through the pipeline without mutable cross-layer state.
@dataclass(frozen=True)
class PublicationPlan:
    """Immutable work selected for one index-family publication."""

    family: IndexFamily
    releases: tuple[str, ...]
    archives: tuple[Archive, ...]
    index: dict[str, Any]
    stale_keys: tuple[str, ...] = ()
    _archives_to_publish: tuple[Archive, ...] | None = None
    skipped_pinned_tools: tuple[PinnedToolSkip, ...] = ()
    skipped_pinned_platforms: tuple[PinnedPlatformSkip, ...] = ()

    # region METHOD_archive_keys
    # PURPOSE: Expose deterministic selected keys for reconciliation without duplicating archive descriptors in the plan.
    @property
    def archive_keys(self) -> tuple[str, ...]:
        """Return the selected family-owned archive keys in deterministic order."""
        return tuple(archive.key for archive in self.archives)

    # endregion METHOD_archive_keys

    # region METHOD_archives_to_publish
    # PURPOSE: Expose only archive work not confirmed by the target while preserving all selected keys for index and stale reconciliation.
    @property
    def archives_to_publish(self) -> tuple[Archive, ...]:
        """Return unconfirmed archives, or all archives before reconciliation."""
        return (
            self.archives
            if self._archives_to_publish is None
            else self._archives_to_publish
        )

    # endregion METHOD_archives_to_publish

    # region METHOD_with_reconciliation
    # PURPOSE: Attach a target's one-pass stale and pending-archive result without changing the selected index content.
    def with_reconciliation(
        self,
        *,
        stale_keys: tuple[str, ...],
        archives_to_publish: tuple[Archive, ...],
    ) -> PublicationPlan:
        """Return this plan with only target-confirmed archive work removed."""
        return replace(
            self,
            stale_keys=stale_keys,
            _archives_to_publish=archives_to_publish,
        )

    # endregion METHOD_with_reconciliation


# endregion CLASS_PublicationPlan
