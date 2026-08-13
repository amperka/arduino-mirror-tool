# region MODULE_CONTRACT
# PURPOSE: Publish one family to an S3-compatible bucket through the MinIO client.
# SCOPE:
# - One-pass family-scoped reconciliation, archive upload, index replacement, and stale cleanup.
# - NOT: HTTP retrieval, selection, local filesystem targets, or CLI parsing.
# KEYWORDS: S3, MinIO, target, publication
# endregion MODULE_CONTRACT

"""S3-compatible publication target."""

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from minio import Minio

from arduino_mirror.domain import Archive, IndexFamily, PublicationPlan

from .archive_tempfile import VerifiedArchive, download_verified
from .retry import DEFAULT_RETRY_POLICY, RetryPolicy, is_transient_s3, retry_call

if TYPE_CHECKING:
    from arduino_mirror.domain import PublicationCancellation


__all__ = ["S3PublicationTarget"]

logger = logging.getLogger(__name__)

_INDEX_NAMES = {
    IndexFamily.PACKAGES: "package_index.json",
    IndexFamily.LIBRARIES: "library_index.json",
}


# region CLASS_S3PublicationTarget
# PURPOSE: Publish verified family archives and indexes to an S3-compatible bucket without exposing storage client details to the application.
class S3PublicationTarget:
    """Map logical target keys to an S3-compatible bucket through MinIO."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        bucket: str,
        endpoint: str = "",
        access_key: str,
        secret_key: str,
        region: str = "",
        prefix: str = "",
        timeout_seconds: float = 600.0,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    ) -> None:
        """Create one S3-compatible target from resolved composition settings."""
        host, secure = _minio_endpoint(endpoint)
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._timeout_seconds = timeout_seconds
        self._retry_policy = retry_policy
        self._client = Minio(
            host,
            access_key=access_key,
            secret_key=secret_key,
            region=region or None,
            secure=secure,
        )

    # region METHOD_reconcile
    # PURPOSE: Build stale and pending archive work from one S3 family listing before publication mutates the target.
    def reconcile(self, plan: PublicationPlan) -> PublicationPlan:
        """Return a plan reconciled with one S3 family listing."""
        prefix = self._object_key(plan.family.value) + "/"
        logical_prefix = f"{plan.family}/"
        listed = retry_call(
            lambda: self._client.list_objects(
                self._bucket,
                prefix=prefix,
                recursive=True,
            ),
            is_retriable=is_transient_s3,
            policy=self._retry_policy,
        )
        present = {
            logical_prefix + name.removeprefix(prefix): item
            for item in listed
            if isinstance(name := item.object_name, str) and name.startswith(prefix)
        }
        stale_keys = tuple(sorted(set(present) - set(plan.archive_keys)))
        archives_to_publish = tuple(
            archive
            for archive in plan.archives
            if not self._archive_is_published(archive, present.get(archive.key))
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
    # PURPOSE: Upload only selected archives that the S3 target cannot already confirm before the family index is replaced.
    def publish_archives(
        self, plan: PublicationPlan, *, cancellation: PublicationCancellation
    ) -> None:
        """Upload every selected archive absent from or unverifiable in the S3 target."""
        for archive in plan.archives_to_publish:
            cancellation.check()
            with download_verified(
                archive,
                self._timeout_seconds,
                plan.family,
                cancellation,
            ) as verified:
                cancellation.check()
                retry_call(
                    lambda a=archive, v=verified: self._put_archive(a, v),
                    is_retriable=is_transient_s3,
                    policy=self._retry_policy,
                    cancellation=cancellation,
                )
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
    # PURPOSE: Replace the family index object only after all selected archives have uploaded.
    def replace_index(
        self, plan: PublicationPlan, *, cancellation: PublicationCancellation
    ) -> None:
        """Overwrite the selected family's published index object."""
        cancellation.check()
        retry_call(
            lambda: self._put_index(plan),
            is_retriable=is_transient_s3,
            policy=self._retry_policy,
            cancellation=cancellation,
        )
        logger.info("Published %s", _INDEX_NAMES[plan.family])
        logger.debug(
            "TARGET_INDEX_REPLACED",
            extra={"family": plan.family, "index_key": _INDEX_NAMES[plan.family]},
        )

    # endregion METHOD_replace_index

    # region METHOD_cleanup_stale
    # PURPOSE: Remove only stale keys supplied by a successful same-family plan.
    def cleanup_stale(
        self, plan: PublicationPlan, *, cancellation: PublicationCancellation
    ) -> None:
        """Delete the supplied family-owned S3 stale object keys."""
        for key in plan.stale_keys:
            cancellation.check()
            retry_call(
                lambda k=key: self._remove_key(k),
                is_retriable=is_transient_s3,
                policy=self._retry_policy,
                cancellation=cancellation,
            )
            logger.info("Removed %s", key)
        logger.debug(
            "TARGET_STALE_CLEANED",
            extra={"family": plan.family, "stale_count": len(plan.stale_keys)},
        )

    # endregion METHOD_cleanup_stale

    # region METHOD__put_archive
    # PURPOSE: Upload one verified archive, rewinding the stream so a retry re-sends full bytes.
    def _put_archive(self, archive: Archive, verified: VerifiedArchive) -> None:
        """Upload one verified archive under its family-owned object key."""
        verified.stream.seek(0)
        self._client.put_object(
            self._bucket,
            self._object_key(archive.key),
            verified.stream,
            length=verified.size,
        )

    # endregion METHOD__put_archive

    # region METHOD__put_index
    # PURPOSE: Serialize and upload one family index from fresh bytes so a retry never sends a partial body.
    def _put_index(self, plan: PublicationPlan) -> None:
        """Upload one family index object from freshly serialized bytes."""
        body = json.dumps(plan.index, ensure_ascii=False, indent=2).encode("utf-8")
        self._client.put_object(
            self._bucket,
            self._object_key(_INDEX_NAMES[plan.family]),
            io.BytesIO(body),
            length=len(body),
            content_type="application/json",
        )

    # endregion METHOD__put_index

    # region METHOD__remove_key
    # PURPOSE: Delete one family-owned stale object key as an idempotent retry unit.
    def _remove_key(self, key: str) -> None:
        """Delete one family-owned stale object key."""
        self._client.remove_object(self._bucket, self._object_key(key))

    # endregion METHOD__remove_key

    # region METHOD__archive_is_published
    # PURPOSE: Confirm a same-key S3 object by its listed declared size without an object-specific request.
    def _archive_is_published(self, archive: Archive, item: object | None) -> bool:
        """Return whether one family-listing item confirms that ``archive`` is available."""
        return (
            item is not None
            and archive.size is not None
            and getattr(item, "size", None) == archive.size
        )

    # endregion METHOD__archive_is_published

    def _object_key(self, key: str) -> str:
        """Prefix one logical key for this configured bucket namespace."""
        return f"{self._prefix}/{key}" if self._prefix else key


# endregion CLASS_S3PublicationTarget


def _minio_endpoint(endpoint: str) -> tuple[str, bool]:
    """Normalize an optional configured endpoint for MinIO client construction."""
    value = endpoint or "storage.yandexcloud.net"
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    return parsed.netloc, parsed.scheme != "http"
