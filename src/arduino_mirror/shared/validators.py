"""Cross-layer numeric-bounds validation primitive."""
# region MODULE_CONTRACT
# PURPOSE: Implement shared guards to validate values.
# SCOPE: validate_interval, MAX_PORT.
# KEYWORDS: validation, interval, bounds, config
# endregion MODULE_CONTRACT

from __future__ import annotations

__all__ = ["validate_interval"]

def validate_interval(
    name: str, value: float, bottom: int | None = None, top: int | None = None
) -> None:
    """Raise ValueError naming `name` if `value` falls outside [bottom, top]."""
    if bottom is not None and value < bottom:
        msg = f"{name} must be >= {bottom}, got {value}"
        raise ValueError(msg)
    if top is not None and value > top:
        msg = f"{name} must be <= {top}, got {value}"
        raise ValueError(msg)
