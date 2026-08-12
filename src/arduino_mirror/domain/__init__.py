"""Re-export domain symbols for cross-layer consumption."""
# region MODULE_CONTRACT
# PURPOSE: Expose the domain layer's public surface from one import path so upper layers depend on arduino_mirror.domain, not internal modules.
# SCOPE:
# - Re-export entities, value objects, ports, events, exceptions, engine types, and settings.
# - NOT: new definitions (this module adds nothing to the domain).
# INVARIANTS: __all__ lists every re-exported symbol.
# KEYWORDS: domain facade, public api, re-export, entities, ports, events, exceptions, settings
# endregion MODULE_CONTRACT

__all__ = []
