"""Tests for gts.x_gts_ref (x-gts-ref schema & instance validation, spec sec 9.5)."""

from gts.x_gts_ref import XGtsRefValidator


class TestValidateSchema:
    def test_valid_absolute_pattern(self):
        errors = XGtsRefValidator().validate_schema(
            {"x-gts-ref": "gts.x.test._.foo.v1~"}
        )
        assert errors == []

    def test_valid_wildcard_pattern(self):
        errors = XGtsRefValidator().validate_schema({"x-gts-ref": "gts.*"})
        assert errors == []

    def test_valid_prefix_wildcard(self):
        errors = XGtsRefValidator().validate_schema({"x-gts-ref": "gts.x.test.*"})
        assert errors == []

    def test_invalid_wildcard_prefix_direct(self):
        error = XGtsRefValidator()._validate_gts_id_or_pattern("notgts*", "path")
        assert error is not None
        assert "Invalid GTS wildcard pattern" in error.reason

    def test_invalid_specific_gts_id(self):
        errors = XGtsRefValidator().validate_schema({"x-gts-ref": "gts.bad id"})
        assert len(errors) == 1
        assert "Invalid GTS identifier" in errors[0].reason

    def test_non_string_ref_value(self):
        errors = XGtsRefValidator().validate_schema({"x-gts-ref": 123})
        assert len(errors) == 1
        assert "must be a string" in errors[0].reason

    def test_invalid_prefix_value(self):
        errors = XGtsRefValidator().validate_schema({"x-gts-ref": "nope"})
        assert len(errors) == 1
        assert "must start with" in errors[0].reason

    def test_relative_pointer_resolves_to_valid_id(self):
        schema = {
            "$id": "gts.x.test._.foo.v1~",
            "properties": {
                "ref_field": {"x-gts-ref": "/$id"},
            },
        }
        errors = XGtsRefValidator().validate_schema(schema)
        assert errors == []

    def test_relative_pointer_unresolvable(self):
        schema = {"properties": {"ref_field": {"x-gts-ref": "/missing/path"}}}
        errors = XGtsRefValidator().validate_schema(schema)
        assert len(errors) == 1
        assert "Cannot resolve reference path" in errors[0].reason

    def test_relative_pointer_resolves_to_invalid_id(self):
        schema = {
            "not_gts": "definitely not a gts id !!",
            "properties": {"ref_field": {"x-gts-ref": "/not_gts"}},
        }
        errors = XGtsRefValidator().validate_schema(schema)
        assert len(errors) == 1
        assert "is not a valid GTS identifier" in errors[0].reason

    def test_recurses_into_nested_structures(self):
        schema = {
            "properties": {
                "child": {"x-gts-ref": "notgts.*"},
            }
        }
        errors = XGtsRefValidator().validate_schema(schema)
        assert len(errors) == 1
        assert "properties/child/x-gts-ref" in errors[0].field_path

    def test_recurses_into_list_of_dicts(self):
        schema = {"allOf": [{"x-gts-ref": "notgts.*"}]}
        errors = XGtsRefValidator().validate_schema(schema)
        assert len(errors) == 1
        assert "allOf[0]/x-gts-ref" in errors[0].field_path


class TestValidateInstanceValue:
    def test_non_string_instance_value_error(self):
        error = XGtsRefValidator()._validate_ref_value(123, "gts.*", "ref", {})
        assert error is not None
        assert "Value must be a string" in error.reason

    def test_relative_ref_pattern_resolution_on_instance(self):
        schema = {
            "$id": "gts.x.test._.foo.v1~",
            "type": "object",
            "properties": {"ref": {"x-gts-ref": "/$id"}},
        }
        errors = XGtsRefValidator().validate_instance(
            {"ref": "gts.x.test._.foo.v1~x.test._.bar.v1"}, schema
        )
        assert errors == []

    def test_relative_ref_pattern_resolution_fails_when_not_gts_prefix(self):
        schema = {
            "other": "not-gts-value",
            "type": "object",
            "properties": {"ref": {"x-gts-ref": "/other"}},
        }
        errors = XGtsRefValidator().validate_instance(
            {"ref": "gts.x.test._.foo.v1~"}, schema
        )
        assert len(errors) == 1
        assert "is not a GTS pattern" in errors[0].reason

    def test_wildcard_pattern_matches_prefix(self):
        errors = XGtsRefValidator().validate_instance(
            "gts.x.test._.foo.v1~", {"x-gts-ref": "gts.x.test.*"}
        )
        assert errors == []

    def test_wildcard_pattern_mismatch(self):
        errors = XGtsRefValidator().validate_instance(
            "gts.x.other._.foo.v1~", {"x-gts-ref": "gts.x.test.*"}
        )
        assert len(errors) == 1
        assert "does not match pattern" in errors[0].reason

    def test_exact_pattern_mismatch(self):
        errors = XGtsRefValidator().validate_instance(
            "gts.x.other._.foo.v1~", {"x-gts-ref": "gts.x.test._.foo.v1~"}
        )
        assert len(errors) == 1
        assert "does not match pattern" in errors[0].reason

    def test_store_lookup_missing_entity(self):
        class FakeStore:
            def get(self, value):
                return None

        errors = XGtsRefValidator(store=FakeStore()).validate_instance(
            "gts.x.test._.foo.v1~", {"x-gts-ref": "gts.*"}
        )
        assert len(errors) == 1
        assert "not found in registry" in errors[0].reason

    def test_store_lookup_found_entity(self):
        class FakeStore:
            def get(self, value):
                return object()

        errors = XGtsRefValidator(store=FakeStore()).validate_instance(
            "gts.x.test._.foo.v1~", {"x-gts-ref": "gts.*"}
        )
        assert errors == []

    def test_array_items_recursion(self):
        schema = {
            "type": "array",
            "items": {"x-gts-ref": "gts.x.test.*"},
        }
        errors = XGtsRefValidator().validate_instance(
            ["gts.x.other.v1~"], schema
        )
        assert len(errors) == 1

    def test_object_properties_recursion(self):
        schema = {
            "type": "object",
            "properties": {"ref": {"x-gts-ref": "gts.x.test.*"}},
        }
        errors = XGtsRefValidator().validate_instance(
            {"ref": "gts.x.other.v1~"}, schema
        )
        assert len(errors) == 1

    def test_root_ref_traverses_nested_constraints(self):
        schema = {
            "type": "object",
            "properties": {
                "link": {"x-gts-ref": "gts.x.test.*"},
                "child": {"$ref": "#"},
            },
        }

        errors = XGtsRefValidator().validate_instance(
            {"child": {"link": "gts.x.other.v1~"}}, schema
        )

        assert len(errors) == 1
        assert errors[0].field_path == "child.link"

    def test_any_of_no_branch_matched(self):
        schema = {
            "anyOf": [
                {"x-gts-ref": "gts.x.test._.a.v1~"},
                {"x-gts-ref": "gts.x.test._.b.v1~"},
            ]
        }
        errors = XGtsRefValidator().validate_instance(
            "gts.x.test._.c.v1~x.test._.item.v1", schema
        )
        assert any("anyOf: no branch matched" in e.reason for e in errors)

    def test_any_of_matches_one_branch(self):
        schema = {
            "anyOf": [
                {"x-gts-ref": "gts.x.test._.a.v1~"},
                {"x-gts-ref": "gts.x.test._.b.v1~"},
            ]
        }
        errors = XGtsRefValidator().validate_instance(
            "gts.x.test._.a.v1~x.test._.item.v1", schema
        )
        assert errors == []

    def test_all_of_validates_each_matching_branch(self):
        schema = {
            "allOf": [
                {"type": "string", "x-gts-ref": "gts.x.test.*"},
            ]
        }
        errors = XGtsRefValidator().validate_instance("gts.x.other.v1~", schema)
        assert len(errors) == 1
