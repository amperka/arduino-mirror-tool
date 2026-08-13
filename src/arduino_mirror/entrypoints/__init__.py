"""Entrypoints layer facade."""
# region MODULE_CONTRACT
# PURPOSE: Expose the entrypoints layer's public surface (outermost hexagonal layer: driving adapters + composition root) from one import path.
# SCOPE:
# - Layer facade re-exporting eight wiring symbols.
# - NOT: CLI parsing, dependency composition, or adapter implementation.
# KEYWORDS: entrypoints, facade, public api, re-export
# endregion MODULE_CONTRACT

from .cli import main

__all__ = ["main"]
