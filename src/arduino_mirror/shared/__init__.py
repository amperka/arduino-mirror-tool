"""Shared kernel public surface."""
# region MODULE_CONTRACT
# PURPOSE: Provide the shared logging formatter from one stable package import.
# SCOPE: LogFormatter re-export only. No module-level logic.
# KEYWORDS: shared kernel, re-export, logging
# endregion MODULE_CONTRACT

from .log import LogFormatter

__all__ = ["LogFormatter"]
