"""Tests for gts.schema_cast (OP#9 version casting)."""

from gts.schema_cast import GtsEntityCastResult, SchemaCastError


class TestToDict:
    def test_to_dict_with_verdict_strings(self):
        result = GtsEntityCastResult(
            from_id="a",
            to_id="b",
            backward_verdict="compatible",
            forward_verdict="unknown",
            full_verdict="unknown",
        )
        d = result.to_dict()
        assert d["backward_compatibility"] == "compatible"
        assert d["forward_compatibility"] == "unknown"
        assert d["full_compatibility"] == "unknown"

    def test_to_dict_falls_back_to_bool_flags(self):
        result = GtsEntityCastResult(
            from_id="a",
            to_id="b",
            is_backward_compatible=True,
            is_forward_compatible=False,
            is_fully_compatible=False,
        )
        d = result.to_dict()
        assert d["backward_compatibility"] == "compatible"
        assert d["forward_compatibility"] == "incompatible"

    def test_to_dict_includes_error_when_present(self):
        result = GtsEntityCastResult(error="boom")
        d = result.to_dict()
        assert d["error"] == "boom"

    def test_to_dict_omits_error_when_absent(self):
        result = GtsEntityCastResult()
        d = result.to_dict()
        assert "error" not in d


class TestInferDirection:
    def test_up_direction(self):
        assert (
            GtsEntityCastResult._infer_direction(
                "gts.x.test._.foo.v1.0~x.test._.bar.v1.0",
                "gts.x.test._.foo.v1.0~x.test._.bar.v1.5",
            )
            == "up"
        )

    def test_down_direction(self):
        assert (
            GtsEntityCastResult._infer_direction(
                "gts.x.test._.foo.v1.0~x.test._.bar.v1.5",
                "gts.x.test._.foo.v1.0~x.test._.bar.v1.0",
            )
            == "down"
        )

    def test_none_direction_same_minor(self):
        assert (
            GtsEntityCastResult._infer_direction(
                "gts.x.test._.foo.v1.0~x.test._.bar.v1.0",
                "gts.x.test._.foo.v1.0~x.test._.bar.v1.0",
            )
            == "none"
        )

    def test_unknown_on_invalid_id(self):
        assert GtsEntityCastResult._infer_direction("not-an-id", "also-not") == "unknown"


class TestEffectiveObjectSchema:
    def test_non_dict_returns_empty(self):
        assert GtsEntityCastResult._effective_object_schema("x") == {}

    def test_direct_properties_returned(self):
        s = {"properties": {"a": {}}}
        assert GtsEntityCastResult._effective_object_schema(s) == s

    def test_allof_branch_with_properties_found(self):
        s = {"allOf": [{"title": "x"}, {"properties": {"a": {}}}]}
        result = GtsEntityCastResult._effective_object_schema(s)
        assert result == {"properties": {"a": {}}}

    def test_no_match_returns_schema_itself(self):
        s = {"type": "string"}
        assert GtsEntityCastResult._effective_object_schema(s) == s


class TestFlattenSchema:
    def test_merges_allof(self):
        schema = {
            "allOf": [
                {"properties": {"a": {}}, "required": ["a"]},
            ],
            "properties": {"b": {}},
            "required": ["b"],
        }
        flat = GtsEntityCastResult._flatten_schema(schema)
        assert set(flat["properties"].keys()) == {"a", "b"}
        assert set(flat["required"]) == {"a", "b"}

    def test_additional_properties_top_level_overrides(self):
        schema = {
            "allOf": [{"additionalProperties": False}],
            "additionalProperties": True,
        }
        flat = GtsEntityCastResult._flatten_schema(schema)
        assert flat["additionalProperties"] is True


class TestCastInstanceToSchema:
    def test_non_dict_instance_raises(self):
        try:
            GtsEntityCastResult._cast_instance_to_schema("nope", {})
            assert False, "expected SchemaCastError"
        except SchemaCastError:
            pass

    def test_missing_required_without_default_reports_reason(self):
        schema = {"properties": {"a": {"type": "string"}}, "required": ["a"]}
        result, added, removed, reasons = GtsEntityCastResult._cast_instance_to_schema(
            {}, schema
        )
        assert "a" not in result
        assert any("Missing required property" in r for r in reasons)

    def test_missing_required_with_default_added(self):
        schema = {
            "properties": {"a": {"type": "string", "default": "x"}},
            "required": ["a"],
        }
        result, added, removed, reasons = GtsEntityCastResult._cast_instance_to_schema(
            {}, schema
        )
        assert result["a"] == "x"
        assert "a" in added

    def test_optional_default_added_when_missing(self):
        schema = {"properties": {"b": {"default": 5}}}
        result, added, removed, reasons = GtsEntityCastResult._cast_instance_to_schema(
            {}, schema
        )
        assert result["b"] == 5
        assert "b" in added

    def test_const_gts_id_updated(self):
        schema = {
            "properties": {
                "type": {"const": "gts.x.test._.foo.v2~"},
            }
        }
        instance = {"type": "gts.x.test._.foo.v1~"}
        result, added, removed, reasons = GtsEntityCastResult._cast_instance_to_schema(
            instance, schema
        )
        assert result["type"] == "gts.x.test._.foo.v2~"

    def test_additional_properties_false_removes_extra(self):
        schema = {
            "properties": {"a": {"type": "string"}},
            "additionalProperties": False,
        }
        instance = {"a": "x", "extra": "y"}
        result, added, removed, reasons = GtsEntityCastResult._cast_instance_to_schema(
            instance, schema
        )
        assert "extra" not in result
        assert "extra" in removed

    def test_nested_object_recursion(self):
        schema = {
            "properties": {
                "child": {
                    "type": "object",
                    "properties": {"x": {"type": "string", "default": "d"}},
                }
            }
        }
        instance = {"child": {}}
        result, added, removed, reasons = GtsEntityCastResult._cast_instance_to_schema(
            instance, schema
        )
        assert result["child"]["x"] == "d"
        assert "child.x" in added

    def test_nested_array_of_objects_recursion(self):
        schema = {
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"x": {"default": "d"}},
                    },
                }
            }
        }
        instance = {"items": [{}]}
        result, added, removed, reasons = GtsEntityCastResult._cast_instance_to_schema(
            instance, schema
        )
        assert result["items"][0]["x"] == "d"
        assert "items[0].x" in added


class TestRemoveGtsConstConstraints:
    def test_replaces_const_gts_id_with_type_string(self):
        schema = {"const": "gts.x.test._.foo.v1~"}
        result = GtsEntityCastResult._remove_gts_const_constraints(schema)
        assert result == {"type": "string"}

    def test_non_gts_const_left_intact(self):
        schema = {"const": "plainvalue"}
        result = GtsEntityCastResult._remove_gts_const_constraints(schema)
        assert result == {"const": "plainvalue"}

    def test_recurses_into_nested_dict_and_list(self):
        schema = {
            "properties": {"a": {"const": "gts.x.test._.foo.v1~"}},
            "allOf": [{"const": "gts.x.test._.bar.v1~"}],
        }
        result = GtsEntityCastResult._remove_gts_const_constraints(schema)
        assert result["properties"]["a"] == {"type": "string"}
        assert result["allOf"][0] == {"type": "string"}

    def test_non_dict_passthrough(self):
        assert GtsEntityCastResult._remove_gts_const_constraints("abc") == "abc"


class TestCheckMinMaxConstraint:
    def test_backward_tightened_minimum_flagged(self):
        errors = GtsEntityCastResult._check_min_max_constraint(
            "p", {"minimum": 1}, {"minimum": 5}, "minimum", "maximum", True
        )
        assert any("increased" in e for e in errors)

    def test_backward_added_minimum_flagged(self):
        errors = GtsEntityCastResult._check_min_max_constraint(
            "p", {}, {"minimum": 5}, "minimum", "maximum", True
        )
        assert any("added" in e for e in errors)

    def test_forward_relaxed_minimum_flagged(self):
        errors = GtsEntityCastResult._check_min_max_constraint(
            "p", {"minimum": 5}, {"minimum": 1}, "minimum", "maximum", False
        )
        assert any("decreased" in e for e in errors)

    def test_forward_removed_minimum_flagged(self):
        errors = GtsEntityCastResult._check_min_max_constraint(
            "p", {"minimum": 5}, {}, "minimum", "maximum", False
        )
        assert any("removed" in e for e in errors)

    def test_backward_tightened_maximum_flagged(self):
        errors = GtsEntityCastResult._check_min_max_constraint(
            "p", {"maximum": 10}, {"maximum": 5}, "minimum", "maximum", True
        )
        assert any("decreased" in e for e in errors)

    def test_forward_relaxed_maximum_flagged(self):
        errors = GtsEntityCastResult._check_min_max_constraint(
            "p", {"maximum": 5}, {"maximum": 10}, "minimum", "maximum", False
        )
        assert any("increased" in e for e in errors)


class TestCheckConstraintCompatibility:
    def test_numeric_constraints_checked(self):
        errors = GtsEntityCastResult._check_constraint_compatibility(
            "p", {"type": "number", "minimum": 1}, {"type": "number", "minimum": 5}
        )
        assert errors

    def test_string_constraints_checked(self):
        errors = GtsEntityCastResult._check_constraint_compatibility(
            "p", {"type": "string", "minLength": 1}, {"type": "string", "minLength": 5}
        )
        assert errors

    def test_array_constraints_checked(self):
        errors = GtsEntityCastResult._check_constraint_compatibility(
            "p", {"type": "array", "minItems": 1}, {"type": "array", "minItems": 5}
        )
        assert errors

    def test_other_types_no_errors(self):
        errors = GtsEntityCastResult._check_constraint_compatibility(
            "p", {"type": "boolean"}, {"type": "boolean"}
        )
        assert errors == []


class TestCheckSchemaCompatibility:
    def test_backward_added_required_flagged(self):
        old = {"properties": {"a": {}}}
        new = {"properties": {"a": {}}, "required": ["a"]}
        ok, errors = GtsEntityCastResult._check_backward_compatibility(old, new)
        assert not ok
        assert any("Added required" in e for e in errors)

    def test_forward_removed_required_flagged(self):
        old = {"properties": {"a": {}}, "required": ["a"]}
        new = {"properties": {"a": {}}}
        ok, errors = GtsEntityCastResult._check_forward_compatibility(old, new)
        assert not ok
        assert any("Removed required" in e for e in errors)

    def test_type_change_flagged(self):
        old = {"properties": {"a": {"type": "string"}}}
        new = {"properties": {"a": {"type": "integer"}}}
        ok, errors = GtsEntityCastResult._check_backward_compatibility(old, new)
        assert not ok
        assert any("type changed" in e for e in errors)

    def test_backward_added_enum_values_flagged(self):
        old = {"properties": {"a": {"enum": ["x"]}}}
        new = {"properties": {"a": {"enum": ["x", "y"]}}}
        ok, errors = GtsEntityCastResult._check_backward_compatibility(old, new)
        assert not ok
        assert any("added enum values" in e for e in errors)

    def test_forward_removed_enum_values_flagged(self):
        old = {"properties": {"a": {"enum": ["x", "y"]}}}
        new = {"properties": {"a": {"enum": ["x"]}}}
        ok, errors = GtsEntityCastResult._check_forward_compatibility(old, new)
        assert not ok
        assert any("removed enum values" in e for e in errors)

    def test_nested_object_errors_prefixed(self):
        old = {"properties": {"a": {"type": "object", "properties": {"b": {"type": "string"}}}}}
        new = {"properties": {"a": {"type": "object", "properties": {"b": {"type": "integer"}}}}}
        ok, errors = GtsEntityCastResult._check_backward_compatibility(old, new)
        assert not ok
        assert any("Property 'a':" in e for e in errors)

    def test_fully_compatible_returns_true(self):
        old = {"properties": {"a": {"type": "string"}}}
        new = {"properties": {"a": {"type": "string"}}, "properties2": {}}
        ok, errors = GtsEntityCastResult._check_backward_compatibility(old, {"properties": {"a": {"type": "string"}}})
        assert ok
        assert errors == []


class TestDiffObjects:
    def test_added_and_removed_properties(self):
        added, removed, changed = [], [], []
        GtsEntityCastResult._diff_objects(
            {"properties": {"a": {}}},
            {"properties": {"b": {}}},
            "",
            added,
            removed,
            changed,
        )
        assert removed == ["a"]
        assert added == ["b"]

    def test_type_and_format_changes(self):
        added, removed, changed = [], [], []
        GtsEntityCastResult._diff_objects(
            {"properties": {"a": {"type": "string", "format": "date"}}},
            {"properties": {"a": {"type": "integer", "format": "int32"}}},
            "",
            added,
            removed,
            changed,
        )
        change_strs = [c["change"] for c in changed]
        assert any("type:" in c for c in change_strs)
        assert any("format:" in c for c in change_strs)

    def test_required_added_and_removed(self):
        added, removed, changed = [], [], []
        GtsEntityCastResult._diff_objects(
            {"required": ["a"]},
            {"required": ["b"]},
            "",
            added,
            removed,
            changed,
        )
        change_strs = {(c["path"], c["change"]) for c in changed}
        assert ("a", "required: removed") in change_strs
        assert ("b", "required: added") in change_strs


class TestOnlyOptionalAddRemove:
    def test_identical_schemas_true(self):
        assert GtsEntityCastResult._only_optional_add_remove(
            {"type": "string"}, {"type": "string"}, "", []
        )

    def test_value_mismatch_for_non_dicts(self):
        reasons = []
        result = GtsEntityCastResult._only_optional_add_remove(1, 2, "path", reasons)
        assert not result
        assert any("value changed" in r for r in reasons)

    def test_keyword_change_detected(self):
        reasons = []
        result = GtsEntityCastResult._only_optional_add_remove(
            {"type": "string"}, {"type": "integer"}, "path", reasons
        )
        assert not result
        assert any("keyword 'type' changed" in r for r in reasons)

    def test_required_added_and_removed_detected(self):
        reasons = []
        result = GtsEntityCastResult._only_optional_add_remove(
            {"required": ["a"]}, {"required": ["b"]}, "path", reasons
        )
        assert not result
        assert any("required added" in r for r in reasons)
        assert any("required removed" in r for r in reasons)

    def test_nested_property_recursion(self):
        reasons = []
        a = {"properties": {"x": {"type": "string"}}}
        b = {"properties": {"x": {"type": "integer"}}}
        result = GtsEntityCastResult._only_optional_add_remove(a, b, "", reasons)
        assert not result
        assert any("properties.x" in r for r in reasons)


class TestCastClassmethod:
    def test_cast_backward_incompatible_and_validation_error(self):
        from_schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
        }
        to_schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
            "required": ["b"],
        }
        result = GtsEntityCastResult.cast(
            "gts.x.test._.foo.v1~a.b._.c.v1",
            "gts.x.test._.foo.v2~",
            {"a": "x"},
            from_schema,
            to_schema,
        )
        assert result.is_fully_compatible is False
        assert result.incompatibility_reasons

    def test_cast_fully_compatible(self):
        from_schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
        }
        to_schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
        }
        result = GtsEntityCastResult.cast(
            "gts.x.test._.foo.v1~a.b._.c.v1",
            "gts.x.test._.foo.v1~",
            {"a": "x"},
            from_schema,
            to_schema,
        )
        assert result.is_fully_compatible is True
        assert result.casted_entity == {"a": "x"}

    def test_cast_with_non_dict_instance_content_defaults_to_empty(self):
        result = GtsEntityCastResult.cast(
            "gts.x.test._.foo.v1.0~x.test._.bar.v1.0",
            "gts.x.test._.foo.v1.0~",
            "not-a-dict",
            {},
            {},
        )
        assert result.casted_entity == {}
