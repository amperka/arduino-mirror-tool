# region MODULE_CONTRACT
# PURPOSE: Prove CLI logging-level input maps only standard operator-facing names to stdlib thresholds.
# SCOPE: Parser acceptance and rejection for --log-level.
# KEYWORDS: CLI, logging, argument parsing
# endregion MODULE_CONTRACT

"""Unit tests for CLI logging-level parsing."""

from __future__ import annotations

import logging

import pytest

from arduino_mirror.entrypoints.cli import _build_parser


# region FUNC_test_log_level_accepts_standard_names
# PURPOSE: Ensure every documented standard logging name is accepted case-insensitively.
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("debug", logging.DEBUG),
        ("INFO", logging.INFO),
        ("warning", logging.WARNING),
        ("ERROR", logging.ERROR),
        ("critical", logging.CRITICAL),
        ("notset", logging.NOTSET),
    ],
)
def test_log_level_accepts_standard_names(value: str, expected: int) -> None:
    """The parser converts a standard level name to its numeric threshold."""
    args = _build_parser().parse_args(["libraries", "--log-level", value])

    assert args.log_level == expected


# endregion FUNC_test_log_level_accepts_standard_names


# region FUNC_test_log_level_defaults_to_info
# PURPOSE: Keep normal publication progress visible without an explicit verbosity flag.
def test_log_level_defaults_to_info() -> None:
    """The parser configures INFO as the operator-facing default."""
    args = _build_parser().parse_args(["libraries"])

    assert args.log_level == logging.INFO


# endregion FUNC_test_log_level_defaults_to_info


# region FUNC_test_short_log_level_option
# PURPOSE: Keep the documented short option equivalent to --log-level.
def test_short_log_level_option() -> None:
    """The short option configures the requested numeric threshold."""
    args = _build_parser().parse_args(["libraries", "-l", "WARNING"])

    assert args.log_level == logging.WARNING


# endregion FUNC_test_short_log_level_option


# region FUNC_test_log_level_rejects_unknown_name
# PURPOSE: Prevent an invalid operator value from silently changing logging behavior.
def test_log_level_rejects_unknown_name() -> None:
    """The parser exits with an error for a non-standard logging level name."""
    with pytest.raises(SystemExit, match="2"):
        _build_parser().parse_args(["libraries", "--log-level", "verbose"])


# endregion FUNC_test_log_level_rejects_unknown_name
