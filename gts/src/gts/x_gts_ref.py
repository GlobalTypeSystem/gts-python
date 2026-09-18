"""
x-gts-ref validation support for GTS schemas (optimized version).

This module implements validation for the x-gts-ref extension as specified
in the GTS specification section 9.5.

Key optimizations:
1. Use jsonpointer library for JSON Pointer resolution
2. Consolidate duplicate validation logic
3. Simplify recursive traversal with a generic walker
"""

from __future__ import annotations

from typing import Any

from jsonschema.validators import validator_for

from ._json_pointer import MISSING
from ._json_pointer import resolve as resolve_json_pointer
from ._naming import GTS_PREFIX, strip_scheme
from .gts import GtsID
from .schema_validation import iter_schema_nodes, map_schema_nodes


def _without_x_gts_ref(schema: Any) -> Any:
    def strip(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        stripped = {key: value for key, value in node.items() if key != "x-gts-ref"}
        for keyword in ("oneOf", "anyOf", "allOf"):
            branches = stripped.get(keyword)
            if (
                isinstance(branches, list)
                and branches
                and all(isinstance(branch, dict) and not branch for branch in branches)
            ):
                stripped.pop(keyword, None)
        return stripped

    return map_schema_nodes(schema, strip)


def _is_x_gts_ref_only_combinator(branches: list[Any]) -> bool:
    if not branches:
        return False
    for branch in branches:
        stripped = _without_x_gts_ref(branch)
        if not isinstance(stripped, dict) or stripped:
            return False
    return True


def _is_structurally_valid(instance: Any, schema: Any) -> bool:
    try:
        validator = validator_for(schema)(_without_x_gts_ref(schema))
        return validator.is_valid(instance)
    except Exception:  # noqa: BLE001 - treat any validation error as "not valid"
        return False


class XGtsRefValidationError(Exception):
    """Exception raised when x-gts-ref validation fails."""

    def __init__(self, field_path: str, value: Any, ref_pattern: str, reason: str):
        super().__init__(
            f"x-gts-ref validation failed for field '{field_path}': {reason}"
        )
        self.field_path = field_path
        self.value = value
        self.ref_pattern = ref_pattern
        self.reason = reason


class XGtsRefValidator:
    """Validator for x-gts-ref constraints in GTS schemas."""

    def __init__(self, store: Any | None = None, enforce_existence: bool = True):
        """
        Initialize validator.

        Args:
            store: Optional GtsStore for resolving entity references.
            enforce_existence: When True (default) and a ``store`` is provided,
                an x-gts-ref value must resolve to a registered entity or
                validation fails. Set to False to validate only that the value
                is a well-formed GTS id matching the constraint pattern, without
                requiring the referenced entity to exist in the registry.
        """
        self.store = store
        self.enforce_existence = enforce_existence
        self.referenced_ids: set[str] = set()

    def validate_instance(
        self, instance: dict[str, Any], schema: dict[str, Any], instance_path: str = ""
    ) -> list[XGtsRefValidationError]:
        """
        Validate an instance against x-gts-ref constraints in schema.

        Args:
            instance: The data instance to validate
            schema: The JSON schema with x-gts-ref extensions
            instance_path: Current path in instance (for error reporting)

        Returns:
            List of validation errors (empty if valid)
        """
        errors: list[XGtsRefValidationError] = []

        def resolve_local_ref(ref: str) -> Any | None:
            if ref != "#" and not ref.startswith("#/"):
                return None
            return resolve_json_pointer(schema, ref, default=None)

        def visit_instance(inst, sch, path, errs, refs=None):
            """Visit instance nodes and validate x-gts-ref constraints."""
            if not isinstance(sch, dict):
                return

            refs = refs or set()
            ref = sch.get("$ref")
            if isinstance(ref, str) and ref not in refs:
                target = resolve_local_ref(ref)
                if target is not None:
                    visit_instance(inst, target, path, errs, refs | {ref})

            if "x-gts-ref" in sch and isinstance(inst, str):
                error = self._validate_ref_value(inst, sch["x-gts-ref"], path, schema)
                if error:
                    errs.append(error)

            one_of = sch.get("oneOf")
            if isinstance(one_of, list):
                if _is_x_gts_ref_only_combinator(one_of):
                    branch_errors = [
                        _validate_branch(inst, branch, path) for branch in one_of
                    ]
                    matching = sum(not branch for branch in branch_errors)
                    if matching == 0:
                        errs.append(
                            XGtsRefValidationError(
                                path, inst, "", "oneOf: no branch matched"
                            )
                        )
                    elif matching > 1:
                        errs.append(
                            XGtsRefValidationError(
                                path,
                                inst,
                                "",
                                f"oneOf: {matching} branches matched, expected exactly 1",
                            )
                        )
                else:
                    matching_branches = [
                        branch
                        for branch in one_of
                        if _is_structurally_valid(inst, branch)
                    ]
                    if len(matching_branches) == 1:
                        errs.extend(_validate_branch(inst, matching_branches[0], path))

            any_of = sch.get("anyOf")
            if isinstance(any_of, list):
                if _is_x_gts_ref_only_combinator(any_of):
                    branch_errors = [
                        _validate_branch(inst, branch, path) for branch in any_of
                    ]
                    if not any(not branch for branch in branch_errors):
                        errs.append(
                            XGtsRefValidationError(
                                path, inst, "", "anyOf: no branch matched"
                            )
                        )
                else:
                    matching_branches = [
                        branch
                        for branch in any_of
                        if _is_structurally_valid(inst, branch)
                    ]
                    branch_errors = [
                        _validate_branch(inst, branch, path)
                        for branch in matching_branches
                    ]
                    if matching_branches and not any(
                        not branch for branch in branch_errors
                    ):
                        errs.append(
                            XGtsRefValidationError(
                                path, inst, "", "anyOf: no branch matched"
                            )
                        )

            all_of = sch.get("allOf")
            if isinstance(all_of, list):
                for branch in all_of:
                    if _is_structurally_valid(inst, branch):
                        errs.extend(_validate_branch(inst, branch, path))

            properties = sch.get("properties")
            if isinstance(properties, dict) and isinstance(inst, dict):
                for prop_name, prop_schema in properties.items():
                    if prop_name in inst:
                        prop_path = f"{path}.{prop_name}" if path else prop_name
                        visit_instance(inst[prop_name], prop_schema, prop_path, errs)

            if isinstance(inst, list):
                prefix_items = sch.get("prefixItems")
                items = sch.get("items")
                if isinstance(prefix_items, list):
                    for idx, item_schema in enumerate(prefix_items[: len(inst)]):
                        item_path = f"{path}[{idx}]"
                        visit_instance(inst[idx], item_schema, item_path, errs)
                    if isinstance(items, dict):
                        for idx in range(len(prefix_items), len(inst)):
                            item_path = f"{path}[{idx}]"
                            visit_instance(inst[idx], items, item_path, errs)
                elif isinstance(items, list):
                    for idx, item_schema in enumerate(items[: len(inst)]):
                        item_path = f"{path}[{idx}]"
                        visit_instance(inst[idx], item_schema, item_path, errs)
                    additional_items = sch.get("additionalItems")
                    if isinstance(additional_items, dict):
                        for idx in range(len(items), len(inst)):
                            item_path = f"{path}[{idx}]"
                            visit_instance(inst[idx], additional_items, item_path, errs)
                elif isinstance(items, dict):
                    for idx, item in enumerate(inst):
                        item_path = f"{path}[{idx}]"
                        visit_instance(item, items, item_path, errs)

        def _validate_branch(inst, branch, path):
            branch_errors: list[XGtsRefValidationError] = []
            visit_instance(inst, branch, path, branch_errors)
            return branch_errors

        visit_instance(instance, schema, instance_path, errors)
        return errors

    def validate_schema(
        self,
        schema: dict[str, Any],
        schema_path: str = "",
        root_schema: dict[str, Any] | None = None,
        resolve_relative: bool = True,
    ) -> list[XGtsRefValidationError]:
        """
        Validate x-gts-ref fields in a schema definition.

        Args:
            schema: The JSON schema to validate
            schema_path: Current path in schema (for error reporting)
            root_schema: The root schema (for resolving relative refs)
            resolve_relative: Whether relative refs must resolve during this check

        Returns:
            List of validation errors (empty if valid)
        """
        if root_schema is None:
            root_schema = schema

        errors = []
        for subschema, path in iter_schema_nodes(schema, schema_path):
            if "x-gts-ref" not in subschema:
                continue
            ref_value = subschema["x-gts-ref"]
            if (
                not resolve_relative
                and isinstance(ref_value, str)
                and ref_value.startswith("/")
            ):
                continue
            ref_path = f"{path}/x-gts-ref" if path else "x-gts-ref"
            error = self._validate_ref_pattern(ref_value, ref_path, root_schema)
            if error:
                errors.append(error)
        return errors

    def validate_schema_ref_existence(
        self,
        schema: Any,
        schema_path: str = "",
        root_schema: dict[str, Any] | None = None,
    ) -> list[XGtsRefValidationError]:
        if self.store is None or not self.enforce_existence:
            return []

        if root_schema is None:
            root_schema = schema
        store = self.store
        errors: list[XGtsRefValidationError] = []
        for subschema, path in iter_schema_nodes(schema, schema_path):
            ref_pattern = subschema.get("x-gts-ref")
            resolved_pattern = self.resolve_ref_pattern(ref_pattern, root_schema)
            if (
                not isinstance(resolved_pattern, str)
                or not resolved_pattern.startswith(GTS_PREFIX)
                or "*" in resolved_pattern
                or store.get(resolved_pattern) is not None
            ):
                continue
            ref_path = f"{path}/x-gts-ref" if path else "x-gts-ref"
            errors.append(
                XGtsRefValidationError(
                    ref_path,
                    ref_pattern,
                    resolved_pattern,
                    f"x-gts-ref constraint type '{resolved_pattern}' is not registered",
                )
            )
        return errors

    def resolve_ref_pattern(
        self, ref_pattern: Any, root_schema: dict[str, Any]
    ) -> str | None:
        if not isinstance(ref_pattern, str):
            return None
        if ref_pattern.startswith("/"):
            return self._resolve_pointer(root_schema, ref_pattern)
        return strip_scheme(ref_pattern)

    def resolve_schema_ref_patterns(
        self, schema: Any, root_schema: dict[str, Any]
    ) -> Any:
        def resolve(node: Any) -> Any:
            if not isinstance(node, dict):
                return node
            ref_pattern = node.get("x-gts-ref")
            if not isinstance(ref_pattern, str) or not ref_pattern.startswith("/"):
                return node
            resolved_pattern = self.resolve_ref_pattern(ref_pattern, root_schema)
            if resolved_pattern is not None:
                node["x-gts-ref"] = resolved_pattern
            return node

        return map_schema_nodes(schema, resolve)

    def _validate_ref_value(
        self, value: str, ref_pattern: str, field_path: str, schema: dict[str, Any]
    ) -> XGtsRefValidationError | None:
        """
        Validate an instance value against its x-gts-ref constraint.

        Args:
            value: The field value to validate
            ref_pattern: The x-gts-ref pattern
            field_path: Path to the field (for error reporting)
            schema: The complete schema (for resolving relative refs)

        Returns:
            XGtsRefValidationError if validation fails, None otherwise
        """
        if not isinstance(value, str):
            return XGtsRefValidationError(
                field_path,
                value,
                ref_pattern,
                f"Value must be a string, got {type(value).__name__}",
            )

        # Resolve pattern if it's a relative reference
        if ref_pattern.startswith("/"):
            resolved_pattern = self.resolve_ref_pattern(ref_pattern, schema)
            if resolved_pattern is None:
                return XGtsRefValidationError(
                    field_path,
                    value,
                    ref_pattern,
                    f"Cannot resolve reference path '{ref_pattern}'",
                )
            if not isinstance(resolved_pattern, str) or not resolved_pattern.startswith(
                "gts."
            ):
                return XGtsRefValidationError(
                    field_path,
                    value,
                    ref_pattern,
                    f"Resolved reference '{ref_pattern}' -> '{resolved_pattern}' is not a GTS pattern",
                )
            ref_pattern = resolved_pattern

        # Validate against GTS pattern
        return self._validate_gts_pattern(value, ref_pattern, field_path)

    def _validate_ref_pattern(
        self, ref_pattern: str, field_path: str, root_schema: dict[str, Any]
    ) -> XGtsRefValidationError | None:
        """
        Validate an x-gts-ref pattern in a schema definition.

        Args:
            ref_pattern: The x-gts-ref value
            field_path: Path to the field (for error reporting)
            root_schema: The root schema (for resolving relative refs)

        Returns:
            XGtsRefValidationError if validation fails, None otherwise
        """
        if not isinstance(ref_pattern, str):
            return XGtsRefValidationError(
                field_path,
                ref_pattern,
                "",
                f"x-gts-ref value must be a string, got {type(ref_pattern).__name__}",
            )

        # Case 1: Absolute GTS pattern
        if ref_pattern.startswith(GTS_PREFIX):
            return self._validate_gts_id_or_pattern(ref_pattern, field_path)

        # Case 2: Relative reference
        if ref_pattern.startswith("/"):
            resolved = self._resolve_pointer(root_schema, ref_pattern)
            if resolved is None:
                return XGtsRefValidationError(
                    field_path,
                    ref_pattern,
                    ref_pattern,
                    f"Cannot resolve reference path '{ref_pattern}'",
                )
            if not isinstance(resolved, str) or not GtsID.is_valid(resolved):
                return XGtsRefValidationError(
                    field_path,
                    ref_pattern,
                    ref_pattern,
                    f"Resolved reference '{ref_pattern}' -> '{resolved}' is not a valid GTS identifier",
                )
            return None

        return XGtsRefValidationError(
            field_path,
            ref_pattern,
            ref_pattern,
            f"Invalid x-gts-ref value: '{ref_pattern}' must start with 'gts.' or '/'",
        )

    def _validate_gts_id_or_pattern(
        self, pattern: str, field_path: str
    ) -> XGtsRefValidationError | None:
        """Validate a GTS ID or pattern in schema definition."""
        if pattern == "gts.*":
            return None  # Valid wildcard

        if "*" in pattern:
            # Wildcard pattern - validate prefix
            prefix = pattern.rstrip("*")
            if not prefix.startswith(GTS_PREFIX):
                return XGtsRefValidationError(
                    field_path,
                    pattern,
                    pattern,
                    f"Invalid GTS wildcard pattern: {pattern}",
                )
            return None

        # Specific GTS ID
        if not GtsID.is_valid(pattern):
            return XGtsRefValidationError(
                field_path, pattern, pattern, f"Invalid GTS identifier: {pattern}"
            )
        return None

    def _validate_gts_pattern(
        self, value: str, pattern: str, field_path: str
    ) -> XGtsRefValidationError | None:
        """
        Validate value matches a GTS pattern.

        Args:
            value: The value to validate
            pattern: GTS pattern (e.g., "gts.*", "gts.x.core.modules.*")
            field_path: Path to field (for error reporting)

        Returns:
            Error if validation fails, None otherwise
        """
        # Validate it's a valid GTS ID
        if not GtsID.is_valid(value):
            return XGtsRefValidationError(
                field_path,
                value,
                pattern,
                f"Value '{value}' is not a valid GTS identifier",
            )

        # Check pattern match
        if pattern == "gts.*":
            pass  # Any valid GTS ID matches
        elif pattern.endswith("*"):
            prefix = pattern[:-1]
            if not value.startswith(prefix):
                return XGtsRefValidationError(
                    field_path,
                    value,
                    pattern,
                    f"Value '{value}' does not match pattern '{pattern}'",
                )
        elif not value.startswith(pattern):
            return XGtsRefValidationError(
                field_path,
                value,
                pattern,
                f"Value '{value}' does not match pattern '{pattern}'",
            )

        # Referenced value must resolve to a registered entity when a store is
        # available and existence enforcement is enabled. Existence is enforced
        # uniformly for all constraint forms (including the bare "gts.*"
        # wildcard).
        if self.store and self.enforce_existence:
            entity = self.store.get(value)
            if not entity:
                return XGtsRefValidationError(
                    field_path,
                    value,
                    pattern,
                    f"Referenced entity '{value}' not found in registry",
                )
            self.referenced_ids.add(value)

        return None

    def _resolve_pointer(self, schema: dict[str, Any], pointer: str) -> str | None:
        """
        Resolve a JSON Pointer in the schema to a GTS identifier.

        Args:
            schema: The schema to search
            pointer: JSON Pointer (e.g., "/$id", "/properties/type")

        Returns:
            The resolved GTS identifier (bare form) or None if not found.
        """
        current = resolve_json_pointer(schema, pointer, default=MISSING)
        if current is MISSING or current is None:
            return None

        # If current is a string, return it (normalized to the bare form).
        if isinstance(current, str):
            return strip_scheme(current)

        # If current is a dict with x-gts-ref, resolve it
        if isinstance(current, dict) and "x-gts-ref" in current:
            ref_value = current["x-gts-ref"]
            if isinstance(ref_value, str):
                if ref_value.startswith("/"):
                    return self._resolve_pointer(schema, ref_value)
                return strip_scheme(ref_value)

        return None
