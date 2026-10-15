"""Native Google Cloud Storage publication target."""
# region MODULE_CONTRACT
# PURPOSE: Publish verified family archives and indexes to GCS using refreshable ADC without exposing storage details to the application.
# SCOPE:
# - Lazy SDK construction, family-prefix reconciliation, bounded uploads, index replacement, and stale cleanup.
# - NOT: Credential configuration, selection, orchestration, or S3 behavior.
# INVARIANTS: SDK storage retries are disabled; the shared retry engine owns the attempt budget. Constructing a target performs no credential discovery or cloud I/O.
# KEYWORDS: GCS, ADC, WIF, publication, retry
# endregion MODULE_CONTRACT

from __future__ import annotations

import json
import logging
from contextlib import suppress
from functools import cached_property
from typing import TYPE_CHECKING

from google.api_core.exceptions import GoogleAPICallError, NotFound
from google.cloud import storage
from google.cloud.storage.exceptions import InvalidResponse

from arduino_mirror.domain import ArchiveUnavailableError

from .archive_tempfile import VerifiedArchive, download_verified
from .retry import DEFAULT_RETRY_POLICY, RetryPolicy, is_transient_http, retry_call

if TYPE_CHECKING:
    from arduino_mirror.domain import Archive, PublicationCancellation, PublicationPlan

__all__ = ["GcsPublicationTarget"]

logger = logging.getLogger(__name__)

_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_SERVER_ERROR_MIN = 500
_HTTP_SERVER_ERROR_MAX = 599


# region CLASS_GcsPublicationTarget
# PURPOSE: Preserve the publication port's family ownership and verified-transfer guarantees using native GCS objects and ADC.
class GcsPublicationTarget:
    """Map logical publication keys to an optional prefix in one GCS bucket."""

    def __init__(
        self,
        *,
        bucket: str,
        index_key: str,
        prefix: str = "",
        timeout_seconds: float = 600.0,
        retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    ) -> None:
        """Store resolved settings without discovering credentials or accessing GCS."""
        self._bucket_name = bucket
        self._index_key = index_key
        self._prefix = prefix.strip("/")
        self._timeout_seconds = timeout_seconds
        self._retry_policy = retry_policy

    # region METHOD__bucket
    # PURPOSE: Defer ADC until the first storage operation and avoid unnecessary project discovery for an existing bucket.
    @cached_property
    def _bucket(self) -> storage.Bucket:
        """Create one cached bucket handle without fetching bucket metadata."""
        # Explicit None disables SDK project discovery; bucket operations need no project.
        return storage.Client(project=None).bucket(self._bucket_name)

    # endregion METHOD__bucket

    # region METHOD_reconcile
    # PURPOSE: Confirm selected archives by listed size and identify only same-family stale objects from one complete inventory.
    def reconcile(self, plan: PublicationPlan) -> PublicationPlan:
        """Return pending and stale logical archive keys from a family-prefix listing."""
        logical_prefix = f"{plan.family.archive_prefix}/"
        prefix = self._object_key(logical_prefix)
        listed = retry_call(
            lambda: list(
                self._bucket.list_blobs(
                    prefix=prefix, timeout=self._timeout_seconds, retry=None
                )
            ),
            is_retriable=_is_transient_gcs,
            policy=self._retry_policy,
        )
        present = {
            key: item
            for item in listed
            if isinstance(name := item.name, str)
            and name.startswith(prefix)
            and (key := logical_prefix + name.removeprefix(prefix)) != self._index_key
        }
        reconciled = plan.with_reconciliation(
            stale_keys=tuple(sorted(set(present) - set(plan.archive_keys))),
            archives_to_publish=tuple(
                archive
                for archive in plan.archives
                if not self._archive_is_published(archive, present.get(archive.key))
            ),
        )
        logger.debug(
            "TARGET_ARCHIVE_KEYS_LISTED",
            extra={"archive_count": len(present), "family": plan.family},
        )
        return reconciled

    # endregion METHOD_reconcile

    # region METHOD_publish_archives
    # PURPOSE: Make every unconfirmed archive available from verified temporary bytes before the family index can be replaced.
    def publish_archives(
        self, plan: PublicationPlan, *, cancellation: PublicationCancellation
    ) -> None:
        """Download, verify, and upload unconfirmed archives with bounded retries."""
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
                        is_retriable=_is_transient_gcs,
                        policy=self._retry_policy,
                        cancellation=cancellation,
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
    # PURPOSE: Replace exactly the configured family index object after all referenced archives are available.
    def replace_index(
        self, plan: PublicationPlan, *, cancellation: PublicationCancellation
    ) -> None:
        """Upload the transformed family index as UTF-8 JSON in one object write."""
        cancellation.check()
        retry_call(
            lambda: self._put_index(plan),
            is_retriable=_is_transient_gcs,
            policy=self._retry_policy,
            cancellation=cancellation,
        )
        logger.info("Published %s", self._index_key)
        logger.debug(
            "TARGET_INDEX_REPLACED",
            extra={"family": plan.family, "index_key": self._index_key},
        )

    # endregion METHOD_replace_index

    # region METHOD_cleanup_stale
    # PURPOSE: Remove only the reconciled stale keys owned by the successful family plan.
    def cleanup_stale(
        self, plan: PublicationPlan, *, cancellation: PublicationCancellation
    ) -> None:
        """Delete reconciled stale archive objects, accepting an already absent key."""
        for key in plan.stale_keys:
            cancellation.check()
            retry_call(
                lambda k=key: self._remove_key(k),
                is_retriable=_is_transient_gcs,
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
    # PURPOSE: Restart each upload attempt from the complete verified stream rather than a partially consumed body.
    def _put_archive(self, archive: Archive, verified: VerifiedArchive) -> None:
        """Upload one verified archive with SDK storage retries disabled."""
        verified.stream.seek(0)
        self._bucket.blob(self._object_key(archive.key)).upload_from_file(
            verified.stream,
            size=verified.size,
            rewind=True,
            timeout=self._timeout_seconds,
            retry=None,
        )

    # endregion METHOD__put_archive

    # region METHOD__put_index
    # PURPOSE: Give every index upload attempt a fresh complete JSON body.
    def _put_index(self, plan: PublicationPlan) -> None:
        """Serialize a family index as UTF-8 without ASCII-escaping its content."""
        body = json.dumps(plan.index, ensure_ascii=False, indent=2).encode("utf-8")
        self._bucket.blob(self._object_key(self._index_key)).upload_from_string(
            body,
            content_type="application/json",
            timeout=self._timeout_seconds,
            retry=None,
        )

    # endregion METHOD__put_index

    # region METHOD__remove_key
    # PURPOSE: Make stale deletion idempotent even if a previous attempt succeeded before its response was lost.
    def _remove_key(self, key: str) -> None:
        """Delete one stale object with SDK retries disabled, ignoring NotFound."""
        with suppress(NotFound):
            self._bucket.blob(self._object_key(key)).delete(
                timeout=self._timeout_seconds, retry=None
            )

    # endregion METHOD__remove_key

    # region METHOD__archive_is_published
    # PURPOSE: Confirm an existing archive solely from listed declared size without an additional object request.
    def _archive_is_published(self, archive: Archive, item: object | None) -> bool:
        """Confirm an archive only when its declared size matches the listing."""
        return (
            item is not None
            and archive.size is not None
            and getattr(item, "size", None) == archive.size
        )

    # endregion METHOD__archive_is_published

    def _object_key(self, key: str) -> str:
        """Map one logical key beneath the configured storage-only prefix."""
        return f"{self._prefix}/{key}" if self._prefix else key


# endregion CLASS_GcsPublicationTarget


# region FUNC__is_transient_gcs
# PURPOSE: Restrict GCS retries to public throttle/server status codes and eligible requests transport failures, excluding auth and permanent errors.
def _is_transient_gcs(error: BaseException) -> bool:
    """Classify public SDK exceptions without relying on private retry predicates."""
    if isinstance(error, GoogleAPICallError):
        status = error.code
    elif isinstance(error, InvalidResponse):
        status = getattr(error.response, "status_code", None)
    else:
        return is_transient_http(error)
    return isinstance(status, int) and (
        status == _HTTP_TOO_MANY_REQUESTS
        or _HTTP_SERVER_ERROR_MIN <= status <= _HTTP_SERVER_ERROR_MAX
    )


# endregion FUNC__is_transient_gcs
