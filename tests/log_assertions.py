# region MODULE_CONTRACT
# PURPOSE: Shared helpers for log-driven test assertions against the stdlib `debug(msg, extra=...)` trace contract.
# SCOPE:
# - extra_fields(rec) — reconstruct the structured trace fields dict from a LogRecord by diffing its attributes against the introspection-derived native LogRecord attribute set.
# - NOT: Logger configuration or emission behavior.
# KEYWORDS: log-driven assertions, extra_fields, LogRecord structured data
# endregion MODULE_CONTRACT

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from arduino_mirror.shared.log import _NATIVE_KEYS

if TYPE_CHECKING:
    import logging

__all__ = ["extra_fields"]

# ``LogRecord.getMessage()`` lazily caches this convenience attribute. It is
# derived by the test assertion, not supplied through ``extra``.
_DERIVED_KEYS = frozenset({"message"})


# region FUNC_extra_fields
# PURPOSE: Let trace tests assert only application-supplied structured fields without coupling to Python LogRecord internals.
def extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    """Return structured fields supplied through the logging ``extra`` channel."""
    return {
        key: getattr(record, key)
        for key in record.__dict__
        if key not in _NATIVE_KEYS | _DERIVED_KEYS
    }


# endregion FUNC_extra_fields
