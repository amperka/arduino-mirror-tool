"""Shared kernel for cross-layer typing shims."""
# region MODULE_CONTRACT
# PURPOSE: Provide a single-package import surface for shared kernel symbols consumed across architectural layers.
# SCOPE: Re-exports from shared sub-modules. No module-level logic.
# KEYWORDS: shared kernel, re-export, typing shims, cross-layer, compat, log
# endregion MODULE_CONTRACT

from .compat import TypeIs
from .log import LogFormatter
from .validators import validate_interval

__all__ = [
    "LogFormatter",
    "TypeIs",
    "validate_interval"
]
