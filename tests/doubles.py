# region MODULE_CONTRACT
# PURPOSE: Supply deterministic source and target doubles for publication use-case tests without external I/O.
# SCOPE:
# - Fixture source, recording target, and configured archive failure.
# - NOT: Selection rules, HTTP, filesystem, or S3 implementations.
# KEYWORDS: test double, fixture source, recording target, publication
# endregion MODULE_CONTRACT

"""Publication collaborators used only by tests."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from arduino_mirror.domain import (
    ArchiveUnavailableError,
    IndexFamily,
    PublicationCancellation,
    PublicationPlan,
)

__all__ = ["FixtureIndexSource", "RecordingPublicationTarget", "TargetArchiveError"]


# region CLASS_TargetArchiveError
# PURPOSE: Signal a configured archive-boundary failure for safe-order and isolation tests.
class TargetArchiveError(RuntimeError):
    """Configured archive publication failure."""

    def __init__(self, family: IndexFamily) -> None:
        """Describe the family whose recording target failed."""
        super().__init__(f"archive publication failed for {family}")


# endregion CLASS_TargetArchiveError


# region CLASS_FixtureIndexSource
# PURPOSE: Supply stable fixture data for a requested family without contacting an upstream service.
@dataclass(frozen=True)
class FixtureIndexSource:
    """Return a fixed raw index for one test double family."""

    family: IndexFamily
    raw_index: dict[str, object]

    def fetch(self, family: IndexFamily) -> dict[str, object]:
        """Return the configured fixture index for its configured family."""
        if family is not self.family:
            msg = f"source for {self.family} cannot fetch {family}"
            raise ValueError(msg)
        return deepcopy(self.raw_index)


# endregion CLASS_FixtureIndexSource


# region CLASS_RecordingPublicationTarget
# PURPOSE: Record publication boundaries and optionally fail archive publication so the test double proves safe ordering and failure isolation.
@dataclass
class RecordingPublicationTarget:
    """No-I/O target that records operations for one family."""

    present_keys: tuple[str, ...] = ()
    fail_archives: bool = False
    unavailable_archive_keys: set[str] = field(default_factory=set)
    operations: list[str] = field(default_factory=list)
    index_replaced: bool = False

    def reconcile(self, plan: PublicationPlan) -> PublicationPlan:
        """Record configured target inventory and return its stale-key reconciliation."""
        self.operations.append(f"{plan.family}:list")
        stale_keys = tuple(
            key
            for key in self.present_keys
            if key.startswith(f"{plan.family.archive_prefix}/")
            and key not in plan.archive_keys
        )
        return plan.with_reconciliation(
            stale_keys=stale_keys,
            archives_to_publish=plan.archives,
        )

    def publish_archives(
        self, plan: PublicationPlan, *, cancellation: PublicationCancellation
    ) -> None:
        """Record archive publication or raise the configured failure."""
        cancellation.check()
        self.operations.append(f"{plan.family}:archives")
        unavailable = next(
            (
                archive
                for archive in plan.archives_to_publish
                if self.fail_archives or archive.key in self.unavailable_archive_keys
            ),
            None,
        )
        if unavailable is not None:
            raise ArchiveUnavailableError(unavailable.key) from TargetArchiveError(
                plan.family
            )

    def replace_index(
        self, plan: PublicationPlan, *, cancellation: PublicationCancellation
    ) -> None:
        """Record the atomic index boundary after archive publication."""
        cancellation.check()
        self.operations.append(f"{plan.family}:index")
        self.index_replaced = True

    def cleanup_stale(
        self, plan: PublicationPlan, *, cancellation: PublicationCancellation
    ) -> None:
        """Record stale cleanup after the index boundary."""
        cancellation.check()
        self.operations.append(f"{plan.family}:cleanup")


# endregion CLASS_RecordingPublicationTarget
