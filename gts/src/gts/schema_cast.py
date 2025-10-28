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
    from_id: str
    to_id: str
    direction: str
    added_properties: List[str]
    removed_properties: List[str]
    changed_properties: List[Dict[str, str]]
    fully_compatible: bool
    incompatibility_reasons: List[str]
    casted_instance: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from": self.from_id,
            "to": self.to_id,
            "direction": self.direction,
            "added_properties": self.added_properties,
            "removed_properties": self.removed_properties,
            "changed_properties": self.changed_properties,
            "fully_compatible": self.fully_compatible,
            "incompatibility_reasons": self.incompatibility_reasons,
            "result": self.casted_instance,
        }

    @classmethod
    def cast(
        cls,
        from_instance_id: str,
        to_schema_id: str,
        from_instance_content: dict,
        to_schema_content: dict,
    ) -> JsonEntityCastResult:
        # Normalize to effective object schema (target)
        target_schema = cls._effective_object_schema(to_schema_content)

        # Determine direction by IDs
        direction = cls._infer_direction(from_instance_id, to_schema_id)

        # Apply casting rules to the instance
        added: List[str] = []
        removed: List[str] = []
        reasons: List[str] = []

        try:
            casted, added, removed = cls._cast_instance_to_schema(
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
                fully_compatible=False,
                incompatibility_reasons=[str(e)],
                casted_instance=None,
            )

        # Validate the transformed instance against the FULL target schema
        try:
            js_validate(instance=casted, schema=to_schema_content)
            fully_compatible = True
        except js_exceptions.ValidationError as ve:
            reasons.append(ve.message)
            fully_compatible = False

        return cls(
            from_id=from_instance_id,
            to_id=to_schema_id,
            direction=direction,
            added_properties=sorted(list(dict.fromkeys(added))),
            removed_properties=sorted(list(dict.fromkeys(removed))),
            changed_properties=[],
            fully_compatible=fully_compatible,
            incompatibility_reasons=reasons,
            casted_instance=(casted if fully_compatible else None),
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
    ) -> Tuple[Dict[str, Any], List[str], List[str]]:
        """Transform instance to conform to schema.

        Rules:
        - Add defaults for missing required fields if provided; otherwise error
        - Remove fields not in target schema when additionalProperties is false
        - Validate constraints via a final jsonschema validation step
        - Recursively handle nested objects (and arrays of objects)
        """
        added: List[str] = []
        removed: List[str] = []

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
                    raise SchemaCastError(f"Missing required property '{path}' and no default is defined")

        # 2) For optional properties with defaults, set if missing (non-breaking)
        for prop, p_schema in target_props.items():
            if prop in required:
                continue
            if prop not in result and isinstance(p_schema, dict) and "default" in p_schema:
                result[prop] = copy.deepcopy(p_schema["default"])
                path = f"{base_path}.{prop}" if base_path else prop
                added.append(path)

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
                new_obj, add_sub, rem_sub = JsonEntityCastResult._cast_instance_to_schema(
                    val, nested_schema, base_path=(f"{base_path}.{prop}" if base_path else prop)
                )
                result[prop] = new_obj
                added.extend(add_sub)
                removed.extend(rem_sub)
            elif p_type == "array" and isinstance(val, list):
                items_schema = p_schema.get("items")
                if isinstance(items_schema, dict) and items_schema.get("type") == "object":
                    nested_schema = JsonEntityCastResult._effective_object_schema(items_schema)
                    new_list: List[Any] = []
                    for idx, item in enumerate(val):
                        if isinstance(item, dict):
                            new_item, add_sub, rem_sub = JsonEntityCastResult._cast_instance_to_schema(
                                item,
                                nested_schema,
                                base_path=(f"{base_path}.{prop}[{idx}]" if base_path else f"{prop}[{idx}]")
                            )
                            new_list.append(new_item)
                            added.extend(add_sub)
                            removed.extend(rem_sub)
                        else:
                            new_list.append(item)
                    result[prop] = new_list

        return result, added, removed

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
