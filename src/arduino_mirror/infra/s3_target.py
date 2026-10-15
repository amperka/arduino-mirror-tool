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
from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    NotRequired,
    Required,
    TypedDict,
    Unpack,
    cast,
    overload,
)
from urllib.parse import urlsplit

from minio import Minio

from arduino_mirror.domain import ArchiveUnavailableError

from .archive_tempfile import VerifiedArchive, download_verified
from .retry import (
    DEFAULT_RETRY_POLICY,
    RetryContext,
    RetryPolicy,
    is_transient_s3,
    retry_call,
)

if TYPE_CHECKING:
    from arduino_mirror.domain import Archive, PublicationCancellation, PublicationPlan


__all__ = ["S3PublicationTarget", "S3TargetSettings"]

logger = logging.getLogger(__name__)


class _S3TargetSettingsArguments(TypedDict):
    """Legacy keyword representation of S3 target settings."""

    bucket: Required[str]
    access_key: Required[str]
    secret_key: Required[str]
    index_key: Required[str]
    endpoint: NotRequired[str]
    region: NotRequired[str]
    prefix: NotRequired[str]
    timeout_seconds: NotRequired[float]


# region CLASS_S3TargetSettings
# PURPOSE: Keep S3 connection and publication-namespace settings cohesive at the infrastructure boundary.
@dataclass(frozen=True)
class S3TargetSettings:
    """Resolved immutable settings for one S3-compatible publication target."""

    bucket: str
    access_key: str
    secret_key: str
    index_key: str
    endpoint: str = ""
    region: str = ""
    prefix: str = ""
    timeout_seconds: float = 600.0


# endregion CLASS_S3TargetSettings


# region CLASS_S3PublicationTarget
# PURPOSE: Publish verified family archives and indexes to an S3-compatible bucket without exposing storage client details to the application.
class S3PublicationTarget:
    """Map logical target keys to an S3-compatible bucket through MinIO."""

    @overload
    def __init__(
        self,
        settings: S3TargetSettings,
        *,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    ) -> None: ...

    @overload
    def __init__(
        self,
        *,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        **settings_arguments: Unpack[_S3TargetSettingsArguments],
    ) -> None: ...

    def __init__(
        self,
        settings: S3TargetSettings | None = None,
        *,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
        **settings_arguments: object,
    ) -> None:
        """Create one S3-compatible target from resolved composition settings."""
        if settings is None:
            settings = S3TargetSettings(
                **cast("_S3TargetSettingsArguments", settings_arguments)
            )
        elif settings_arguments:
            msg = "pass either S3TargetSettings or individual target settings"
            raise TypeError(msg)
        host, secure = _minio_endpoint(settings.endpoint)
        self._bucket = settings.bucket
        self._index_key = settings.index_key
        self._prefix = settings.prefix.strip("/")
        self._timeout_seconds = settings.timeout_seconds
        self._retry_policy = retry_policy
        self._client = Minio(
            host,
            access_key=settings.access_key,
            secret_key=settings.secret_key,
            region=settings.region or None,
            secure=secure,
        )

    # region METHOD_reconcile
    # PURPOSE: Build stale and pending archive work from one S3 family listing before publication mutates the target.
    def reconcile(self, plan: PublicationPlan) -> PublicationPlan:
        """Return a plan reconciled with one S3 family listing."""
        logical_prefix = f"{plan.family.archive_prefix}/"
        prefix = self._object_key(logical_prefix)
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
            key: item
            for item in listed
            if isinstance(name := item.object_name, str)
            and name.startswith(prefix)
            and (key := logical_prefix + name.removeprefix(prefix)) != self._index_key
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
            try:
                with download_verified(
                    archive,
                    self._timeout_seconds,
                    plan.family,
                    cancellation,
                    retry_policy=self._retry_policy,
                ) as verified:
                    cancellation.check()
                    retry_call(
                        lambda a=archive, v=verified: self._put_archive(a, v),
                        is_retriable=is_transient_s3,
                        policy=self._retry_policy,
                        context=RetryContext(cancellation=cancellation),
                    )
            except Exception as error:
                cancellation.check()
                raise ArchiveUnavailableError(archive.key) from error
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
            context=RetryContext(cancellation=cancellation),
        )
        logger.info("Published %s", self._index_key)
        logger.debug(
            "TARGET_INDEX_REPLACED",
            extra={"family": plan.family, "index_key": self._index_key},
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
                context=RetryContext(cancellation=cancellation),
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
            self._object_key(self._index_key),
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
