# region MODULE_CONTRACT
# PURPOSE: Coordinate safe publication of one selected Arduino index family while keeping external source and storage behavior behind domain ports.
# SCOPE:
# - Publication use case, stale-key reconciliation, and trace boundaries.
# - NOT: index parsing, selection algorithms, CLI parsing, HTTP, or S3 calls.
# INVARIANTS: Archives publish before index replacement; stale cleanup follows a successful replacement and affects only the selected family.
# KEYWORDS: use case, publication, orchestration, trace, reconciliation
# endregion MODULE_CONTRACT

"""Publication application use case."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager

    from arduino_mirror.domain import (
        IndexFamily,
        IndexSource,
        PublicationCancellation,
        PublicationPlan,
        PublicationTarget,
        SelectionPolicy,
    )

__all__ = ["PublishFamily"]

logger = logging.getLogger(__name__)


# region CLASS_PublishFamily
# PURPOSE: Execute one family's source, selection, archive, index, and cleanup boundaries in the safe order required for clients to keep working.
@dataclass(frozen=True)
class PublishFamily:
    """Coordinate a publication through injected domain ports."""

    source: IndexSource
    selection: SelectionPolicy
    target: PublicationTarget

    # region METHOD_plan
    # PURPOSE: Build a family-scoped selected plan and calculate only that family's stale archive keys when origin archive work exists.
    def plan(
        self,
        family: IndexFamily,
        *,
        check_cancelled: Callable[[], None] | None = None,
    ) -> PublicationPlan:
        """Return a selected plan reconciled with the target's family-owned keys."""
        check = check_cancelled or _continue
        check()
        raw_index = self.source.fetch(family)
        check()
        logger.debug("SOURCE_FETCHED", extra={"family": family})

        selected = self.selection.select(raw_index)
        if selected.family is not family:
            msg = f"selection returned {selected.family} for {family}"
            raise ValueError(msg)
        logger.info(
            "Selected %s %s release(s), %s archive(s)",
            len(selected.releases),
            family.value,
            len(selected.archives),
        )
        logger.debug(
            "PLAN_SELECTED",
            extra={
                "archive_count": len(selected.archives),
                "family": family,
                "release_count": len(selected.releases),
            },
        )

        if not selected.archives:
            logger.debug(
                "PLAN_EMPTY",
                extra={"family": family, "release_count": len(selected.releases)},
            )
            return selected

        check()
        plan = self.target.reconcile(selected)
        check()
        logger.info("Found %s stale %s archive(s)", len(plan.stale_keys), family.value)
        logger.debug(
            "STALE_PLANNED",
            extra={
                "family": family,
                "stale_count": len(plan.stale_keys),
                "to_publish_count": len(plan.archives_to_publish),
            },
        )
        return plan

    # endregion METHOD_plan

    # region METHOD_preview
    # PURPOSE: Build a transformed plan without reading or mutating a target so dry run remains externally side-effect free.
    def preview(
        self,
        family: IndexFamily,
        *,
        cancellation: PublicationCancellation | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> PublicationPlan:
        """Return the selected plan without target reconciliation or publication."""
        check = (
            cancellation.check
            if cancellation is not None
            else check_cancelled or _continue
        )
        check()
        raw_index = self.source.fetch(family)
        check()
        logger.debug("SOURCE_FETCHED", extra={"family": family})
        plan = self.selection.select(raw_index)
        if plan.family is not family:
            msg = f"selection returned {plan.family} for {family}"
            raise ValueError(msg)
        logger.info(
            "Selected %s %s release(s), %s archive(s)",
            len(plan.releases),
            family.value,
            len(plan.archives),
        )
        logger.debug(
            "PLAN_SELECTED",
            extra={
                "archive_count": len(plan.archives),
                "family": family,
                "release_count": len(plan.releases),
            },
        )
        return plan

    # endregion METHOD_preview

    # region METHOD_run
    # PURPOSE: Publish a non-empty planned family while preserving the last published state when no origin archive is selected.
    # ENSURES: A successful non-empty plan means archive publication, index replacement, and stale cleanup completed in order.
    def run(
        self,
        family: IndexFamily,
        *,
        cancellation: PublicationCancellation | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> PublicationPlan:
        """Run one safe family publication."""
        controller = cancellation or _CallableCancellation(check_cancelled or _continue)
        plan = self.plan(family, check_cancelled=controller.check)
        if not plan.archives:
            logger.warning(
                "Skipped %s publication: no origin archives selected", family.value
            )
            logger.debug("PUBLICATION_SKIPPED_EMPTY", extra={"family": family})
            return plan

        controller.check()
        self.target.publish_archives(plan, cancellation=controller)
        controller.check()
        logger.debug(
            "ARCHIVES_PUBLISHED",
            extra={"archive_count": len(plan.archives_to_publish), "family": family},
        )

        self.target.replace_index(plan, cancellation=controller)
        controller.check()
        logger.debug("INDEX_REPLACED", extra={"family": family})

        self.target.cleanup_stale(plan, cancellation=controller)
        logger.debug(
            "STALE_CLEANED",
            extra={"family": family, "stale_count": len(plan.stale_keys)},
        )
        return plan

    # endregion METHOD_run


def _continue() -> None:
    """Provide the default no-op cancellation boundary for direct use-case callers."""


@dataclass(frozen=True)
class _CallableCancellation:
    """Adapt a legacy cancellation checker to a non-interrupting target controller."""

    checker: Callable[[], None]

    def check(self) -> None:
        """Delegate the next-boundary check to the caller's original callback."""
        self.checker()

    def interrupt_download(self) -> AbstractContextManager[None]:
        """Keep direct use-case callers cooperative when they do not provide a controller."""
        return nullcontext()


# endregion CLASS_PublishFamily
