"""OP#12 - Schema-vs-schema derivation admission (spec sec 4.1).

Ported from the Rust reference (`schema_derivation.rs`). Admission requires
``Valid(derived) subset-of Valid(base)`` on the most-derived *declaration* of
each property, plus two admission rules that inclusion alone does not express:

- a derivation may not switch off a base property with ``false``
- a derivation may not close a nested object level in a way that orphans an
  ancestor property under ``allOf`` composition

The inclusion primitive is provided by :mod:`gts.compatibility` (backed by
``jsonsubschema``); this module only supplies the declaration reduction and the
admission rules.
"""

from __future__ import annotations

import copy
from typing import Any

from .compatibility import boolean_schema_value, check_accepted_set_inclusion

MAX_RECURSION_DEPTH = 64
_ADDITIONAL = "additionalProperties"
_STRUCTURAL = {"properties", "required", _ADDITIONAL}


def validate_derivation_compatibility(
    base_schema: Any,
    derived_schema: Any,
    base_id: str,
    derived_id: str,
) -> list[str]:
    """Full OP#12 admission check on resolved base/derived schemas."""
    errors = _validate_derivation(base_schema, derived_schema, base_id, derived_id)
    errors.extend(
        _validate_closed_descendant_branches(
            base_schema, derived_schema, base_id, derived_id
        )
    )
    return errors


def validate_derivation(
    base_schema: Any,
    derived_schema: Any,
    base_id: str,
    derived_id: str,
) -> list[str]:
    """Declaration inclusion check without the closed-descendant branch rule."""
    return _validate_derivation(base_schema, derived_schema, base_id, derived_id)


def validate_closed_descendant_branches(
    ancestor_schema: Any,
    descendant_schema: Any,
    ancestor_label: str,
    descendant_label: str,
) -> list[str]:
    return _validate_closed_descendant_branches(
        ancestor_schema, descendant_schema, ancestor_label, descendant_label
    )


# --- declaration inclusion -------------------------------------------------
def _validate_derivation(
    base_schema: Any,
    derived_schema: Any,
    base_id: str,
    derived_id: str,
) -> list[str]:
    base = _declared_schema(base_schema, 0)
    derived = _declared_schema(derived_schema, 0)
    errors: list[str] = []

    # An omitted additionalProperties inherits the base's constraint through
    # allOf composition rather than reopening the level.
    if (
        isinstance(derived, dict)
        and _ADDITIONAL not in derived
        and isinstance(base, dict)
        and _ADDITIONAL in base
    ):
        derived[_ADDITIONAL] = copy.deepcopy(base[_ADDITIONAL])

    if (
        isinstance(base, dict)
        and boolean_schema_value(base.get(_ADDITIONAL)) is False
        and isinstance(derived_schema, dict)
        and _ADDITIONAL in derived_schema
        and boolean_schema_value(derived_schema.get(_ADDITIONAL)) is not False
    ):
        errors.append(
            f"derived schema '{derived_id}' loosens additionalProperties from a "
            f"closed constraint in base '{base_id}'"
        )

    # Admission fails closed: an unprovable inclusion is rejected.
    if check_accepted_set_inclusion(derived, base) is not True:
        errors.append(
            f"derived schema '{derived_id}' is not included in base '{base_id}': "
            "the declared schema accepts instances the base rejects"
        )

    _collect_disabled_base_properties(base, derived, base_id, derived_id, errors)
    return errors


def _declared_schema(schema: Any, depth: int) -> Any:
    if not isinstance(schema, dict):
        return copy.deepcopy(schema)
    if depth >= MAX_RECURSION_DEPTH:
        return copy.deepcopy(schema)
    declared: dict[str, Any] = {}
    additional: list[Any] = [None]

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for branch in all_of:
            branch_declared = _declared_schema(branch, depth + 1)
            if isinstance(branch_declared, dict):
                _absorb_declaration(declared, additional, branch_declared, depth)
    _absorb_declaration(declared, additional, schema, depth)

    if additional[0] is not None:
        declared[_ADDITIONAL] = additional[0]
    return declared


def _absorb_declaration(
    declared: dict[str, Any],
    additional: list[Any],
    source: dict[str, Any],
    depth: int,
) -> None:
    for keyword, value in source.items():
        if keyword == "allOf":
            continue
        if keyword == _ADDITIONAL:
            _merge_additional_properties_constraint(additional, value)
        elif keyword == "properties":
            target = declared.setdefault("properties", {})
            if isinstance(target, dict) and isinstance(value, dict):
                for name, prop in value.items():
                    prop_declared = _declared_schema(prop, depth + 1)
                    if name in target:
                        target[name] = _absorb_property(
                            target[name], prop_declared, depth + 1
                        )
                    else:
                        target[name] = prop_declared
        elif keyword == "required":
            target = declared.setdefault("required", [])
            if isinstance(target, list) and isinstance(value, list):
                for name in value:
                    if name not in target:
                        target.append(name)
        else:
            declared[keyword] = copy.deepcopy(value)


def _absorb_property(inherited: Any, overlay: Any, depth: int) -> Any:
    if (
        not isinstance(inherited, dict)
        or not isinstance(overlay, dict)
        or depth >= MAX_RECURSION_DEPTH
    ):
        return copy.deepcopy(overlay)

    composed: dict[str, Any] = {
        k: copy.deepcopy(v) for k, v in overlay.items() if k not in _STRUCTURAL
    }
    additional: list[Any] = [inherited.get(_ADDITIONAL)]
    for keyword in ("properties", "required"):
        if keyword in inherited:
            composed[keyword] = copy.deepcopy(inherited[keyword])
    _absorb_declaration(composed, additional, overlay, depth)
    if additional[0] is not None:
        composed[_ADDITIONAL] = additional[0]
    return composed


def _merge_additional_properties_constraint(
    additional: list[Any], candidate: Any
) -> None:
    current = additional[0]
    if boolean_schema_value(current) is False:
        return
    if boolean_schema_value(candidate) is True and current is not None:
        return
    additional[0] = copy.deepcopy(candidate)


def _collect_disabled_base_properties(
    base: Any,
    derived: Any,
    base_id: str,
    derived_id: str,
    errors: list[str],
) -> None:
    base_flat = flatten_schema(base)
    derived_flat = flatten_schema(derived)
    base_props = base_flat.get("properties") if isinstance(base_flat, dict) else None
    derived_props = (
        derived_flat.get("properties") if isinstance(derived_flat, dict) else None
    )
    if not isinstance(derived_props, dict):
        return
    for name, derived_property in derived_props.items():
        if (
            derived_property is False
            and isinstance(base_props, dict)
            and name in base_props
        ):
            errors.append(
                f"property '{name}': derived schema '{derived_id}' disables property "
                f"defined in base '{base_id}'"
            )


# --- closed-descendant branch admission ------------------------------------
def _validate_closed_descendant_branches(
    ancestor_schema: Any,
    descendant_schema: Any,
    ancestor_label: str,
    descendant_label: str,
) -> list[str]:
    errors: list[str] = []
    _collect_closed_descendant_branch_errors(
        flatten_schema(ancestor_schema),
        descendant_schema,
        "",
        0,
        ancestor_label,
        descendant_label,
        errors,
    )
    return errors


def _collect_closed_descendant_branch_errors(
    ancestor: Any,
    descendant_schema: Any,
    path: str,
    depth: int,
    ancestor_label: str,
    descendant_label: str,
    errors: list[str],
) -> None:
    if depth >= MAX_RECURSION_DEPTH:
        errors.append(
            f"schema compatibility check exceeded maximum nesting depth at '{path}' "
            f"between ancestor '{ancestor_label}' and descendant '{descendant_label}'"
        )
        return
    if not isinstance(descendant_schema, dict):
        return

    ancestor_props = ancestor.get("properties") if isinstance(ancestor, dict) else None
    descendant_props = descendant_schema.get("properties")
    descendant_props = descendant_props if isinstance(descendant_props, dict) else None

    if boolean_schema_value(descendant_schema.get(_ADDITIONAL)) is False:
        orphaned = sorted(
            name
            for name in (ancestor_props or {})
            if not (descendant_props and name in descendant_props)
        )
        for name in orphaned:
            property_path = _join_path(path, name)
            errors.append(
                f"property '{property_path}': descendant schema '{descendant_label}' "
                "sets a closed additionalProperties constraint but does not restate "
                f"property defined in ancestor '{ancestor_label}', making it unusable "
                "under allOf composition"
            )

    if descendant_props:
        common = sorted(
            name
            for name in descendant_props
            if isinstance(ancestor_props, dict) and name in ancestor_props
        )
        for name in common:
            ancestor_prop = ancestor_props.get(name)  # type: ignore[union-attr]
            descendant_prop = descendant_props.get(name)
            _collect_closed_descendant_branch_errors(
                flatten_schema(ancestor_prop),
                descendant_prop,
                _join_path(path, name),
                depth + 1,
                ancestor_label,
                descendant_label,
                errors,
            )

    all_of = descendant_schema.get("allOf")
    if isinstance(all_of, list):
        for item in all_of:
            _collect_closed_descendant_branch_errors(
                ancestor,
                item,
                path,
                depth + 1,
                ancestor_label,
                descendant_label,
                errors,
            )


def _join_path(prefix: str, name: str) -> str:
    return name if not prefix else f"{prefix}.{name}"


# --- allOf flattening ------------------------------------------------------
def flatten_schema(schema: Any) -> Any:
    """Merge ``allOf`` into one effective object schema (recursive on props)."""
    if not isinstance(schema, dict):
        return schema
    result: dict[str, Any] = {}
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for branch in all_of:
            _merge_flat(result, flatten_schema(branch))
    for key, value in schema.items():
        if key == "allOf":
            continue
        _merge_flat(result, {key: value})
    return result


def _merge_flat(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if key == "properties" and isinstance(value, dict):
            props = target.setdefault("properties", {})
            for name, prop_schema in value.items():
                if (
                    name in props
                    and isinstance(props[name], dict)
                    and isinstance(prop_schema, dict)
                ):
                    props[name] = flatten_schema({"allOf": [props[name], prop_schema]})
                else:
                    props[name] = copy.deepcopy(prop_schema)
        elif key == "required" and isinstance(value, list):
            required = target.setdefault("required", [])
            for name in value:
                if name not in required:
                    required.append(name)
        elif key == _ADDITIONAL:
            current = target.get(_ADDITIONAL)
            if boolean_schema_value(current) is False:
                continue
            if boolean_schema_value(value) is True and current is not None:
                continue
            target[_ADDITIONAL] = copy.deepcopy(value)
        else:
            target[key] = copy.deepcopy(value)
