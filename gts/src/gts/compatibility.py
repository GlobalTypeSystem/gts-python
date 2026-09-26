"""Type Schema evolution / derivation compatibility (spec sec 4, OP#8 & OP#12).

The compatibility relations are defined by accepted-instance-set inclusion,
NOT by structural diffing:

- backward compatibility: ``Valid(old) subset-of Valid(new)`` (new reads old data)
- forward compatibility:  ``Valid(new) subset-of Valid(old)`` (old reads new data)

Rather than re-implement the inclusion engine, this module delegates the
inclusion primitive to the ``jsonsubschema`` library and only adds the GTS
verdict vocabulary on top. Schema derivation (OP#12) reuses the same primitive
via :func:`check_accepted_set_inclusion`.
"""

from __future__ import annotations

from typing import Any

from jsonschema.validators import validator_for
from jsonsubschema import isSubschema

COMPATIBLE = "compatible"
INCOMPATIBLE = "incompatible"
UNKNOWN = "unknown"

# JSON Schema meta keywords and GTS extensions that carry no assertion the
# inclusion engine understands. Stripping them keeps the comparison stable and
# avoids the library reasoning about identifiers or GTS-only annotations.
_META_KEYWORDS = {"$id", "$schema", "$comment", "$anchor", "$dynamicAnchor"}

_NON_ASSERTION_KEYWORDS = {
    "$anchor",
    "$comment",
    "$defs",
    "$dynamicAnchor",
    "$id",
    "$schema",
    "default",
    "definitions",
    "deprecated",
    "description",
    "examples",
    "readOnly",
    "title",
    "writeOnly",
}


def boolean_schema_value(schema: Any) -> bool | None:
    """Return True/False when a schema is boolean-equivalent, else None.

    ``{}`` == True and ``{"not": {}}`` == False; annotation keywords are ignored.
    """
    if isinstance(schema, bool):
        return schema
    if isinstance(schema, dict):
        assertions = [
            (k, v) for k, v in schema.items() if k not in _NON_ASSERTION_KEYWORDS
        ]
        if len(assertions) > 1:
            return None
        if not assertions:
            return True
        key, inner = assertions[0]
        if key == "not":
            iv = boolean_schema_value(inner)
            return None if iv is None else (not iv)
        return None
    return None


def _finite_values(schema: Any) -> list[Any] | None:
    if not isinstance(schema, dict):
        return None
    if "const" in schema:
        return [schema["const"]]
    enum = schema.get("enum")
    return list(enum) if isinstance(enum, list) else None


def _value_constraint_makes_type_redundant(schema: dict[Any, Any]) -> bool:
    if "type" not in schema:
        return False
    values = _finite_values(schema)
    if values is None:
        return False
    try:
        validator = validator_for({"type": schema["type"]})({"type": schema["type"]})
        return all(validator.is_valid(value) for value in values)
    except Exception:  # noqa: BLE001 - intentional broad fallback
        return False


def _finite_subset(subset: Any, superset: Any) -> bool | None:
    values = _finite_values(subset)
    if values is None:
        return None
    try:
        subset_validator = validator_for(subset)(subset)
        superset_validator = validator_for(superset)(superset)
        return all(
            not subset_validator.is_valid(value) or superset_validator.is_valid(value)
            for value in values
        )
    except Exception:  # noqa: BLE001 - intentional broad fallback
        return None


def sanitize(schema: Any) -> Any:
    """Return a copy of ``schema`` with meta/GTS-only keywords removed.

    ``$ref`` is deliberately preserved: callers resolve references before
    comparing, and a surviving ``$ref`` means the target was unresolvable.
    """
    if isinstance(schema, dict):
        # ``jsonsubschema`` rejects some otherwise-valid const/enum schemas with
        # a sibling type. The type can only be removed when it accepts every
        # enumerated value, which leaves the accepted-instance set unchanged.
        drop_type = _value_constraint_makes_type_redundant(schema)
        result = {}
        for key, value in schema.items():
            if key in _META_KEYWORDS:
                continue
            if isinstance(key, str) and key.startswith("x-gts-"):
                continue
            if key == "type" and drop_type:
                continue
            result[key] = sanitize(value)
        return result
    if isinstance(schema, list):
        return [sanitize(item) for item in schema]
    return schema


def _lower_root_unevaluated_properties(schema: Any) -> Any | None:
    if not isinstance(schema, dict) or "unevaluatedProperties" not in schema:
        return schema
    if any(
        key in schema
        for key in (
            "$ref",
            "$dynamicRef",
            "allOf",
            "anyOf",
            "oneOf",
            "if",
            "then",
            "else",
            "not",
            "dependentSchemas",
        )
    ):
        return None
    unevaluated = schema["unevaluatedProperties"]
    if (
        "additionalProperties" in schema
        and schema["additionalProperties"] != unevaluated
    ):
        return None
    result = dict(schema)
    result.pop("unevaluatedProperties")
    result.setdefault("additionalProperties", unevaluated)
    return result


def _coerce_bool_schema(schema: Any) -> Any:
    """Turn a top-level boolean schema into its object-equivalent.

    ``jsonsubschema`` only accepts object schemas as operands, so ``true`` and
    ``false`` are expressed as ``{}`` and ``{"not": {}}`` respectively.
    """
    if schema is True:
        return {}
    if schema is False:
        return {"not": {}}
    return schema


def _is_subschema(subset: Any, superset: Any) -> bool | None:
    """``Valid(subset) subset-of Valid(superset)`` or ``None`` when unprovable."""
    lowered_subset = _lower_root_unevaluated_properties(subset)
    lowered_superset = _lower_root_unevaluated_properties(superset)
    if lowered_subset is None or lowered_superset is None:
        return None
    finite_result = _finite_subset(lowered_subset, lowered_superset)
    if finite_result is not None:
        return finite_result
    try:
        return bool(
            isSubschema(
                _coerce_bool_schema(sanitize(lowered_subset)),
                _coerce_bool_schema(sanitize(lowered_superset)),
            )
        )
    except Exception:  # noqa: BLE001 - intentional broad fallback
        return None


def _verdict(result: bool | None) -> str:
    if result is None:
        return UNKNOWN
    return COMPATIBLE if result else INCOMPATIBLE


def _canonical_dialect(declared: str) -> str:
    body = declared.removesuffix("#")
    return body.removeprefix("https://").removeprefix("http://")


def dialects_differ(old_schema: Any, new_schema: Any) -> bool:
    if not isinstance(old_schema, dict) or not isinstance(new_schema, dict):
        return False
    old_dialect = old_schema.get("$schema")
    new_dialect = new_schema.get("$schema")
    return (
        isinstance(old_dialect, str)
        and isinstance(new_dialect, str)
        and _canonical_dialect(old_dialect) != _canonical_dialect(new_dialect)
    )


def check_backward_compatibility(old_schema: Any, new_schema: Any) -> str:
    """new consumers read old data: ``Valid(old) subset-of Valid(new)``."""
    if dialects_differ(old_schema, new_schema):
        return UNKNOWN
    return _verdict(_is_subschema(old_schema, new_schema))


def check_forward_compatibility(old_schema: Any, new_schema: Any) -> str:
    """old consumers read new data: ``Valid(new) subset-of Valid(old)``."""
    if dialects_differ(old_schema, new_schema):
        return UNKNOWN
    return _verdict(_is_subschema(new_schema, old_schema))


def full_verdict(backward: str, forward: str) -> str:
    if backward == INCOMPATIBLE or forward == INCOMPATIBLE:
        return INCOMPATIBLE
    if backward == COMPATIBLE and forward == COMPATIBLE:
        return COMPATIBLE
    return UNKNOWN


def check_accepted_set_inclusion(subset: Any, superset: Any) -> bool | None:
    """Shared inclusion primitive used by OP#12 derivation admission."""
    return _is_subschema(subset, superset)


# --- diagnostics (spec sec 4.4) -------------------------------------------
#
# The inclusion verdict tells you *whether* two definitions are compatible; a
# caller admitting a new version also wants to know *why* a direction failed and
# whether a level can still gain optional properties later. The Rust reference
# surfaces both (``SchemaComparison`` carries diagnostics plus the content model
# of every object level). We keep ``jsonsubschema`` as the inclusion primitive
# and add the same reporting on top of it.

OPEN = "open"
CLOSED = "closed"
PARTIAL = "partially_open"

_APPLICATOR_OBJECT_KEYWORDS = (
    "properties",
    "patternProperties",
    "$defs",
    "definitions",
)


def _level_content_model(node: dict[str, Any]) -> str:
    """Classify how one object level treats undeclared properties.

    - ``open``: accepts an undeclared property with any value;
    - ``closed``: rejects every undeclared property;
    - ``partially_open``: accepts some undeclared names or constrains their
      values (schema-valued ``additionalProperties``, ``patternProperties`` or
      ``propertyNames``).
    """
    has_pattern = bool(node.get("patternProperties"))
    has_property_names = "propertyNames" in node
    additional = node.get("additionalProperties")
    if additional is None:
        additional_kind = "open"
    else:
        boolean = boolean_schema_value(additional)
        if boolean is True:
            additional_kind = "open"
        elif boolean is False:
            additional_kind = "closed"
        else:
            additional_kind = "schema"

    if additional_kind == "closed" and not has_pattern and not has_property_names:
        return CLOSED
    if additional_kind == "open" and not has_pattern and not has_property_names:
        return OPEN
    return PARTIAL


def _is_object_level(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    if node.get("type") == "object" or "properties" in node:
        return True
    return any(
        key in node
        for key in ("additionalProperties", "patternProperties", "propertyNames")
    )


def classify_object_levels(schema: Any) -> list[dict[str, str]]:
    """Content model of every object level of a (resolved) schema.

    Returns one entry per object level, e.g.
    ``[{"path": "$", "content_model": "closed"}, ...]``. Callers use it to
    report, per level, whether a later definition can add an optional property
    there (only a ``closed`` level can, per spec sec 4.4).
    """
    levels: list[dict[str, str]] = []
    seen: set[int] = set()

    def walk(node: Any, path: str, depth: int) -> None:
        if depth > 64 or not isinstance(node, dict):
            return
        marker = id(node)
        if marker in seen:
            return
        seen.add(marker)

        if _is_object_level(node):
            levels.append({"path": path, "content_model": _level_content_model(node)})

        properties = node.get("properties")
        if isinstance(properties, dict):
            for name, child in properties.items():
                walk(child, f"{path}.{name}", depth + 1)

        # Undeclared-property and pattern-property values are object levels of
        # the instance too, so classify their schemas (a bare boolean/absent
        # additionalProperties carries no nested level).
        pattern_properties = node.get("patternProperties")
        if isinstance(pattern_properties, dict):
            for pattern, child in pattern_properties.items():
                walk(child, f"{path}.patternProperties[{pattern}]", depth + 1)
        additional = node.get("additionalProperties")
        if isinstance(additional, dict):
            walk(additional, f"{path}.additionalProperties", depth + 1)

        # Array element schemas: `items` as a single schema, and the tuple forms
        # (`prefixItems`, or `items`/`additionalItems` as a list).
        items = node.get("items")
        if isinstance(items, dict):
            walk(items, f"{path}[]", depth + 1)
        elif isinstance(items, list):
            for index, child in enumerate(items):
                walk(child, f"{path}[{index}]", depth + 1)
        prefix_items = node.get("prefixItems")
        if isinstance(prefix_items, list):
            for index, child in enumerate(prefix_items):
                walk(child, f"{path}[{index}]", depth + 1)
        additional_items = node.get("additionalItems")
        if isinstance(additional_items, dict):
            walk(additional_items, f"{path}[].additionalItems", depth + 1)

        for combinator in ("allOf", "anyOf", "oneOf"):
            branches = node.get(combinator)
            if isinstance(branches, list):
                for branch in branches:
                    walk(branch, path, depth + 1)

    walk(schema, "$", 0)
    return levels


def explain_verdict(
    verdict: str, *, backward: bool, differing_dialects: bool = False
) -> list[str]:
    """Human-readable reasons for a non-``compatible`` directional verdict.

    Pure function of an already-computed ``verdict`` (``compatible`` /
    ``incompatible`` / ``unknown``); it does not re-run the inclusion check, so
    the caller pays for the accepted-instance-set comparison exactly once.
    ``backward`` selects the direction (backward is ``Valid(old) subset-of
    Valid(new)``, forward the reverse) for message wording; ``differing_dialects``
    distinguishes an ``unknown`` caused by incomparable dialects from one the
    checker could not decide. Returns ``[]`` for a compatible verdict.
    """
    if verdict == COMPATIBLE:
        return []
    if verdict == UNKNOWN:
        if differing_dialects:
            return [
                (
                    "compatibility is unknown: the two definitions declare "
                    "different JSON Schema dialects, so their accepted-instance "
                    "sets are not comparable"
                )
            ]
        return [
            (
                "compatibility is unknown: the accepted-instance-set inclusion "
                "could not be proved or disproved for this direction"
            )
        ]
    if backward:
        return [
            (
                "backward incompatible: Valid(old) is not a subset of Valid(new); "
                "the new definition rejects instances the old definition accepts"
            )
        ]
    return [
        (
            "forward incompatible: Valid(new) is not a subset of Valid(old); the "
            "old definition rejects instances the new definition accepts"
        )
    ]
