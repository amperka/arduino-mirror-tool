# region MODULE_CONTRACT
# PURPOSE: Stream, verify, and temporarily retain selected origin archives until a target has safely published their bytes.
# SCOPE:
# - Streamed HTTP download, in-memory-to-disk spooling, SHA-256 and declared-size validation, and temporary-resource cleanup.
# - NOT: Index retrieval, selection, or storage publication.
# INVARIANTS: A verified archive is available only inside its context manager; closing the context removes any spooled temporary file.
# KEYWORDS: archive, temporary file, spool, checksum, HTTP
# endregion MODULE_CONTRACT

"""Verified origin archive retrieval through automatically cleaned temporary files."""

from __future__ import annotations

import hashlib
import logging
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO, cast

import requests

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from arduino_mirror.domain import Archive, IndexFamily, PublicationCancellation

__all__ = [
    "ArchiveVerificationError",
    "VerifiedArchive",
    "download_verified",
    "is_file_verified",
]

logger = logging.getLogger(__name__)

_SPOOL_MAX_MEMORY_BYTES = 16 * 1024 * 1024


# region CLASS_ArchiveVerificationError
# PURPOSE: Stop publication before index replacement when downloaded origin bytes differ from supplied integrity metadata.
class ArchiveVerificationError(ValueError):
    """An origin archive failed its declared SHA-256 or size validation."""

    def __init__(self, check: str, key: str) -> None:
        """Describe the failed integrity check and selected archive key."""
        super().__init__(f"{check} mismatch for {key}")


# endregion CLASS_ArchiveVerificationError


# region CLASS_VerifiedArchive
# PURPOSE: Give a target a rewindable verified stream and its measured byte length during one temporary-resource lifetime.
@dataclass(frozen=True)
class VerifiedArchive:
    """A verified archive stream that is valid only in its download context."""

    stream: BinaryIO
    size: int


# endregion CLASS_VerifiedArchive


# region FUNC_is_file_verified
# PURPOSE: Confirm that an already stored archive satisfies the selected source record without downloading it again.
def is_file_verified(archive: Archive, path: Path) -> bool:
    """Return whether ``path`` has the selected archive's declared size or SHA-256."""
    if not path.is_file():
        return False
    if archive.size is not None and path.stat().st_size == archive.size:
        return True
    if archive.sha256 is None:
        return False
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest() == archive.sha256


# endregion FUNC_is_file_verified


# region FUNC_download_verified
# PURPOSE: Stream and validate one origin archive before a target can expose its bytes to Arduino clients.
@contextmanager
def download_verified(
    archive: Archive,
    timeout_seconds: float,
    family: IndexFamily,
    cancellation: PublicationCancellation,
) -> Iterator[VerifiedArchive]:
    """Yield a rewindable verified temporary archive stream and clean it on context exit."""
    logger.info("Downloading %s", archive.key)
    logger.debug(
        "ARCHIVE_DOWNLOAD_STARTED",
        extra={"archive_key": archive.key, "family": family},
    )
    hasher = hashlib.sha256()
    size = 0
    with tempfile.SpooledTemporaryFile(
        max_size=_SPOOL_MAX_MEMORY_BYTES, mode="w+b"
    ) as temporary:
        with (
            cancellation.interrupt_download(),
            requests.get(
                archive.source_url, stream=True, timeout=timeout_seconds
            ) as response,
        ):
            response.raise_for_status()
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                cancellation.check()
                if chunk:
                    temporary.write(chunk)
                    hasher.update(chunk)
                    size += len(chunk)
        cancellation.check()
        _raise_if_invalid(archive, family, hasher.hexdigest(), size)
        temporary.seek(0)
        logger.info("Verified %s (%s bytes)", archive.key, size)
        logger.debug(
            "ARCHIVE_VERIFIED",
            extra={"archive_key": archive.key, "family": family, "size": size},
        )
        yield VerifiedArchive(stream=cast("BinaryIO", temporary), size=size)


# endregion FUNC_download_verified


def _raise_if_invalid(
    archive: Archive, family: IndexFamily, checksum: str, size: int
) -> None:
    """Raise a traced verification error when the streamed bytes contradict source metadata."""
    check = None
    if archive.sha256 is not None and checksum != archive.sha256:
        check = "checksum"
    elif archive.size is not None and size != archive.size:
        check = "size"
    if check is None:
        return
    logger.debug(
        "ARCHIVE_VERIFICATION_FAILED",
        extra={"archive_key": archive.key, "check": check, "family": family},
    )
    raise ArchiveVerificationError(check, archive.key)
