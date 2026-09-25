"""Tests for gts.compatibility (spec sec 4, OP#8 & OP#12 inclusion primitive)."""

from gts.compatibility import (
    CLOSED,
    COMPATIBLE,
    INCOMPATIBLE,
    OPEN,
    PARTIAL,
    UNKNOWN,
    boolean_schema_value,
    sanitize,
    check_backward_compatibility,
    check_forward_compatibility,
    classify_object_levels,
    explain_verdict,
    full_verdict,
    check_accepted_set_inclusion,
)


class TestObjectLevelClassification:
    def test_open_when_additional_properties_absent(self):
        levels = classify_object_levels(
            {"type": "object", "properties": {"a": {"type": "string"}}}
        )
        assert levels == [{"path": "$", "content_model": OPEN}]

    def test_closed_when_additional_properties_false(self):
        levels = classify_object_levels(
            {"type": "object", "properties": {}, "additionalProperties": False}
        )
        assert levels == [{"path": "$", "content_model": CLOSED}]

    def test_partial_when_additional_properties_is_schema(self):
        levels = classify_object_levels(
            {"type": "object", "additionalProperties": {"type": "string"}}
        )
        assert levels == [{"path": "$", "content_model": PARTIAL}]

    def test_partial_when_pattern_properties_present(self):
        levels = classify_object_levels(
            {
                "type": "object",
                "additionalProperties": False,
                "patternProperties": {"^x-": {"type": "string"}},
            }
        )
        assert levels == [{"path": "$", "content_model": PARTIAL}]

    def test_nested_levels_are_reported(self):
        levels = classify_object_levels(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "payload": {"type": "object", "properties": {}},
                },
            }
        )
        paths = {level["path"]: level["content_model"] for level in levels}
        assert paths == {"$": CLOSED, "$.payload": OPEN}

    def test_levels_under_applicator_keywords_are_reported(self):
        levels = classify_object_levels(
            {
                "type": "object",
                "additionalProperties": {"type": "object", "properties": {}},
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"type": "object", "additionalProperties": False},
                    },
                    "map": {
                        "type": "object",
                        "patternProperties": {
                            "^x-": {"type": "object", "additionalProperties": False}
                        },
                    },
                },
            }
        )
        paths = {level["path"]: level["content_model"] for level in levels}
        # Object levels nested under additionalProperties, array items, and
        # patternProperties are all classified now, not only `properties`.
        assert paths["$.additionalProperties"] == OPEN
        assert paths["$.items[]"] == CLOSED
        assert paths["$.map.patternProperties[^x-]"] == CLOSED


class TestExplainVerdict:
    def test_compatible_direction_has_no_reasons(self):
        assert explain_verdict(COMPATIBLE, backward=True) == []

    def test_incompatible_backward_explains_relation(self):
        reasons = explain_verdict(INCOMPATIBLE, backward=True)
        assert reasons
        assert "backward incompatible" in reasons[0]

    def test_incompatible_forward_explains_relation(self):
        reasons = explain_verdict(INCOMPATIBLE, backward=False)
        assert reasons
        assert "forward incompatible" in reasons[0]

    def test_unknown_on_differing_dialects(self):
        reasons = explain_verdict(UNKNOWN, backward=True, differing_dialects=True)
        assert reasons and "different JSON Schema dialects" in reasons[0]

    def test_unknown_without_dialect_difference(self):
        reasons = explain_verdict(UNKNOWN, backward=False, differing_dialects=False)
        assert reasons and "could not be proved or disproved" in reasons[0]


class TestBooleanSchemaValue:
    def test_bool_passthrough(self):
        assert boolean_schema_value(True) is True
        assert boolean_schema_value(False) is False

    def test_empty_dict_is_true(self):
        assert boolean_schema_value({}) is True

    def test_annotations_only_is_true(self):
        assert boolean_schema_value({"title": "x", "description": "y"}) is True

    def test_not_empty_is_false(self):
        assert boolean_schema_value({"not": {}}) is False

    def test_double_negation_is_true(self):
        assert boolean_schema_value({"not": {"not": {}}}) is True

    def test_multiple_assertions_is_none(self):
        assert boolean_schema_value({"type": "string", "minLength": 1}) is None

    def test_non_bool_non_dict_is_none(self):
        assert boolean_schema_value("nope") is None
        assert boolean_schema_value(42) is None

    def test_not_of_unprovable_is_none(self):
        assert boolean_schema_value({"not": {"type": "string", "minLength": 1}}) is None


class TestSanitize:
    def test_strips_meta_keywords(self):
        schema = {"$id": "x", "$schema": "y", "type": "string"}
        assert sanitize(schema) == {"type": "string"}

    def test_strips_x_gts_keys(self):
        schema = {"x-gts-ref": "gts.*", "type": "string"}
        assert sanitize(schema) == {"type": "string"}

    def test_recurses_into_lists_and_nested_dicts(self):
        schema = {
            "allOf": [{"$id": "a", "type": "string"}],
            "properties": {"p": {"$comment": "x", "type": "integer"}},
        }
        result = sanitize(schema)
        assert result["allOf"] == [{"type": "string"}]
        assert result["properties"]["p"] == {"type": "integer"}

    def test_non_dict_non_list_passthrough(self):
        assert sanitize("abc") == "abc"
        assert sanitize(5) == 5

    def test_drops_redundant_type_with_enum(self):
        schema = {"enum": ["a", "b"], "type": "string"}
        result = sanitize(schema)
        assert "type" not in result

    def test_keeps_type_when_enum_has_other_types(self):
        schema = {"enum": ["a", 1], "type": "string"}
        result = sanitize(schema)
        assert result.get("type") == "string"


class TestUnevaluatedPropertiesLowering:
    def test_conditional_applicators_are_not_lowered(self):
        from gts.compatibility import _lower_root_unevaluated_properties

        assert (
            _lower_root_unevaluated_properties(
                {"unevaluatedProperties": False, "if": {"properties": {"a": True}}}
            )
            is None
        )


class TestCompatibilityVerdicts:
    def test_backward_compatible_widened_enum(self):
        # old accepted set must be subset of new
        result = check_backward_compatibility(
            {"type": "string", "enum": ["a"]},
            {"type": "string", "enum": ["a", "b"]},
        )
        assert result == COMPATIBLE

    def test_backward_incompatible_narrowed_type(self):
        result = check_backward_compatibility(
            {"type": "string"},
            {"type": "integer"},
        )
        assert result == INCOMPATIBLE

    def test_forward_compatible_case(self):
        result = check_forward_compatibility(
            {"type": "string", "enum": ["a", "b"]},
            {"type": "string", "enum": ["a"]},
        )
        assert result == COMPATIBLE

    def test_full_verdict_incompatible_dominates(self):
        assert full_verdict(INCOMPATIBLE, COMPATIBLE) == INCOMPATIBLE
        assert full_verdict(COMPATIBLE, INCOMPATIBLE) == INCOMPATIBLE

    def test_full_verdict_compatible_both(self):
        assert full_verdict(COMPATIBLE, COMPATIBLE) == COMPATIBLE

    def test_full_verdict_unknown_otherwise(self):
        assert full_verdict(UNKNOWN, COMPATIBLE) == UNKNOWN
        assert full_verdict(COMPATIBLE, UNKNOWN) == UNKNOWN

    def test_accepted_set_inclusion_true(self):
        assert check_accepted_set_inclusion({"type": "string"}, {}) is True

    def test_accepted_set_inclusion_false(self):
        assert (
            check_accepted_set_inclusion({"type": "string"}, {"type": "integer"})
            is False
        )

    def test_boolean_schema_operands_are_coerced(self):
        # True == accept everything, False == accept nothing
        assert check_accepted_set_inclusion(False, True) is True
        assert check_accepted_set_inclusion(True, False) is False
