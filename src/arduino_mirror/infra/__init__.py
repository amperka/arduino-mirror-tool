"""Adapters layer facade — sole public surface for cross-layer consumers (application, composition root)."""
# region MODULE_CONTRACT
# PURPOSE: Provide a single, stable import surface for all infrastructure adapters so application code and entrypoints depend on one package boundary instead of scattered subpackages.
# SCOPE: Re-exports from infra subpackages for cross-layer consumers.
# KEYWORDS: adapters, facade, infra, re-export
# endregion MODULE_CONTRACT

__all__ = []
