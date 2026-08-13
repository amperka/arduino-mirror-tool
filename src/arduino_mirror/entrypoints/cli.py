# region MODULE_CONTRACT
# PURPOSE: Let an operator independently plan or publish Arduino package and library indexes.
# SCOPE:
# - Argument parsing, configuration resolution, composition, result reporting, and debug setup.
# - NOT: HTTP, S3, selection-policy, or bootstrap implementation.
# INVARIANTS: Each invocation composes one index-family pipeline; dry run never invokes a target.
# KEYWORDS: CLI, packages, libraries, configuration, dry run
# endregion MODULE_CONTRACT

"""CLI for independent Arduino package and library publication."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import TYPE_CHECKING

from arduino_mirror.domain import IndexFamily

from .config import Config
from .di import make_publication_use_case
from .logger import configure_cli_logger
from .signals import PublicationCancelledError, SignalCancellation

if TYPE_CHECKING:
    from collections.abc import Callable

    from arduino_mirror.domain import PublicationCancellation, PublicationPlan

__all__ = ["main", "run_publication"]

logger = logging.getLogger(__name__)


# region FUNC_run_publication
# PURPOSE: Compose and execute one configured HTTP and storage pipeline while keeping the CLI independent of concrete adapters.
def run_publication(
    config: Config,
    *,
    cancellation: PublicationCancellation | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> PublicationPlan:
    """Run one family pipeline against the configured source and target."""
    use_case = make_publication_use_case(config)
    marker = (
        "PUBLICATION_PREVIEW_REQUESTED"
        if config.dry_run
        else "PUBLICATION_RUN_REQUESTED"
    )
    logger.debug(marker, extra={"family": config.family})
    return (
        use_case.preview(
            config.family,
            cancellation=cancellation,
            check_cancelled=check_cancelled,
        )
        if config.dry_run
        else use_case.run(
            config.family,
            cancellation=cancellation,
            check_cancelled=check_cancelled,
        )
    )


# endregion FUNC_run_publication


# region FUNC_main
# PURPOSE: Let an operator independently plan or publish one configured package or library family.
def main(argv: list[str] | None = None) -> int:
    """Run the selected package or library command."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    configure_cli_logger(level=args.log_level)
    family = IndexFamily(args.family)
    config = Config.from_values(
        family=family,
        values=vars(args),
        environment=os.environ,
    )
    config.validate()
    logger.debug(
        "CLI_CONFIG_RESOLVED",
        extra={
            "dry_run": config.dry_run,
            "family": config.family,
            "target": config.target,
        },
    )

    with SignalCancellation() as cancellation:
        try:
            plan = run_publication(config, cancellation=cancellation)
        except PublicationCancelledError as error:
            logger.warning("Publication cancelled by %s", error.signal_name)
            logger.debug(
                "PUBLICATION_CANCELLED",
                extra={"family": config.family, "signal": error.signal_name},
            )
            return error.exit_code

    outcome = "planned" if config.dry_run else "published"
    sys.stdout.write(
        f"{family}: {outcome} {len(plan.releases)} release(s), "
        f"{len(plan.archive_keys)} archive(s), {len(plan.stale_keys)} stale\n"
    )
    return 0


# endregion FUNC_main


def _build_parser() -> argparse.ArgumentParser:
    """Create the parser with explicit CLI values left unset for precedence resolution."""
    parser = argparse.ArgumentParser(
        prog="arduino-mirror",
        description="Publish filtered Arduino package or library indexes.",
    )
    parser.add_argument(
        "family",
        choices=[family.value for family in IndexFamily],
        help="Index family to publish: packages or libraries.",
    )
    parser.add_argument(
        "--input",
        dest="input_index",
        help="Source index URL; defaults depend on the selected family.",
    )
    parser.add_argument(
        "--mirror-host",
        help="Public base URL used when archive URLs are rewritten in the index.",
    )
    parser.add_argument(
        "--target",
        choices=["s3", "local"],
        help="Publication target: s3 for S3-compatible storage or local for a directory.",
    )
    parser.add_argument("--bucket", help="Destination S3 bucket name.")
    parser.add_argument(
        "--prefix", help="Optional key prefix within the publication target."
    )
    parser.add_argument("--endpoint", help="S3-compatible storage endpoint.")
    parser.add_argument("--region", help="Optional S3 region.")
    parser.add_argument("--local-root", help="Root directory for the local target.")
    parser.add_argument("--access-key", help="S3 access key ID.")
    parser.add_argument("--secret-key", help="S3 secret access key.")
    parser.add_argument(
        "--architectures",
        help="Comma-separated Boards Manager architectures to retain; ignored for libraries.",
    )
    parser.add_argument(
        "--packages",
        dest="package_names",
        help="Comma-separated Boards Manager package names to retain; ignored for libraries.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Plan publication without downloading archives or writing to the target.",
    )
    parser.add_argument(
        "-l",
        "--log-level",
        type=_parse_log_level,
        default=logging.INFO,
        metavar="LEVEL",
        help="Logging threshold: DEBUG, INFO, WARNING, ERROR, CRITICAL, or NOTSET (default: INFO).",
    )
    return parser


def _parse_log_level(value: str) -> int:
    """Convert a case-insensitive standard logging level name to its numeric value."""
    level = logging.getLevelName(value.upper())
    if not isinstance(level, int):
        msg = f"invalid logging level: {value}"
        raise argparse.ArgumentTypeError(msg)
    return level
