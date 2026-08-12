# region MODULE_CONTRACT
# PURPOSE: Shared helpers for log-driven test assertions against the stdlib `debug(msg, extra=...)` trace contract.
# SCOPE: extra_fields(rec) — reconstruct the structured trace fields dict from a LogRecord by diffing its attributes against the introspection-derived native LogRecord attribute set.
# KEYWORDS: log-driven assertions, extra_fields, LogRecord structured data
# endregion MODULE_CONTRACT

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from arduino_mirror.shared.log import _NATIVE_KEYS

if TYPE_CHECKING:
    import logging


def extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {k: getattr(record, k) for k in record.__dict__ if k not in _NATIVE_KEYS}
