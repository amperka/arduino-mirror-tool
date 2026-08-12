"""Type compatibility shims for older Python versions."""
# region MODULE_CONTRACT
# PURPOSE: Maintain forward-compatible type annotations across Python versions without import branching at every call site.
# SCOPE: Python version compat shims for type annotations.
# KEYWORDS: typing compat, python version compat
# endregion MODULE_CONTRACT

import sys

if sys.version_info >= (3, 13):
    from typing import TypeIs
else:
    from typing_extensions import TypeIs

__all__ = [
    "TypeIs"
]
