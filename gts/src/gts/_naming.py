"""Internal naming primitives for GTS identifiers.

This module is the single, **internal** home for the low-level string handling
of GTS identifiers:

- the ``gts://`` URI scheme,
- the bare ``gts.`` prefix, and
- the ``~`` type marker.

It is deliberately private (underscore-prefixed and absent from the public
package exports). Consumers of the SDK should never reach for these primitives:
they work with the :class:`~gts.gts.GtsID` value object and high-level
operations (validate, cast, resolve, store lookups, ...), all of which normalize
identifiers internally. Keeping this logic in one private place stops it from
leaking across the library and onto the public API surface.
"""

from __future__ import annotations

# Distinguishes GTS identifiers from other strings; also used for URI encoding.
GTS_PREFIX = "gts."
GTS_URI_PREFIX = "gts://"
# Separates the segments of a chained identifier and, at the very end, marks a
# type identifier (e.g. ``gts.acme.pkg._.user.v1~``).
GTS_TYPE_MARKER = "~"


def strip_scheme(value: str) -> str:
    """Return the canonical bare form, dropping any ``gts://`` scheme.

    Non-GTS strings are returned unchanged, so this is safe to call on arbitrary
    registry keys or ``$ref`` targets at a boundary.
    """
    return value.removeprefix(GTS_URI_PREFIX)


def has_scheme(value: str) -> bool:
    """True if ``value`` carries the ``gts://`` URI scheme."""
    return value.startswith(GTS_URI_PREFIX)


def with_scheme(value: str) -> str:
    """Return the ``gts://`` URI encoding of ``value`` (idempotent)."""
    return value if has_scheme(value) else GTS_URI_PREFIX + value


def looks_like_gts(value: str) -> bool:
    """True if ``value`` looks like a GTS identifier in either encoding.

    A cheap prefix check (bare ``gts.`` or ``gts://``); it does not fully
    validate the identifier.
    """
    return value.startswith((GTS_URI_PREFIX, GTS_PREFIX))


def is_type_ref(value: str) -> bool:
    """True if the (scheme-stripped) identifier denotes a type.

    Type identifiers end with the type marker ``~``; instance identifiers do
    not.
    """
    return strip_scheme(value).endswith(GTS_TYPE_MARKER)
