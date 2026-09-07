"""OP#13 - Schema Traits Validation (``x-gts-traits-schema`` / ``x-gts-traits``).

Ported from the Rust reference (`schema_traits.rs`). Validates that trait values
supplied by derived schemas conform to the effective trait schema built from the
whole inheritance chain.

Algorithm:
1. Walk the chain root -> leaf. For each schema collect ``x-gts-traits-schema``
   subschemas (compose via ``allOf``) and ``x-gts-traits`` values (RFC 7396
   merge).
2. Materialize absent trait properties from their ``default`` (never ``const``).
3. Validate the effective values against the effective schema (JSON Schema +
   ``x-gts-ref`` + required-trait completeness, the last only for non-abstract
   types).
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from jsonschema import Draft7Validator
from jsonschema.validators import validator_for

from . import derivation
from .x_gts_ref import XGtsRefValidator

X_GTS_TRAITS_SCHEMA = "x-gts-traits-schema"
X_GTS_TRAITS = "x-gts-traits"
MAX_RECURSION_DEPTH = 64
_MISSING = object()


class EffectiveTraits:
    """Built trait artifacts plus the raw inputs they were composed from."""

    def __init__(
        self,
        schema: Any,
        values: Any,
        resolved_trait_schemas: List[Any],
        merged_traits: Dict[str, Any],
    ) -> None:
        self.schema = schema
        self.values = values
        self.resolved_trait_schemas = resolved_trait_schemas
        self.merged_traits = merged_traits

    def _has_schema(self) -> bool:
        return len(self.resolved_trait_schemas) > 0

    def _has_explicit_values(self) -> bool:
        return isinstance(self.merged_traits, dict) and len(self.merged_traits) > 0

    def validate(self, check_unresolved: bool) -> List[str]:
        """Return a list of error strings (empty means valid)."""
        errors = _validate_trait_schema_integrity(self.resolved_trait_schemas)
        if errors:
            return errors
        errors = _validate_trait_schema_compatibility(self.resolved_trait_schemas)
        if errors:
            return errors

        if not self._has_schema():
            if self._has_explicit_values():
                return [
                    f"{X_GTS_TRAITS} values provided but no {X_GTS_TRAITS_SCHEMA} "
                    "is defined in the inheritance chain"
                ]
            return []

        if _effective_schema_is_false(self.schema):
            if self._has_explicit_values():
                return [
                    f"{X_GTS_TRAITS_SCHEMA} resolves to `false` in the chain - "
                    f"{X_GTS_TRAITS} values are prohibited"
                ]
            return []

        return _validate_trait_values(self.schema, self.values, check_unresolved)


# --- collection ------------------------------------------------------------
def collect_trait_schema_from_value(value: Any, out: List[Any], depth: int = 0) -> None:
    if depth >= MAX_RECURSION_DEPTH or not isinstance(value, dict):
        return
    if X_GTS_TRAITS_SCHEMA in value:
        out.append(copy.deepcopy(value[X_GTS_TRAITS_SCHEMA]))
    all_of = value.get("allOf")
    if isinstance(all_of, list):
        for item in all_of:
            collect_trait_schema_from_value(item, out, depth + 1)


def collect_traits_from_value(
    value: Any, merged: Dict[str, Any], depth: int = 0
) -> None:
    if depth >= MAX_RECURSION_DEPTH or not isinstance(value, dict):
        return
    traits = value.get(X_GTS_TRAITS)
    if isinstance(traits, dict):
        for k, v in traits.items():
            merged[k] = copy.deepcopy(v)
    all_of = value.get("allOf")
    if isinstance(all_of, list):
        for item in all_of:
            collect_traits_from_value(item, merged, depth + 1)


def inline_local_pointers(fragment: Any, root: Any, depth: int = 0) -> Any:
    """Inline JSON Pointer ``#/...`` refs against the host document ``root``."""
    if depth >= MAX_RECURSION_DEPTH:
        return copy.deepcopy(fragment)
    if isinstance(fragment, dict):
        ref = fragment.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            target = _resolve_json_pointer(root, ref[1:])
            if target is not None:
                resolved = inline_local_pointers(target, root, depth + 1)
                if len(fragment) > 1 and isinstance(resolved, dict):
                    for k, v in fragment.items():
                        if k != "$ref":
                            resolved[k] = inline_local_pointers(v, root, depth + 1)
                return resolved
        return {k: inline_local_pointers(v, root, depth + 1) for k, v in fragment.items()}
    if isinstance(fragment, list):
        return [inline_local_pointers(item, root, depth + 1) for item in fragment]
    return copy.deepcopy(fragment)


def _resolve_json_pointer(root: Any, pointer: str) -> Any:
    # pointer begins with '/'
    parts = [p for p in pointer.split("/") if p != ""]
    current = root
    for part in parts:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


# --- RFC 7396 merge --------------------------------------------------------
def merge_rfc7396_into(
    target: Dict[str, Any], patch: Dict[str, Any], depth: int = 0
) -> None:
    if depth >= MAX_RECURSION_DEPTH:
        return
    for k, v in patch.items():
        if v is None:
            target.pop(k, None)
        elif isinstance(v, dict):
            existing = target.get(k)
            if isinstance(existing, dict):
                merge_rfc7396_into(existing, v, depth + 1)
            else:
                fresh: Dict[str, Any] = {}
                merge_rfc7396_into(fresh, v, depth + 1)
                target[k] = fresh
        else:
            target[k] = copy.deepcopy(v)


# --- composition -----------------------------------------------------------
def build_effective_traits_schema(schemas: List[Any]) -> Any:
    if len(schemas) == 0:
        return {}
    if len(schemas) == 1:
        return copy.deepcopy(schemas[0])
    return {"type": "object", "allOf": [copy.deepcopy(s) for s in schemas]}


def build_effective_traits(
    resolved_trait_schemas: List[Any],
    merged_traits: Dict[str, Any],
    dialect: Optional[str],
) -> EffectiveTraits:
    effective_schema = build_effective_traits_schema(resolved_trait_schemas)
    if dialect and isinstance(effective_schema, dict):
        effective_schema["$schema"] = dialect
    values = _materialize_traits(effective_schema, merged_traits)
    return EffectiveTraits(
        schema=effective_schema,
        values=values,
        resolved_trait_schemas=list(resolved_trait_schemas),
        merged_traits=copy.deepcopy(merged_traits),
    )


def _effective_schema_is_false(schema: Any, depth: int = 0) -> bool:
    if depth >= MAX_RECURSION_DEPTH:
        return False
    if schema is False:
        return True
    if isinstance(schema, dict):
        all_of = schema.get("allOf")
        if isinstance(all_of, list):
            return any(_effective_schema_is_false(i, depth + 1) for i in all_of)
    return False


# --- materialization -------------------------------------------------------
def _collect_props(schema: Any, props: List[Tuple[str, Any]], depth: int = 0) -> None:
    if depth >= MAX_RECURSION_DEPTH or not isinstance(schema, dict):
        return
    p = schema.get("properties")
    if isinstance(p, dict):
        for k, v in p.items():
            props.append((k, v))
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for item in all_of:
            _collect_props(item, props, depth + 1)


def _collect_all_properties(schema: Any) -> List[Tuple[str, Any]]:
    props: List[Tuple[str, Any]] = []
    _collect_props(schema, props, 0)
    # keep last occurrence of each name (rightmost wins)
    seen = set()
    deduped: List[Tuple[str, Any]] = []
    for name, sch in reversed(props):
        if name not in seen:
            seen.add(name)
            deduped.append((name, sch))
    deduped.reverse()
    return deduped


def _collect_all_required(schema: Any, req=None, depth: int = 0):
    if req is None:
        req = set()
    if depth >= MAX_RECURSION_DEPTH or not isinstance(schema, dict):
        return req
    required = schema.get("required")
    if isinstance(required, list):
        for item in required:
            if isinstance(item, str):
                req.add(item)
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for item in all_of:
            _collect_all_required(item, req, depth + 1)
    return req


def _materialize_traits(trait_schema: Any, traits: Any, depth: int = 0) -> Any:
    if depth >= MAX_RECURSION_DEPTH:
        return copy.deepcopy(traits)
    result: Dict[str, Any] = dict(traits) if isinstance(traits, dict) else {}

    all_props: List[Tuple[str, Any]] = []
    _collect_props(trait_schema, all_props, 0)

    # Resolve each property once; nearest (most-derived) default wins (leaf->root).
    order: List[str] = []
    resolved: Dict[str, Tuple[Any, Any]] = {}
    for name, sch in reversed(all_props):
        if name not in resolved:
            order.append(name)
            resolved[name] = (sch, _MISSING)
        prop_schema, nearest_default = resolved[name]
        if nearest_default is _MISSING and isinstance(sch, dict) and "default" in sch:
            resolved[name] = (prop_schema, sch["default"])

    for name in order:
        prop_schema, nearest_default = resolved[name]
        if name not in result:
            if nearest_default is not _MISSING:
                result[name] = copy.deepcopy(nearest_default)
        elif (
            isinstance(result.get(name), dict)
            and isinstance(prop_schema, dict)
            and prop_schema.get("type") == "object"
            and "properties" in prop_schema
        ):
            result[name] = _materialize_traits(prop_schema, result[name], depth + 1)

    return result


# --- validation ------------------------------------------------------------
def _validate_trait_schema_integrity(resolved_trait_schemas: List[Any]) -> List[str]:
    for i, ts in enumerate(resolved_trait_schemas):
        if isinstance(ts, bool):
            continue
        if isinstance(ts, dict):
            try:
                cls = validator_for(ts)
                cls.check_schema(ts)
            except Exception as e:
                return [f"{X_GTS_TRAITS_SCHEMA}[{i}] is not a valid JSON Schema: {e}"]
        else:
            return [
                f"{X_GTS_TRAITS_SCHEMA}[{i}] must be an object subschema or a "
                f"boolean; got {ts}"
            ]
    return []


def _validate_trait_schema_compatibility(resolved_trait_schemas: List[Any]) -> List[str]:
    errors: List[str] = []
    for i in range(1, len(resolved_trait_schemas)):
        ancestor_schema = build_effective_traits_schema(resolved_trait_schemas[:i])
        descendant_schema = build_effective_traits_schema(resolved_trait_schemas[: i + 1])
        for err in derivation.validate_derivation(
            ancestor_schema,
            descendant_schema,
            "ancestor trait schema",
            "descendant trait schema",
        ):
            errors.append(
                f"{X_GTS_TRAITS_SCHEMA}[{i}] is incompatible with ancestor trait "
                f"schema: {err}"
            )
        for err in derivation.validate_closed_descendant_branches(
            ancestor_schema,
            resolved_trait_schemas[i],
            "ancestor trait schema",
            "descendant trait schema",
        ):
            errors.append(
                f"{X_GTS_TRAITS_SCHEMA}[{i}] is incompatible with ancestor trait "
                f"schema: {err}"
            )
    return errors


def _strip_required(schema: Any, depth: int = 0) -> Any:
    if depth >= MAX_RECURSION_DEPTH or not isinstance(schema, dict):
        return schema
    out = dict(schema)
    out.pop("required", None)
    all_of = out.get("allOf")
    if isinstance(all_of, list):
        out["allOf"] = [_strip_required(i, depth + 1) for i in all_of]
    return out


def _validate_traits_against_schema(
    trait_schema: Any, effective_traits: Any, check_unresolved: bool
) -> List[str]:
    errors: List[str] = []
    validation_schema = trait_schema if check_unresolved else _strip_required(trait_schema)

    try:
        cls = validator_for(validation_schema)
        validator = cls(validation_schema)
        for error in validator.iter_errors(effective_traits):
            errors.append(f"trait validation: {error.message}")
    except Exception as e:
        errors.append(f"failed to compile trait schema: {e}")

    if not check_unresolved:
        return errors

    all_props = _collect_all_properties(trait_schema)
    required = _collect_all_required(trait_schema)
    traits_obj = effective_traits if isinstance(effective_traits, dict) else {}

    for prop_name, prop_schema in all_props:
        if prop_name not in required:
            continue
        has_value = prop_name in traits_obj
        has_default = isinstance(prop_schema, dict) and "default" in prop_schema
        if not has_value and not has_default:
            expected_type = "any"
            if isinstance(prop_schema, dict) and isinstance(prop_schema.get("type"), str):
                expected_type = prop_schema["type"]
            errors.append(
                f"trait property '{prop_name}' (type: {expected_type}) is not "
                "resolved: no value provided and no default defined in the trait "
                "schema"
            )
    return errors


def _validate_trait_values(
    effective_traits_schema: Any, effective_traits: Any, check_unresolved: bool
) -> List[str]:
    errors = _validate_traits_against_schema(
        effective_traits_schema, effective_traits, check_unresolved
    )
    xref = XGtsRefValidator()
    for err in xref.validate_instance(effective_traits, effective_traits_schema, ""):
        errors.append(f"trait x-gts-ref: {err.reason}")
    return errors
