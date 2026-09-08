"""Tests for gts.derivation (OP#12 schema-vs-schema derivation admission)."""

from gts.derivation import (
    flatten_schema,
    validate_derivation,
    validate_derivation_compatibility,
    validate_closed_descendant_branches,
)


class TestFlattenSchema:
    def test_non_dict_returned_as_is(self):
        assert flatten_schema("not-a-dict") == "not-a-dict"
        assert flatten_schema(True) is True

    def test_merges_allof_properties(self):
        schema = {
            "allOf": [
                {"properties": {"a": {"type": "string"}}, "required": ["a"]},
                {"properties": {"b": {"type": "integer"}}, "required": ["b"]},
            ]
        }
        flat = flatten_schema(schema)
        assert set(flat["properties"].keys()) == {"a", "b"}
        assert set(flat["required"]) == {"a", "b"}

    def test_merges_same_property_via_nested_allof(self):
        schema = {
            "allOf": [
                {"properties": {"a": {"type": "string", "minLength": 1}}},
                {"properties": {"a": {"maxLength": 10}}},
            ]
        }
        flat = flatten_schema(schema)
        assert flat["properties"]["a"]["minLength"] == 1
        assert flat["properties"]["a"]["maxLength"] == 10

    def test_additional_properties_false_sticky(self):
        schema = {
            "allOf": [
                {"additionalProperties": False},
                {"additionalProperties": True},
            ]
        }
        flat = flatten_schema(schema)
        assert flat["additionalProperties"] is False

    def test_additional_properties_true_does_not_override_existing(self):
        schema = {"allOf": [{"additionalProperties": {"type": "string"}}]}
        flat = flatten_schema(schema)
        # top-level has no additionalProperties key, so allOf value applies
        assert flat["additionalProperties"] == {"type": "string"}

    def test_scalar_keys_overwritten(self):
        schema = {"allOf": [{"title": "a"}], "title": "b"}
        flat = flatten_schema(schema)
        assert flat["title"] == "b"


class TestValidateDerivation:
    def test_compatible_extension_no_errors(self):
        base = {"type": "object", "properties": {"a": {"type": "string"}}}
        derived = {
            "allOf": [base],
            "type": "object",
            "properties": {"b": {"type": "integer"}},
        }
        errors = validate_derivation(base, derived, "base", "derived")
        assert errors == []

    def test_loosening_additional_properties_flagged(self):
        base = {"type": "object", "additionalProperties": False}
        derived = {"type": "object", "additionalProperties": True}
        errors = validate_derivation(base, derived, "base", "derived")
        assert any("loosens additionalProperties" in e for e in errors)

    def test_incompatible_type_change_flagged(self):
        base = {"type": "string"}
        derived = {"type": "integer"}
        errors = validate_derivation(base, derived, "base", "derived")
        assert any("not included in base" in e for e in errors)

    def test_disabling_base_property_flagged(self):
        base = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
        }
        derived = {
            "type": "object",
            "properties": {"a": False},
        }
        errors = validate_derivation(base, derived, "base", "derived")
        assert any("disables property" in e for e in errors)


class TestValidateClosedDescendantBranches:
    def test_no_errors_when_descendant_restates_property(self):
        ancestor = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
        }
        descendant = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "additionalProperties": False,
        }
        errors = validate_closed_descendant_branches(
            ancestor, descendant, "ancestor", "descendant"
        )
        assert errors == []

    def test_orphaned_property_flagged(self):
        ancestor = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
        }
        descendant = {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
        errors = validate_closed_descendant_branches(
            ancestor, descendant, "ancestor", "descendant"
        )
        assert any("unusable under allOf composition" in e for e in errors)

    def test_recurses_into_allof_branches(self):
        ancestor = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
        }
        descendant = {
            "allOf": [
                {"type": "object", "additionalProperties": False},
            ]
        }
        errors = validate_closed_descendant_branches(
            ancestor, descendant, "ancestor", "descendant"
        )
        assert any("a" in e for e in errors)

    def test_recurses_into_nested_common_properties(self):
        ancestor = {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {"x": {"type": "string"}},
                }
            },
        }
        descendant = {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                }
            },
        }
        errors = validate_closed_descendant_branches(
            ancestor, descendant, "ancestor", "descendant"
        )
        assert any("nested.x" in e for e in errors)


class TestValidateDerivationCompatibility:
    def test_combines_declaration_and_closed_branch_checks(self):
        base = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
        }
        derived = {
            "allOf": [base],
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
        errors = validate_derivation_compatibility(base, derived, "base", "derived")
        assert any("unusable under allOf composition" in e for e in errors)

    def test_non_dict_schema_handled(self):
        errors = validate_derivation_compatibility(True, True, "base", "derived")
        assert errors == []
