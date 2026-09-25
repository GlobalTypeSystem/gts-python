"""JSON Schema dialects a GTS Type Schema may declare (spec sec 2.4 / 11).

GTS admits Draft-07, Draft 2019-09 and Draft 2020-12 and nothing else. This
module is the single home for detecting the dialect a document declares and
mapping it to its canonical meta-schema URI, mirroring the Rust reference's
``schema_dialect`` module. Keeping it out of :class:`~gts.store.GtsStore` makes
the dialect rules unit-testable on their own.
"""

from __future__ import annotations

from typing import Any

# Canonical (http, unfragmented) meta-schema -> short dialect label.
_SUPPORTED = {
    "http://json-schema.org/draft-07/schema": "draft-07",
    "http://json-schema.org/draft/2019-09/schema": "2019-09",
    "http://json-schema.org/draft/2020-12/schema": "2020-12",
}

_DIALECT_URI = {
    "draft-07": "http://json-schema.org/draft-07/schema#",
    "2019-09": "https://json-schema.org/draft/2019-09/schema",
    "2020-12": "https://json-schema.org/draft/2020-12/schema",
}


def document_dialect(schema: dict[str, Any]) -> str:
    """Short label of the dialect ``schema`` declares in its ``$schema``.

    The ``http``/``https`` spellings and a trailing ``#`` fragment are treated
    as equivalent; anything outside the supported set is refused.

    Raises:
        ValueError: if no supported dialect is declared.
    """
    dialect = schema.get("$schema")
    if not isinstance(dialect, str) or not dialect:
        raise ValueError("$schema must declare a supported JSON Schema dialect")
    normalized = dialect.removesuffix("#").lower().replace("https://", "http://", 1)
    try:
        return _SUPPORTED[normalized]
    except KeyError as error:
        raise ValueError(f"Unsupported JSON Schema dialect: {dialect}") from error


def dialect_uri(schema: dict[str, Any]) -> str:
    """Canonical meta-schema URI for the dialect ``schema`` declares."""
    return _DIALECT_URI[document_dialect(schema)]


def supports_ref_siblings(schema: Any) -> bool:
    """Whether the declared dialect evaluates keywords alongside ``$ref``.

    Draft 2019-09 and 2020-12 do; Draft-07 ignores ``$ref`` siblings.
    """
    dialect = schema.get("$schema") if isinstance(schema, dict) else None
    return isinstance(dialect, str) and (
        "/draft/2019-09/" in dialect or "/draft/2020-12/" in dialect
    )


def check_subschemas(schema: dict[str, Any]) -> None:
    """Reject a nested subschema that switches JSON Schema dialect.

    JSON Schema lets any subschema restate ``$schema`` and switch dialect, but
    GTS reads every part of a type under the single dialect its top-level
    ``$schema`` selects (spec sec 11). This mirrors the Rust reference's
    ``schema_dialect::check_subschemas``: a nested ``$schema`` may only restate
    the document's own dialect. An unrecognized nested ``$schema`` is left to
    the meta-schema check rather than reported here.

    Raises:
        ValueError: on the first subschema that changes dialect, by location.
    """
    # Imported lazily to avoid any import-order coupling with schema_validation.
    from .schema_validation import iter_schema_nodes

    root_dialect = document_dialect(schema)
    for node, path in iter_schema_nodes(schema):
        if not path:
            continue  # the document root defines the dialect
        # Trait schemas are dialect-checked by the traits subsystem, which
        # reports a more specific "differs from host dialect" message.
        if any(segment == "x-gts-traits-schema" for segment in path.split("/")):
            continue
        declared = node.get("$schema")
        if not isinstance(declared, str) or not declared:
            continue
        try:
            nested_dialect = document_dialect(node)
        except ValueError:
            continue  # not a recognized dialect; the meta-schema check owns it
        if nested_dialect != root_dialect:
            raise ValueError(
                f"subschema at '{path}' declares dialect {nested_dialect} but the "
                f"type is read under {root_dialect}; a subschema must not change "
                "JSON Schema dialect"
            )
