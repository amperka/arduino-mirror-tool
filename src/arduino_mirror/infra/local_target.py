# region MODULE_CONTRACT
# PURPOSE: Publish one family to a local directory tree for offline operation and end-to-end verification.
# SCOPE: Family-scoped local archive reconciliation and atomic index replacement.
# NOT: HTTP retrieval, selection, S3, or CLI parsing.
# KEYWORDS: local storage, target, publication
# endregion MODULE_CONTRACT

"""Local filesystem publication target."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from shutil import copyfileobj
from typing import TYPE_CHECKING, Any

from arduino_mirror.domain import IndexFamily, PublicationPlan

from .archive_tempfile import download_verified, is_file_verified

if TYPE_CHECKING:
    from arduino_mirror.domain import PublicationCancellation

__all__ = ["LocalPublicationTarget"]

logger = logging.getLogger(__name__)

_INDEX_NAMES = {
    IndexFamily.PACKAGES: "package_index.json",
    IndexFamily.LIBRARIES: "library_index.json",
}


# region CLASS_LocalPublicationTarget
# PURPOSE: Publish verified family archives and indexes to a local tree for offline operation and end-to-end verification.
@dataclass(frozen=True)
class LocalPublicationTarget:
    """Map logical target keys to a local directory tree."""

    root: Path
    prefix: str = ""
    timeout_seconds: float = 600.0

    # region METHOD_reconcile
    # PURPOSE: Build stale and pending archive work from one family directory scan before publication mutates the target.
    def reconcile(self, plan: PublicationPlan) -> PublicationPlan:
        """Return a plan reconciled with one scan of its local family directory."""
        directory = self._path(plan.family.value)
        present = (
            {
                f"{plan.family}/{file.relative_to(directory).as_posix()}": file
                for file in directory.rglob("*")
                if file.is_file()
            }
            if directory.exists()
            else {}
        )
        stale_keys = tuple(sorted(set(present) - set(plan.archive_keys)))
        archives_to_publish = tuple(
            archive
            for archive in plan.archives
            if (file := present.get(archive.key)) is None
            or not is_file_verified(archive, file)
        )
        reconciled = plan.with_reconciliation(
            stale_keys=stale_keys,
            archives_to_publish=archives_to_publish,
        )
        logger.debug(
            "TARGET_ARCHIVE_KEYS_LISTED",
            extra={"archive_count": len(present), "family": plan.family},
        )
        return reconciled

    # endregion METHOD_reconcile

    # region METHOD_publish_archives
    # PURPOSE: Download, verify, and publish only selected archives that the local target cannot already confirm.
    def publish_archives(
        self, plan: PublicationPlan, *, cancellation: PublicationCancellation
    ) -> None:
        """Publish every selected archive absent from or unverifiable in the local target."""
        for archive in plan.archives_to_publish:
            cancellation.check()
            destination = self._path(archive.key)
            with download_verified(
                archive, self.timeout_seconds, plan.family, cancellation
            ) as verified:
                cancellation.check()
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("wb") as file:
                    copyfileobj(verified.stream, file)
            logger.info("Published %s", archive.key)
        logger.debug(
            "TARGET_ARCHIVES_PUBLISHED",
            extra={
                "archive_count": len(plan.archives_to_publish),
                "family": plan.family,
            },
        )

    # endregion METHOD_publish_archives

    # region METHOD_replace_index
    # PURPOSE: Atomically replace one family index only after its archives are available.
    def replace_index(
        self, plan: PublicationPlan, *, cancellation: PublicationCancellation
    ) -> None:
        """Atomically write the selected family's transformed index."""
        cancellation.check()
        destination = self._path(_INDEX_NAMES[plan.family])
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(destination, plan.index, cancellation=cancellation)
        logger.info("Published %s", _INDEX_NAMES[plan.family])
        logger.debug(
            "TARGET_INDEX_REPLACED",
            extra={"family": plan.family, "index_key": _INDEX_NAMES[plan.family]},
        )

    # endregion METHOD_replace_index

    # region METHOD_cleanup_stale
    # PURPOSE: Remove only obsolete archive keys after the family index replacement succeeds.
    def cleanup_stale(
        self, plan: PublicationPlan, *, cancellation: PublicationCancellation
    ) -> None:
        """Delete the supplied family-owned stale archive keys."""
        for key in plan.stale_keys:
            cancellation.check()
            path = self._path(key)
            if path.is_file():
                path.unlink()
                logger.info("Removed %s", key)
        logger.debug(
            "TARGET_STALE_CLEANED",
            extra={"family": plan.family, "stale_count": len(plan.stale_keys)},
        )

    # endregion METHOD_cleanup_stale

    def _path(self, key: str) -> Path:
        """Map one logical key to the configured local storage root."""
        prefix = self.prefix.strip("/")
        return self.root / prefix / key if prefix else self.root / key


# endregion CLASS_LocalPublicationTarget


def _atomic_write_json(
    destination: Path, value: dict[str, Any], *, cancellation: PublicationCancellation
) -> None:
    """Atomically replace a local JSON index file with UTF-8 content."""
    handle, temporary = tempfile.mkstemp(dir=destination.parent, suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
        cancellation.check()
        Path(temporary).replace(destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
