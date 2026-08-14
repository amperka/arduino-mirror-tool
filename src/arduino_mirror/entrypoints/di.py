# region MODULE_CONTRACT
# PURPOSE: Compose configured publication collaborators at the outermost layer so use cases retain no HTTP, filesystem, or S3 knowledge.
# SCOPE:
# - Factories for CLI publication use cases.
# - NOT: argument parsing, configuration resolution, index selection, or adapter implementation.
# INVARIANTS: One factory creates one family pipeline with its configured source and target; configuration validation happens before composition.
# KEYWORDS: dependency injection, composition root, CLI, publication
# endregion MODULE_CONTRACT

"""Dependency-injection factories for publication entry points."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit

from arduino_mirror.application import (
    LatestLibrariesPolicy,
    LatestPackagesPolicy,
    PublishFamily,
)
from arduino_mirror.domain import IndexFamily, SelectionPolicy
from arduino_mirror.infra import (
    HttpIndexSource,
    LocalPublicationTarget,
    RetryPolicy,
    S3PublicationTarget,
)

from .config import Config, TargetKind

__all__ = ["make_publication_use_case"]

logger = logging.getLogger(__name__)


# region FUNC_make_publication_use_case
# PURPOSE: Give one CLI invocation a source and selected target without leaking concrete adapters into the application layer.
def make_publication_use_case(config: Config) -> PublishFamily:
    """Compose the configured pipeline for one index family."""
    retry_policy = RetryPolicy(
        max_attempts=config.retry_attempts,
        base_delay=config.retry_base_delay,
    )
    source = HttpIndexSource(
        urls={config.family: config.input_index},
        retry_policy=retry_policy,
    )
    target: LocalPublicationTarget | S3PublicationTarget
    match config.target:
        case TargetKind.LOCAL:
            target = LocalPublicationTarget(
                root=config.local_root,
                index_key=config.index_key,
                prefix=config.prefix,
                retry_policy=retry_policy,
            )
        case TargetKind.S3:
            target = S3PublicationTarget(
                bucket=config.bucket,
                endpoint=config.endpoint,
                access_key=config.access_key,
                secret_key=config.secret_key,
                region=config.region,
                index_key=config.index_key,
                prefix=config.prefix,
                retry_policy=retry_policy,
            )
    policy = _policy_for(config)
    logger.debug(
        "PUBLICATION_PIPELINE_COMPOSED",
        extra={"family": config.family, "target": config.target},
    )
    return PublishFamily(source=source, selection=policy, target=target)


# endregion FUNC_make_publication_use_case


def _policy_for(config: Config) -> SelectionPolicy:
    """Create the independent selection policy for the configured family."""
    if config.family is IndexFamily.PACKAGES:
        return LatestPackagesPolicy(
            mirror_host=config.mirror_host,
            origin_host=_origin_of(config.input_index),
            architectures=config.architectures,
            package_names=config.package_names,
            pinned_tools=config.pinned_tools,
        )
    return LatestLibrariesPolicy(
        mirror_host=config.mirror_host,
        origin_host=_origin_of(config.input_index),
    )


def _origin_of(index_url: str) -> str:
    """Derive the configured archive host root from a family index URL."""
    parsed = urlsplit(index_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
