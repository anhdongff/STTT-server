"""Cache service package.

Provides small, well-tested wrappers around Redis/DiceDB so other modules
don't call Redis directly.
"""

__all__ = ["redis_client"]
