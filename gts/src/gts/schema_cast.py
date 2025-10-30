from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import copy
from jsonschema import validate as js_validate
from jsonschema import exceptions as js_exceptions

from .gts import GtsID


class SchemaCastError(Exception):
    pass


@dataclass
class JsonEntityCastResult:
    from_id: str = ""
    to_id: str = ""
    direction: str = "unknown"
    added_properties: List[str] = None  # type: ignore
    removed_properties: List[str] = None  # type: ignore
    changed_properties: List[Dict[str, str]] = None  # type: ignore
    is_fully_compatible: bool = False
    is_backward_compatible: bool = False
    is_forward_compatible: bool = False
    incompatibility_reasons: List[str] = None  # type: ignore
    backward_errors: List[str] = None  # type: ignore
    forward_errors: List[str] = None  # type: ignore
    casted_entity: Optional[Dict[str, Any]] = None
    error: str = ""

    def __post_init__(self):
        # Initialize list fields if None
        if self.added_properties is None:
            self.added_properties = []
        if self.removed_properties is None:
            self.removed_properties = []
        if self.changed_properties is None:
            self.changed_properties = []
        if self.incompatibility_reasons is None:
            self.incompatibility_reasons = []
        if self.backward_errors is None:
            self.backward_errors = []
        if self.forward_errors is None:
            self.forward_errors = []

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "from": self.from_id,
            "to": self.to_id,
            "old": self.from_id,
            "new": self.to_id,
            "direction": self.direction,
            "added_properties": self.added_properties,
            "removed_properties": self.removed_properties,
            "changed_properties": self.changed_properties,
            "is_fully_compatible": self.is_fully_compatible,
            "is_backward_compatible": self.is_backward_compatible,
            "is_forward_compatible": self.is_forward_compatible,
            "incompatibility_reasons": self.incompatibility_reasons,
            "backward_errors": self.backward_errors,
            "forward_errors": self.forward_errors,
            "casted_entity": self.casted_entity,
        }
        if self.error:
            result["error"] = self.error
        return result

    @classmethod
    def cast(
        cls,
        from_instance_id: str,
        to_schema_id: str,
        from_instance_content: dict,
        from_schema_content: dict,
        to_schema_content: dict,
        resolver: Optional[Any] = None,
    ) -> JsonEntityCastResult:
        # Flatten target schema to merge allOf and get all properties including const values
        target_schema = cls._flatten_schema(to_schema_content)

        # Determine direction by IDs
        direction = cls._infer_direction(from_instance_id, to_schema_id)

        # Determine which is old/new based on direction
        if direction == "up":
            old_schema = from_schema_content
            new_schema = to_schema_content
        elif direction == "down":
            old_schema = to_schema_content
            new_schema = from_schema_content
        else:
            old_schema = from_schema_content
            new_schema = to_schema_content

        # Check compatibility
        is_backward, backward_errors = cls._check_backward_compatibility(old_schema, new_schema)
        is_forward, forward_errors = cls._check_forward_compatibility(old_schema, new_schema)

        # Apply casting rules to the instance
        added: List[str] = []
        removed: List[str] = []
        reasons: List[str] = []

        try:
            casted, added, removed, incompatibility_reasons = cls._cast_instance_to_schema(
                copy.deepcopy(from_instance_content) if isinstance(from_instance_content, dict) else {},
                target_schema,
                base_path="",
            )
        except SchemaCastError as e:
            return cls(
                from_id=from_instance_id,
                to_id=to_schema_id,
                direction=direction,
                added_properties=sorted(list(dict.fromkeys(added))),
                removed_properties=sorted(list(dict.fromkeys(removed))),
                changed_properties=[],
                is_fully_compatible=False,
                is_backward_compatible=is_backward,
                is_forward_compatible=is_forward,
                incompatibility_reasons=[str(e)],
                backward_errors=backward_errors,
                forward_errors=forward_errors,
                casted_entity=None,
            )

        # Validate the transformed instance against the FULL target schema
        # Allow GTS ID changes in const values
        try:
            if resolver is not None:
                cls._validate_with_gts_id_tolerance(casted, to_schema_content, resolver)
            else:
                cls._validate_with_gts_id_tolerance(casted, to_schema_content, None)
            is_fully_compatible = True
        except js_exceptions.ValidationError as ve:
            reasons.append(ve.message)
            is_fully_compatible = False

        return cls(
            from_id=from_instance_id,
            to_id=to_schema_id,
            direction=direction,
            added_properties=sorted(list(dict.fromkeys(added))),
            removed_properties=sorted(list(dict.fromkeys(removed))),
            changed_properties=[],
            is_fully_compatible=is_fully_compatible,
            is_backward_compatible=is_backward,
            is_forward_compatible=is_forward,
            incompatibility_reasons=reasons,
            backward_errors=backward_errors,
            forward_errors=forward_errors,
            casted_entity=casted,
        )

    @staticmethod
    def _infer_direction(from_id: str, to_id: str) -> str:
        try:
            gid_from = GtsID(from_id)
            gid_to = GtsID(to_id)
            from_minor = gid_from.gts_id_segments[-1].ver_minor
            to_minor = gid_to.gts_id_segments[-1].ver_minor
            if from_minor is not None and to_minor is not None:
                if to_minor > from_minor:
                    return "up"
                if to_minor < from_minor:
                    return "down"
                return "none"
        except Exception:
            pass
        return "unknown"

    @staticmethod
    def _effective_object_schema(s: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(s, dict):
            return {}
        if isinstance(s.get("properties"), dict) or isinstance(s.get("required"), list):
            return s
        if isinstance(s.get("allOf"), list):
            for part in s["allOf"]:
                if isinstance(part, dict) and (
                    isinstance(part.get("properties"), dict)
                    or isinstance(part.get("required"), list)
                ):
                    return part
        return s

    @staticmethod
    def _cast_instance_to_schema(
        instance: Dict[str, Any],
        schema: Dict[str, Any],
        base_path: str = "",
        incompatibility_reasons: List[str] = [],
    ) -> Tuple[Dict[str, Any], List[str], List[str], List[str]]:
        """Transform instance to conform to schema.

        Rules:
        - Add defaults for missing required fields if provided; otherwise error
        - Remove fields not in target schema when additionalProperties is false
        - Validate constraints via a final jsonschema validation step
        - Recursively handle nested objects (and arrays of objects)
        """
        added: List[str] = []
        removed: List[str] = []
        incompatibility_reasons: List[str] = []

        if not isinstance(instance, dict):
            raise SchemaCastError("Instance must be an object for casting")

        target_props = schema.get("properties", {}) if isinstance(schema.get("properties"), dict) else {}
        required = set(schema.get("required", [])) if isinstance(schema.get("required"), list) else set()
        additional = schema.get("additionalProperties", True)

        # Start from current values
        result: Dict[str, Any] = dict(instance)

        # 1) Ensure required properties exist (fill defaults if provided)
        for prop in required:
            if prop not in result:
                p_schema = target_props.get(prop, {})
                if isinstance(p_schema, dict) and "default" in p_schema:
                    result[prop] = copy.deepcopy(p_schema["default"])
                    path = f"{base_path}.{prop}" if base_path else prop
                    added.append(path)
                else:
                    path = f"{base_path}.{prop}" if base_path else prop
                    incompatibility_reasons.append(f"Missing required property '{path}' and no default is defined")
                    # raise SchemaCastError(f"Missing required property '{path}' and no default is defined")

        # 2) For optional properties with defaults, set if missing (non-breaking)
        for prop, p_schema in target_props.items():
            if prop in required:
                continue
            if prop not in result and isinstance(p_schema, dict) and "default" in p_schema:
                result[prop] = copy.deepcopy(p_schema["default"])
                path = f"{base_path}.{prop}" if base_path else prop
                added.append(path)

        # 2.5) Update const values to match target schema (for GTS ID fields like type and id)
        for prop, p_schema in target_props.items():
            if not isinstance(p_schema, dict):
                continue
            if "const" in p_schema:
                const_value = p_schema["const"]
                # Update the value if it's a GTS ID or if the property exists
                if prop in result:
                    old_value = result[prop]
                    # Only update if the const value is different and both are GTS IDs
                    if isinstance(const_value, str) and isinstance(old_value, str):
                        if GtsID.is_valid(const_value) and GtsID.is_valid(old_value):
                            if old_value != const_value:
                                result[prop] = const_value
                                path = f"{base_path}.{prop}" if base_path else prop
                                # Don't add to changed list, this is expected for version casting

        # 3) Remove properties not present in target schema when additionalProperties is false
        if additional is False:
            for prop in list(result.keys()):
                if prop not in target_props:
                    del result[prop]
                    path = f"{base_path}.{prop}" if base_path else prop
                    removed.append(path)

        # 4) Recurse into nested object properties
        for prop, p_schema in target_props.items():
            if prop not in result:
                continue
            val = result[prop]
            if not isinstance(p_schema, dict):
                continue
            p_type = p_schema.get("type")
            if p_type == "object" and isinstance(val, dict):
                nested_schema = JsonEntityCastResult._effective_object_schema(p_schema)
                new_obj, add_sub, rem_sub, new_incompatibility_reasons = JsonEntityCastResult._cast_instance_to_schema(
                    val, nested_schema, base_path=(f"{base_path}.{prop}" if base_path else prop), incompatibility_reasons=incompatibility_reasons
                )
                result[prop] = new_obj
                added.extend(add_sub)
                removed.extend(rem_sub)
                incompatibility_reasons.extend(new_incompatibility_reasons)
            elif p_type == "array" and isinstance(val, list):
                items_schema = p_schema.get("items")
                if isinstance(items_schema, dict) and items_schema.get("type") == "object":
                    nested_schema = JsonEntityCastResult._effective_object_schema(items_schema)
                    new_list: List[Any] = []
                    for idx, item in enumerate(val):
                        if isinstance(item, dict):
                            new_item, add_sub, rem_sub, new_incompatibility_reasons = JsonEntityCastResult._cast_instance_to_schema(
                                item,
                                nested_schema,
                                base_path=(f"{base_path}.{prop}[{idx}]" if base_path else f"{prop}[{idx}]"),
                                incompatibility_reasons=incompatibility_reasons,
                            )
                            new_list.append(new_item)
                            added.extend(add_sub)
                            removed.extend(rem_sub)
                            incompatibility_reasons.extend(new_incompatibility_reasons)
                        else:
                            new_list.append(item)
                    result[prop] = new_list

        return result, added, removed, incompatibility_reasons

    @staticmethod
    def _validate_with_gts_id_tolerance(
        instance: Dict[str, Any],
        schema: Dict[str, Any],
        resolver: Optional[Any] = None,
    ) -> None:
        """Validate instance against schema, but allow const values to differ if both are GTS IDs."""
        # Create a modified schema that removes const constraints for GTS IDs
        modified_schema = JsonEntityCastResult._remove_gts_const_constraints(schema)

        if resolver is not None:
            js_validate(instance=instance, schema=modified_schema, resolver=resolver)
        else:
            js_validate(instance=instance, schema=modified_schema)

    @staticmethod
    def _remove_gts_const_constraints(schema: Any) -> Any:
        """Recursively remove const constraints where the value is a GTS ID. The reason is that
        we want to allow const values to differ in minor version casting if both are GTS IDs.
        """
        if not isinstance(schema, dict):
            return schema

        result = {}
        for key, value in schema.items():
            if key == "const" and isinstance(value, str) and GtsID.is_valid(value):
                # Replace const with a type constraint instead
                result["type"] = "string"
                continue
            elif isinstance(value, dict):
                result[key] = JsonEntityCastResult._remove_gts_const_constraints(value)
            elif isinstance(value, list):
                result[key] = [
                    JsonEntityCastResult._remove_gts_const_constraints(item)
                    if isinstance(item, dict)
                    else item
                    for item in value
                ]
            else:
                result[key] = value

        return result

    @staticmethod
    def _flatten_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
        """Flatten a schema by merging allOf schemas."""
        result = {"properties": {}, "required": []}

        # Merge allOf schemas
        if "allOf" in schema:
            for sub_schema in schema["allOf"]:
                flattened = JsonEntityCastResult._flatten_schema(sub_schema)
                result["properties"].update(flattened.get("properties", {}))
                result["required"].extend(flattened.get("required", []))
                # Preserve additionalProperties from sub-schemas (last one wins)
                if "additionalProperties" in flattened:
                    result["additionalProperties"] = flattened["additionalProperties"]

        # Add direct properties and required
        if "properties" in schema:
            result["properties"].update(schema["properties"])
        if "required" in schema:
            result["required"].extend(schema["required"])
        # Preserve additionalProperties from top level (overrides sub-schemas)
        if "additionalProperties" in schema:
            result["additionalProperties"] = schema["additionalProperties"]

        return result

    @staticmethod
    def _check_min_max_constraint(
        prop: str,
        old_schema: Dict[str, Any],
        new_schema: Dict[str, Any],
        min_key: str,
        max_key: str,
        check_tightening: bool,
    ) -> List[str]:
        """Check min/max constraint compatibility between schemas.

        Args:
            prop: Property name for error messages
            old_schema: Old property schema
            new_schema: New property schema
            min_key: Key for minimum constraint (e.g., 'minimum', 'minLength', 'minItems')
            max_key: Key for maximum constraint (e.g., 'maximum', 'maxLength', 'maxItems')
            check_tightening: If True, check for constraint tightening (backward compat).
                            If False, check for constraint relaxation (forward compat).

        Returns:
            List of error messages
        """
        errors: List[str] = []

        # Check minimum constraint
        old_min = old_schema.get(min_key)
        new_min = new_schema.get(min_key)
        if old_min is not None and new_min is not None:
            if check_tightening and new_min > old_min:
                errors.append(f"Property '{prop}' {min_key} increased from {old_min} to {new_min}")
            elif not check_tightening and new_min < old_min:
                errors.append(f"Property '{prop}' {min_key} decreased from {old_min} to {new_min}")
        elif check_tightening and old_min is None and new_min is not None:
            errors.append(f"Property '{prop}' added {min_key} constraint: {new_min}")
        elif not check_tightening and old_min is not None and new_min is None:
            errors.append(f"Property '{prop}' removed {min_key} constraint")

        # Check maximum constraint
        old_max = old_schema.get(max_key)
        new_max = new_schema.get(max_key)
        if old_max is not None and new_max is not None:
            if check_tightening and new_max < old_max:
                errors.append(f"Property '{prop}' {max_key} decreased from {old_max} to {new_max}")
            elif not check_tightening and new_max > old_max:
                errors.append(f"Property '{prop}' {max_key} increased from {old_max} to {new_max}")
        elif check_tightening and old_max is None and new_max is not None:
            errors.append(f"Property '{prop}' added {max_key} constraint: {new_max}")
        elif not check_tightening and old_max is not None and new_max is None:
            errors.append(f"Property '{prop}' removed {max_key} constraint")

        return errors

    @staticmethod
    def _check_constraint_compatibility(
        prop: str,
        old_prop_schema: Dict[str, Any],
        new_prop_schema: Dict[str, Any],
        check_tightening: bool = True,
    ) -> List[str]:
        """Check if constraints are compatible between old and new property schemas.

        Args:
            prop: Property name for error messages
            old_prop_schema: Old property schema
            new_prop_schema: New property schema
            check_tightening: If True, check for constraint tightening (backward compat).
                            If False, check for constraint relaxation (forward compat).

        Returns:
            List of error messages
        """
        errors: List[str] = []
        prop_type = old_prop_schema.get("type")

        # Numeric constraints (for number/integer types)
        if prop_type in ("number", "integer"):
            errors.extend(
                JsonEntityCastResult._check_min_max_constraint(
                    prop, old_prop_schema, new_prop_schema, "minimum", "maximum", check_tightening
                )
            )

        # String constraints
        if prop_type == "string":
            errors.extend(
                JsonEntityCastResult._check_min_max_constraint(
                    prop, old_prop_schema, new_prop_schema, "minLength", "maxLength", check_tightening
                )
            )

        # Array constraints
        if prop_type == "array":
            errors.extend(
                JsonEntityCastResult._check_min_max_constraint(
                    prop, old_prop_schema, new_prop_schema, "minItems", "maxItems", check_tightening
                )
            )

        return errors

    @staticmethod
    def _check_schema_compatibility(
        old_schema: Dict[str, Any],
        new_schema: Dict[str, Any],
        check_backward: bool,
    ) -> tuple[bool, List[str]]:
        """Unified compatibility checker for backward and forward compatibility.

        Args:
            old_schema: Old schema version
            new_schema: New schema version
            check_backward: If True, check backward compatibility (new consumers read old data).
                          If False, check forward compatibility (old consumers read new data).

        Returns:
            Tuple of (is_compatible, list_of_errors)
        """
        errors: List[str] = []

        # Flatten schemas to handle allOf
        old_flat = JsonEntityCastResult._flatten_schema(old_schema)
        new_flat = JsonEntityCastResult._flatten_schema(new_schema)

        old_props = old_flat.get("properties", {})
        new_props = new_flat.get("properties", {})
        old_required = set(old_flat.get("required", []))
        new_required = set(new_flat.get("required", []))

        # Check required properties changes
        if check_backward:
            # Backward: cannot add required properties
            newly_required = new_required - old_required
            if newly_required:
                errors.append(f"Added required properties: {', '.join(newly_required)}")
        else:
            # Forward: cannot remove required properties
            removed_required = old_required - new_required
            if removed_required:
                errors.append(f"Removed required properties: {', '.join(removed_required)}")

        # Check properties that exist in both schemas
        common_props = set(old_props.keys()) & set(new_props.keys())
        for prop in common_props:
            old_prop_schema = old_props[prop]
            new_prop_schema = new_props[prop]

            # Check if type changed
            old_type = old_prop_schema.get("type")
            new_type = new_prop_schema.get("type")
            if old_type and new_type and old_type != new_type:
                errors.append(f"Property '{prop}' type changed from {old_type} to {new_type}")

            # Check enum constraints
            old_enum = old_prop_schema.get("enum")
            new_enum = new_prop_schema.get("enum")
            if old_enum and new_enum:
                old_enum_set = set(old_enum)
                new_enum_set = set(new_enum)
                if check_backward:
                    # Backward: cannot add enum values
                    added_enum_values = new_enum_set - old_enum_set
                    if added_enum_values:
                        errors.append(f"Property '{prop}' added enum values: {added_enum_values}")
                else:
                    # Forward: cannot remove enum values
                    removed_enum_values = old_enum_set - new_enum_set
                    if removed_enum_values:
                        errors.append(f"Property '{prop}' removed enum values: {removed_enum_values}")

            # Check constraint compatibility
            constraint_errors = JsonEntityCastResult._check_constraint_compatibility(
                prop, old_prop_schema, new_prop_schema, check_tightening=check_backward
            )
            errors.extend(constraint_errors)

            # Recursively check nested object properties
            if old_type == "object" and new_type == "object":
                nested_compat, nested_errors = JsonEntityCastResult._check_schema_compatibility(
                    old_prop_schema, new_prop_schema, check_backward
                )
                if not nested_compat:
                    for err in nested_errors:
                        errors.append(f"Property '{prop}': {err}")

        return len(errors) == 0, errors

    @staticmethod
    def _check_backward_compatibility(
        old_schema: Dict[str, Any],
        new_schema: Dict[str, Any],
    ) -> tuple[bool, List[str]]:
        """Check if new schema is backward compatible with old schema.

        Backward compatibility: new consumers can read old data.
        Rules:
        - Can add optional properties
        - Can remove required properties
        - Can remove enum values
        - Can relax constraints (increase max, decrease min, etc.)
        - Cannot add required properties
        - Cannot change property types
        - Cannot add enum values
        - Cannot tighten constraints (decrease max, increase min, etc.)
        """
        return JsonEntityCastResult._check_schema_compatibility(old_schema, new_schema, check_backward=True)

    @staticmethod
    def _check_forward_compatibility(
        old_schema: Dict[str, Any],
        new_schema: Dict[str, Any],
    ) -> tuple[bool, List[str]]:
        """Check if new schema is forward compatible with old schema.

        Forward compatibility: old consumers can read new data.
        Rules:
        - Can add required properties
        - Can add enum values
        - Can tighten constraints (decrease max, increase min, etc.)
        - Cannot remove required properties
        - Cannot change property types
        - Cannot remove enum values
        - Cannot relax constraints (increase max, decrease min, etc.)
        """
        return JsonEntityCastResult._check_schema_compatibility(old_schema, new_schema, check_backward=False)

    @staticmethod
    def _diff_objects(
        obj_a: Dict[str, Any],
        obj_b: Dict[str, Any],
        base: str,
        added: List[str],
        removed: List[str],
        changed: List[Dict[str, str]],
    ) -> None:
        a_props = obj_a.get("properties", {}) if isinstance(obj_a, dict) else {}
        b_props = obj_b.get("properties", {}) if isinstance(obj_b, dict) else {}
        a_keys = set(a_props.keys())
        b_keys = set(b_props.keys())
        for k in sorted(a_keys - b_keys):
            p = f"{base}.{k}" if base else k
            removed.append(p)
        for k in sorted(b_keys - a_keys):
            p = f"{base}.{k}" if base else k
            added.append(p)
        for k in sorted(a_keys & b_keys):
            p = f"{base}.{k}" if base else k
            av = a_props.get(k, {})
            bv = b_props.get(k, {})
            if isinstance(av, dict) and isinstance(bv, dict):
                at = av.get("type")
                bt = bv.get("type")
                if at != bt:
                    changed.append({"path": p, "change": f"type: {at} -> {bt}"})
                af = av.get("format")
                bf = bv.get("format")
                if af != bf:
                    changed.append({"path": p, "change": f"format: {af} -> {bf}"})
                JsonEntityCastResult._diff_objects(av, bv, p, added, removed, changed)

        a_req = set(obj_a.get("required", [])) if isinstance(obj_a, dict) else set()
        b_req = set(obj_b.get("required", [])) if isinstance(obj_b, dict) else set()
        for k in sorted(b_req - a_req):
            rp = f"{base}.{k}" if base else k
            changed.append({"path": rp, "change": "required: added"})
        for k in sorted(a_req - b_req):
            rp = f"{base}.{k}" if base else k
            changed.append({"path": rp, "change": "required: removed"})

    @staticmethod
    def _path_label(path: str) -> str:
        return path if path else "root"

    @staticmethod
    def _filtered(d: Dict[str, Any]) -> Dict[str, Any]:
        exclude = ("properties", "required")
        return {k: v for k, v in d.items() if k not in exclude}

    @staticmethod
    def _only_optional_add_remove(
        a: Dict[str, Any],
        b: Dict[str, Any],
        path: str,
        reasons: List[str],
    ) -> bool:
        if not isinstance(a, dict) or not isinstance(b, dict):
            if a != b:
                reasons.append(f"{JsonEntityCastResult._path_label(path)}: value changed")
                return False
            return True

        fa = JsonEntityCastResult._filtered(a)
        fb = JsonEntityCastResult._filtered(b)
        if fa != fb:
            keys = set(fa.keys()) | set(fb.keys())
            for k in sorted(keys):
                va = fa.get(k, "<missing>")
                vb = fb.get(k, "<missing>")
                if va != vb:
                    reasons.append(
                        f"{JsonEntityCastResult._path_label(path)}: keyword '{k}' changed"
                    )
            return False

        a_req = set(a.get("required", [])) if isinstance(a.get("required"), list) else set()
        b_req = set(b.get("required", [])) if isinstance(b.get("required"), list) else set()
        if a_req != b_req:
            added_req = sorted(list(b_req - a_req))
            removed_req = sorted(list(a_req - b_req))
            if added_req:
                reasons.append(
                    f"{JsonEntityCastResult._path_label(path)}: required added -> "
                    f"{', '.join(added_req)}"
                )
            if removed_req:
                reasons.append(
                    f"{JsonEntityCastResult._path_label(path)}: required removed -> "
                    f"{', '.join(removed_req)}"
                )
            return False

        a_props = a.get("properties", {}) if isinstance(a.get("properties"), dict) else {}
        b_props = b.get("properties", {}) if isinstance(b.get("properties"), dict) else {}
        common = set(a_props.keys()) & set(b_props.keys())
        for k in common:
            next_path = f"{path}.properties.{k}" if path else f"properties.{k}"
            if not JsonEntityCastResult._only_optional_add_remove(
                a_props[k], b_props[k], next_path, reasons
            ):
                return False
        return True
