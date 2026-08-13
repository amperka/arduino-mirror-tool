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

__all__ = ["Archive", "IndexFamily", "PublicationPlan"]


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
