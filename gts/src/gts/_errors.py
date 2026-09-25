"""Structured GTS exception hierarchy.

The Rust reference models store failures as a single ``StoreError`` enum whose
variants (not-found, invalid, conflict, unresolved-ref, ...) let callers react
by category rather than by matching on message text. The idiomatic Python
analogue is a small exception hierarchy rooted at :class:`GtsError`.

:class:`GtsValidationError` also derives from the builtin :class:`ValueError`:
the library historically raised bare ``ValueError`` for validation failures and
callers/tests catch it, so keeping that base preserves backward compatibility
while letting new code catch :class:`GtsError` / :class:`GtsValidationError`.
"""

from __future__ import annotations


class GtsError(Exception):
    """Base class for every error raised by the GTS library."""


class GtsNotFoundError(GtsError):
    """An entity, schema or instance was not present in the store."""


class GtsValidationError(GtsError, ValueError):
    """An entity failed GTS validation.

    Subclasses :class:`ValueError` so existing ``except ValueError`` callers
    keep working while new code can catch the narrower GTS categories.
    """


class GtsConflictError(GtsError):
    """An entity id is already registered with different content."""

    def __init__(self, entity_id: str, message: str | None = None) -> None:
        self.entity_id = entity_id
        super().__init__(
            message
            or f"Entity '{entity_id}' is already registered with different content"
        )


class GtsUnresolvedRefError(GtsValidationError):
    """A ``$ref`` or ``x-gts-ref`` target could not be resolved."""
