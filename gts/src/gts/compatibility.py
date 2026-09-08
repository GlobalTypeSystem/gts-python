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
    finite_result = _finite_subset(subset, superset)
    if finite_result is not None:
        return finite_result
    try:
        return bool(
            isSubschema(
                _coerce_bool_schema(sanitize(subset)),
                _coerce_bool_schema(sanitize(superset)),
            )
        )
    except Exception:  # noqa: BLE001 - intentional broad fallback
        return None


def _verdict(result: bool | None) -> str:
    if result is None:
        return UNKNOWN
    return COMPATIBLE if result else INCOMPATIBLE


def check_backward_compatibility(old_schema: Any, new_schema: Any) -> str:
    """new consumers read old data: ``Valid(old) subset-of Valid(new)``."""
    return _verdict(_is_subschema(old_schema, new_schema))


def check_forward_compatibility(old_schema: Any, new_schema: Any) -> str:
    """old consumers read new data: ``Valid(new) subset-of Valid(old)``."""
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
