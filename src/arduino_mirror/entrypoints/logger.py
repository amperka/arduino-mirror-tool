"""Process-wide logger setup for entry points."""
# region MODULE_CONTRACT
# PURPOSE: Centralize ROOT-logger configuration for every entry point.
# SCOPE: Only logger configuration
# INVARIANTS:
# - configure_logger wires ONE shared LogFormatter instance onto both handlers (the timestamp flag is identical on both)
# - helpers call logging.captureWarnings(True)
# - neither helper calls logging.basicConfig
# KEYWORDS: logger, configure, cli, entrypoints
# endregion MODULE_CONTRACT

from __future__ import annotations

import logging

from arduino_mirror.shared import LogFormatter

__all__ = ["configure_cli_logger"]


# region FUNC_configure_cli_logger
# PURPOSE: Render structured trace records at the operator-selected standard logging threshold without changing global logging configuration owned by an embedding process.
def configure_cli_logger(*, level: int) -> None:
    """Configure the package logger for the selected numeric logging threshold."""
    package_logger = logging.getLogger("arduino_mirror")
    package_logger.setLevel(level)
    package_logger.propagate = False
    for handler in package_logger.handlers:
        if handler.name == "arduino-mirror-cli":
            package_logger.removeHandler(handler)
            handler.close()
            break
    handler = logging.StreamHandler()
    handler.name = "arduino-mirror-cli"
    handler.setFormatter(LogFormatter())
    package_logger.addHandler(handler)


# endregion FUNC_configure_cli_logger
