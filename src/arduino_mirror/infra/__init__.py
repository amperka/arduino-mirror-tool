# region MODULE_CONTRACT
# PURPOSE: Expose concrete HTTP, temporary-archive, local, S3, and GCS adapters through one infrastructure boundary.
# SCOPE:
# - Adapter re-exports only.
# - NOT: Adapter behavior, selection policies, or publication orchestration.
# KEYWORDS: infrastructure, HTTP, temporary file, local storage, S3
# endregion MODULE_CONTRACT

"""Infrastructure adapter facade."""

from .archive_tempfile import ArchiveVerificationError, download_verified
from .gcs_target import GcsPublicationTarget
from .http_source import HttpIndexSource
from .local_target import LocalPublicationTarget
from .retry import DEFAULT_RETRY_POLICY, RetryPolicy
from .s3_target import S3PublicationTarget

__all__ = [
    "DEFAULT_RETRY_POLICY",
    "ArchiveVerificationError",
    "GcsPublicationTarget",
    "HttpIndexSource",
    "LocalPublicationTarget",
    "RetryPolicy",
    "S3PublicationTarget",
    "download_verified",
]
