"""Package entry point exposing public client and constants."""
# region MODULE_CONTRACT
# PURPOSE: Expose the public API.
# SCOPE: Public package surface.
# KEYWORDS: package, entrypoint, version, paths, constants
# endregion MODULE_CONTRACT

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("arduino_mirror")
except PackageNotFoundError:
    __version__ = "0.1.0"

__all__ = ["__version__"]
