"""Inlining of GTS ``$ref`` targets (spec sec 4.3).

Extracted from :class:`~gts.store.GtsStore` so reference resolution is testable
without a full store. Resolution takes a ``provider`` callable that returns a
schema's content by GTS id (or raises :class:`KeyError` when absent) — the
Python analogue of the Rust reference's ``SchemaProvider`` trait.

References are inlined recursively so a schema reached through an intermediate
is fully expanded. Cyclic references are left unresolved on purpose: the
surviving ``$ref`` makes the effective schema unprovable, which is the intended
admission failure.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from .gts import GtsRef
from .schema_dialect import supports_ref_siblings

# A ``$ref`` provider maps a bare GTS id to its schema content, raising
# ``KeyError`` when the id is unknown.
SchemaProvider = Callable[[str], dict]

MAX_SCHEMA_REF_EXPANSIONS = 10_000


def resolve_schema_refs(schema: Any, provider: SchemaProvider) -> Any:
    """Return ``schema`` with external ``$ref`` targets inlined via ``provider``."""
    return inline_refs(
        copy.deepcopy(schema), set(), supports_ref_siblings(schema), provider, [0]
    )


def inline_refs(
    node: Any,
    seen: set[str],
    supports_ref_siblings_flag: bool,
    provider: SchemaProvider,
    expansions: list[int] | None = None,
) -> Any:
    """Recursively inline ``$ref`` references, guarding against cycles."""
    expansions = expansions if expansions is not None else [0]
    if isinstance(node, dict):
        ref_uri = node.get("$ref")
        if isinstance(ref_uri, str):
            ref = GtsRef.parse(ref_uri)
            # Local (#/...) refs are resolved by JSON Schema itself; only
            # external targets are inlined from the provider.
            ref_id = None if ref.is_local else ref.target_id
            if ref_id is not None:
                if ref_id in seen:
                    # Cycle detected: leave the $ref unresolved.
                    return node
                expansions[0] += 1
                if expansions[0] > MAX_SCHEMA_REF_EXPANSIONS:
                    raise ValueError(
                        f"schema reference expansion exceeds limit of "
                        f"{MAX_SCHEMA_REF_EXPANSIONS}"
                    )
                try:
                    ref_schema = provider(ref_id)
                except KeyError:
                    return node  # Leave unresolved

                resolved = inline_refs(
                    copy.deepcopy(ref_schema),
                    seen | {ref_id},
                    supports_ref_siblings(ref_schema),
                    provider,
                    expansions,
                )
                if supports_ref_siblings_flag and len(node) > 1:
                    siblings = {
                        key: value for key, value in node.items() if key != "$ref"
                    }
                    return {
                        "allOf": [
                            resolved,
                            inline_refs(
                                siblings,
                                seen,
                                supports_ref_siblings_flag,
                                provider,
                                expansions,
                            ),
                        ]
                    }
                return resolved
        return {
            key: inline_refs(
                value, seen, supports_ref_siblings_flag, provider, expansions
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [
            inline_refs(item, seen, supports_ref_siblings_flag, provider, expansions)
            for item in node
        ]
    return node
