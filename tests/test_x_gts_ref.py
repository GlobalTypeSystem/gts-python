"""Tests for gts.x_gts_ref (x-gts-ref schema & instance validation, spec sec 9.5)."""

from gts.x_gts_ref import X_GTS_REF_SELF, XGtsRefValidator


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
        assert "must be a GTS identifier" in errors[0].reason

    def test_selected_type_self_reference_is_valid(self):
        validator = XGtsRefValidator()
        assert validator.is_self_reference(X_GTS_REF_SELF)
        assert validator.validate_schema({"x-gts-ref": X_GTS_REF_SELF}) == []

    def test_other_pointers_are_invalid(self):
        validator = XGtsRefValidator()
        for pointer in ("/missing/path", "/properties/id"):
            errors = validator.validate_schema({"x-gts-ref": pointer})
            assert len(errors) == 1
            assert "must be a GTS identifier" in errors[0].reason

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

    def test_ignores_x_gts_ref_in_annotation_data(self):
        schema = {
            "properties": {
                "payload": {
                    "const": {"x-gts-ref": "gts.x.test._.missing.v1~"},
                    "default": {"x-gts-ref": "not-a-gts-id"},
                }
            }
        }
        assert XGtsRefValidator().validate_schema(schema) == []

    def test_property_named_x_gts_ref_is_not_a_keyword(self):
        schema = {
            "properties": {
                "x-gts-ref": {"x-gts-ref": "notgts.*"},
            }
        }
        errors = XGtsRefValidator().validate_schema(schema)
        assert len(errors) == 1
        assert errors[0].field_path == "properties/x-gts-ref/x-gts-ref"

    def test_recurses_into_draft3_schema_forms(self):
        schema = {
            "$schema": "http://json-schema.org/draft-03/schema#",
            "extends": {"x-gts-ref": "invalid-extends"},
            "type": ["object", {"x-gts-ref": "invalid-type"}],
            "disallow": ["array", {"x-gts-ref": "invalid-disallow"}],
        }
        errors = XGtsRefValidator().validate_schema(schema)
        assert [error.field_path for error in errors] == [
            "extends/x-gts-ref",
            "type[1]/x-gts-ref",
            "disallow[1]/x-gts-ref",
        ]


class TestValidateSchemaRefExistence:
    def test_missing_concrete_constraint_type_fails(self):
        class FakeStore:
            def get(self, value):
                return None

        errors = XGtsRefValidator(store=FakeStore()).validate_schema_ref_existence(
            {"properties": {"ref": {"x-gts-ref": "gts.x.test._.foo.v1~"}}}
        )

        assert len(errors) == 1
        assert errors[0].field_path == "properties/ref/x-gts-ref"
        assert (
            "constraint type 'gts.x.test._.foo.v1~' is not registered"
            in errors[0].reason
        )

    def test_registered_and_wildcard_constraints_pass(self):
        class FakeStore:
            def get(self, value):
                return object() if value == "gts.x.test._.foo.v1~" else None

            def items(self):
                return [("gts.x.test._.foo.v1~", object())]

        errors = XGtsRefValidator(store=FakeStore()).validate_schema_ref_existence(
            {
                "allOf": [
                    {"x-gts-ref": "gts.x.test._.foo.v1~"},
                    {"x-gts-ref": "gts.x.test.*"},
                ]
            }
        )

        assert errors == []

    def test_annotation_data_does_not_require_constraint_type(self):
        class FakeStore:
            def get(self, value):
                return None

        errors = XGtsRefValidator(store=FakeStore()).validate_schema_ref_existence(
            {"const": {"x-gts-ref": "gts.x.test._.missing.v1~"}}
        )
        assert errors == []

    def test_selected_type_constraint_uses_explicit_leaf(self):
        class FakeStore:
            def get(self, value):
                return object() if value == "gts.x.test._.leaf.v1~" else None

        errors = XGtsRefValidator(store=FakeStore()).validate_schema_ref_existence(
            {"x-gts-ref": X_GTS_REF_SELF},
            selected_type_id="gts.x.test._.leaf.v1~",
        )
        assert errors == []


class TestValidateInstanceValue:
    def test_non_string_instance_value_error(self):
        error = XGtsRefValidator()._validate_ref_value(123, "gts.*", "ref", None)
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

    def test_self_reference_uses_explicit_selected_leaf(self):
        schema = {
            "$id": "gts.x.test._.base.v1~",
            "type": "object",
            "properties": {"ref": {"x-gts-ref": X_GTS_REF_SELF}},
        }
        leaf = "gts.x.test._.base.v1~x.test._.leaf.v1~"
        assert (
            XGtsRefValidator().validate_instance(
                {"ref": leaf}, schema, selected_type_id=leaf
            )
            == []
        )
        errors = XGtsRefValidator().validate_instance(
            {"ref": "gts.x.test._.base.v1~"}, schema, selected_type_id=leaf
        )
        assert len(errors) == 1
        assert "does not match pattern" in errors[0].reason

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
        errors = XGtsRefValidator().validate_instance(["gts.x.other.v1~"], schema)
        assert len(errors) == 1

    def test_tuple_additional_items_recursion(self):
        schema = {
            "type": "array",
            "items": [{"type": "string"}],
            "additionalItems": {
                "type": "string",
                "x-gts-ref": "gts.x.test._.target.v1~",
            },
        }
        errors = XGtsRefValidator().validate_instance(
            ["tuple-prefix", "gts.x.other._.target.v1~"], schema
        )
        assert len(errors) == 1
        assert errors[0].field_path == "[1]"

    def test_prefix_items_recursion(self):
        schema = {
            "type": "array",
            "prefixItems": [{"type": "string"}],
            "items": {
                "type": "string",
                "x-gts-ref": "gts.x.test._.target.v1~",
            },
        }
        errors = XGtsRefValidator().validate_instance(
            ["tuple-prefix", "gts.x.other._.target.v1~"], schema
        )
        assert len(errors) == 1
        assert errors[0].field_path == "[1]"

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
