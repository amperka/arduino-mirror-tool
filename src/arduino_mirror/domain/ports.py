# region MODULE_CONTRACT
# PURPOSE: Decouple publication use cases from index retrieval, family-specific selection, and target storage implementations.
# SCOPE:
# - Structural contracts consumed by the application layer.
# - NOT: concrete adapters, CLI parsing, or publication orchestration.
# INVARIANTS: Ports express synchronous collaboration boundaries and perform no I/O themselves.
# KEYWORDS: port, protocol, source, selection, publication target
# endregion MODULE_CONTRACT

"""Domain ports for publication collaborators."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from arduino_mirror.domain.publication import IndexFamily, PublicationPlan

__all__ = [
    "IndexSource",
    "PublicationCancellation",
    "PublicationTarget",
    "SelectionPolicy",
]


# region CLASS_IndexSource
# PURPOSE: Let a publication use case obtain one raw index without knowing whether it came from a fake, HTTP, or another source.
@runtime_checkable
class IndexSource(Protocol):
    """Obtain raw data for an Arduino index family."""

    def fetch(self, family: IndexFamily) -> dict[str, object]:
        """Return the raw index data for ``family``."""


# endregion CLASS_IndexSource


# region CLASS_SelectionPolicy
# PURPOSE: Let each index family apply its own release-selection rule without coupling the publication flow to index schema details.
@runtime_checkable
class SelectionPolicy(Protocol):
    """Create a family-scoped publication plan from raw index data."""

    def select(self, raw_index: dict[str, object]) -> PublicationPlan:
        """Return the selected publication plan."""


# endregion CLASS_SelectionPolicy


# region CLASS_PublicationCancellation
# PURPOSE: Let publication adapters distinguish an interruptible archive download from an upload that must finish at its next safe boundary.
@runtime_checkable
class PublicationCancellation(Protocol):
    """Coordinate safe publication cancellation across application and adapters."""

    def check(self) -> None:
        """Stop before the next external operation when cancellation was requested."""

    def interrupt_download(self) -> AbstractContextManager[None]:
        """Make the enclosed archive download immediately interruptible."""


# endregion CLASS_PublicationCancellation


# region CLASS_PublicationTarget
# PURPOSE: Give the use case a storage boundary that preserves the safe archive, index, and cleanup order.
@runtime_checkable
class PublicationTarget(Protocol):
    """Publish one family's archives and index to its owned target namespace."""

    def reconcile(self, plan: PublicationPlan) -> PublicationPlan:
        """Confirm selected archives and stale keys from one family-scoped target inventory."""

    def publish_archives(
        self, plan: PublicationPlan, *, cancellation: PublicationCancellation
    ) -> None:
        """Make every unconfirmed archive required by ``plan`` available."""

    def replace_index(
        self, plan: PublicationPlan, *, cancellation: PublicationCancellation
    ) -> None:
        """Atomically replace ``plan``'s published index."""

    def cleanup_stale(
        self, plan: PublicationPlan, *, cancellation: PublicationCancellation
    ) -> None:
        """Remove stale archives owned by ``plan.family`` after index replacement."""


# endregion CLASS_PublicationTarget
