"""Tests for gts.x_gts_ref: the entity- and pattern-reference keywords
(x-gts-type-ref, x-gts-instance-ref, x-gts-pattern-ref; README §9.6, ADR-0007/0008)."""

from gts.x_gts_ref import (
    SELF_ID_REF,
    X_GTS_INSTANCE_REF,
    X_GTS_PATTERN_REF,
    X_GTS_TYPE_REF,
    XGtsRefValidator,
)

DRAFT7 = "draft-07"

_TYPE = "gts.x.test._.foo.v1~"
_INSTANCE = "gts.x.test._.foo.v1~x.test._.bar.v1"


def _validator(**kwargs):
    return XGtsRefValidator(inherited_dialect=DRAFT7, **kwargs)


class TestValidateSchema:
    def test_valid_type_ref_operand(self):
        assert _validator().validate_schema({X_GTS_TYPE_REF: _TYPE}) == []

    def test_valid_instance_ref_operand(self):
        assert _validator().validate_schema({X_GTS_INSTANCE_REF: _TYPE}) == []

    def test_valid_pattern_ref_wildcard_operand(self):
        assert _validator().validate_schema({X_GTS_PATTERN_REF: "gts.*"}) == []

    def test_valid_pattern_ref_family_operand(self):
        assert _validator().validate_schema({X_GTS_PATTERN_REF: "gts.x.test.*"}) == []

    def test_type_ref_rejects_instance_operand(self):
        errors = _validator().validate_schema({X_GTS_TYPE_REF: _INSTANCE})
        assert len(errors) == 1
        assert "selects no GTS Type Identifiers" in errors[0].reason

    def test_instance_ref_allows_instance_operand(self):
        assert _validator().validate_schema({X_GTS_INSTANCE_REF: _INSTANCE}) == []

    def test_non_string_operand(self):
        errors = _validator().validate_schema({X_GTS_TYPE_REF: 123})
        assert len(errors) == 1
        assert "must be a string" in errors[0].reason

    def test_invalid_operand_value(self):
        errors = _validator().validate_schema({X_GTS_PATTERN_REF: "nope"})
        assert len(errors) == 1
        assert "expected a GTS identifier, a wildcard pattern" in errors[0].reason

    def test_self_reference_requires_selected_type(self):
        # No $id to resolve /$id against: a schema error.
        errors = _validator().validate_schema({X_GTS_TYPE_REF: SELF_ID_REF})
        assert len(errors) == 1
        assert "has no selected GTS Type Schema" in errors[0].reason

    def test_self_reference_valid_with_id(self):
        schema = {"$id": "gts://" + _TYPE, X_GTS_TYPE_REF: SELF_ID_REF}
        assert _validator().validate_schema(schema) == []

    def test_other_pointers_are_invalid(self):
        for pointer in ("/missing/path", "/properties/id"):
            errors = _validator().validate_schema({X_GTS_PATTERN_REF: pointer})
            assert len(errors) == 1

    def test_recurses_into_nested_structures(self):
        schema = {"properties": {"child": {X_GTS_PATTERN_REF: "notgts.*"}}}
        errors = _validator().validate_schema(schema)
        assert len(errors) == 1
        assert "properties/child/x-gts-pattern-ref" in errors[0].field_path

    def test_recurses_into_list_of_dicts(self):
        schema = {"allOf": [{X_GTS_TYPE_REF: "notgts.*"}]}
        errors = _validator().validate_schema(schema)
        assert len(errors) == 1
        assert "allOf[0]/x-gts-type-ref" in errors[0].field_path

    def test_ignores_keyword_shaped_literal_data(self):
        schema = {
            "properties": {
                "payload": {
                    "const": {X_GTS_TYPE_REF: "gts.x.test._.missing.v1~"},
                    "default": {X_GTS_PATTERN_REF: "not-a-gts-id"},
                }
            }
        }
        assert _validator().validate_schema(schema) == []

    def test_property_named_like_keyword_is_not_a_keyword(self):
        schema = {"properties": {X_GTS_TYPE_REF: {X_GTS_PATTERN_REF: "notgts.*"}}}
        errors = _validator().validate_schema(schema)
        assert len(errors) == 1
        assert errors[0].field_path == "properties/x-gts-type-ref/x-gts-pattern-ref"


class TestValidateSchemaRefExistence:
    def test_missing_concrete_constraint_type_fails(self):
        class FakeStore:
            def get(self, value):
                return None

        errors = _validator(store=FakeStore()).validate_schema_ref_existence(
            {"properties": {"ref": {X_GTS_TYPE_REF: _TYPE}}}
        )
        assert len(errors) == 1
        assert errors[0].field_path == "properties/ref/x-gts-type-ref"
        assert "is not registered" in errors[0].reason

    def test_registered_and_wildcard_constraints_pass(self):
        class FakeStore:
            def get(self, value):
                return object() if value == _TYPE else None

            def items(self):
                return [(_TYPE, object())]

        errors = _validator(store=FakeStore()).validate_schema_ref_existence(
            {
                "allOf": [
                    {X_GTS_TYPE_REF: _TYPE},
                    {X_GTS_INSTANCE_REF: "gts.x.test.*"},
                ]
            }
        )
        assert errors == []

    def test_pattern_ref_operand_is_not_looked_up(self):
        class FakeStore:
            def get(self, value):
                return None

            def items(self):
                return []

        # x-gts-pattern-ref never consults the registry (ADR-0007).
        errors = _validator(store=FakeStore()).validate_schema_ref_existence(
            {X_GTS_PATTERN_REF: "gts.x.test._.missing.v1~"}
        )
        assert errors == []

    def test_keyword_shaped_literal_data_is_not_looked_up(self):
        class FakeStore:
            def get(self, value):
                return None

        errors = _validator(store=FakeStore()).validate_schema_ref_existence(
            {"const": {X_GTS_TYPE_REF: "gts.x.test._.missing.v1~"}}
        )
        assert errors == []

    def test_self_reference_uses_explicit_leaf(self):
        leaf = "gts.x.test._.leaf.v1~"

        class FakeStore:
            def get(self, value):
                return object() if value == leaf else None

        errors = _validator(store=FakeStore()).validate_schema_ref_existence(
            {X_GTS_TYPE_REF: SELF_ID_REF}, selected_type_id=leaf
        )
        assert errors == []


class TestValidateInstanceValue:
    def test_type_ref_accepts_type_candidate(self):
        errors = _validator().validate_instance(_TYPE, {X_GTS_TYPE_REF: "gts.x.test.*"})
        assert errors == []

    def test_type_ref_rejects_instance_candidate(self):
        errors = _validator().validate_instance(
            _INSTANCE, {X_GTS_TYPE_REF: "gts.x.test.*"}
        )
        assert len(errors) == 1
        assert "requires a GTS Type Identifier" in errors[0].reason

    def test_instance_ref_rejects_type_candidate(self):
        errors = _validator().validate_instance(
            _TYPE, {X_GTS_INSTANCE_REF: "gts.x.test.*"}
        )
        assert len(errors) == 1
        assert "requires a GTS Instance Identifier" in errors[0].reason

    def test_pattern_ref_accepts_covered_pattern(self):
        errors = _validator().validate_instance(
            "gts.x.test._.foo.v1~*", {X_GTS_PATTERN_REF: "gts.x.test.*"}
        )
        assert errors == []

    def test_pattern_ref_rejects_uncovered_pattern(self):
        errors = _validator().validate_instance(
            "gts.x.other.*", {X_GTS_PATTERN_REF: "gts.x.test.*"}
        )
        assert len(errors) == 1
        assert "selects GTS identifiers outside" in errors[0].reason

    def test_pattern_ref_rejects_malformed_value(self):
        errors = _validator().validate_instance(
            "not a pattern", {X_GTS_PATTERN_REF: "gts.*"}
        )
        assert len(errors) == 1
        assert "not a valid GTS identifier pattern" in errors[0].reason

    def test_self_reference_uses_explicit_selected_leaf(self):
        schema = {
            "$id": "gts.x.test._.base.v1~",
            "type": "object",
            "properties": {"ref": {X_GTS_TYPE_REF: SELF_ID_REF}},
        }
        leaf = "gts.x.test._.base.v1~x.test._.leaf.v1~"
        assert (
            _validator().validate_instance({"ref": leaf}, schema, selected_type_id=leaf)
            == []
        )
        errors = _validator().validate_instance(
            {"ref": "gts.x.test._.base.v1~"}, schema, selected_type_id=leaf
        )
        assert len(errors) == 1
        assert "does not match pattern" in errors[0].reason

    def test_exact_pattern_mismatch(self):
        errors = _validator().validate_instance(
            "gts.x.other._.foo.v1~", {X_GTS_TYPE_REF: _TYPE}
        )
        assert len(errors) == 1
        assert "does not match pattern" in errors[0].reason

    def test_store_lookup_missing_entity(self):
        class FakeStore:
            def get(self, value):
                return None

        errors = _validator(store=FakeStore()).validate_instance(
            _TYPE, {X_GTS_TYPE_REF: "gts.*"}
        )
        assert len(errors) == 1
        assert "not found in registry" in errors[0].reason

    def test_pattern_ref_does_not_consult_store(self):
        class FakeStore:
            def get(self, value):
                return None

        # A covered pattern value is accepted even though nothing is registered.
        errors = _validator(store=FakeStore()).validate_instance(
            "gts.x.test._.foo.v1~", {X_GTS_PATTERN_REF: "gts.*"}
        )
        assert errors == []

    def test_array_items_recursion(self):
        schema = {"type": "array", "items": {X_GTS_TYPE_REF: "gts.x.test.*"}}
        errors = _validator().validate_instance(["gts.x.other.v1~"], schema)
        assert len(errors) == 1

    def test_tuple_additional_items_recursion(self):
        schema = {
            "type": "array",
            "items": [{"type": "string"}],
            "additionalItems": {"type": "string", X_GTS_TYPE_REF: "gts.x.test.*"},
        }
        errors = _validator().validate_instance(
            ["tuple-prefix", "gts.x.other._.target.v1~"], schema
        )
        assert len(errors) == 1
        assert errors[0].field_path == "[1]"

    def test_prefix_items_recursion(self):
        schema = {
            "type": "array",
            "prefixItems": [{"type": "string"}],
            "items": {"type": "string", X_GTS_TYPE_REF: "gts.x.test.*"},
        }
        errors = _validator().validate_instance(
            ["tuple-prefix", "gts.x.other._.target.v1~"], schema
        )
        assert len(errors) == 1
        assert errors[0].field_path == "[1]"

    def test_object_properties_recursion(self):
        schema = {
            "type": "object",
            "properties": {"ref": {X_GTS_TYPE_REF: "gts.x.test.*"}},
        }
        errors = _validator().validate_instance({"ref": "gts.x.other.v1~"}, schema)
        assert len(errors) == 1

    def test_root_ref_traverses_nested_constraints(self):
        schema = {
            "type": "object",
            "properties": {
                "link": {X_GTS_TYPE_REF: "gts.x.test.*"},
                "child": {"$ref": "#"},
            },
        }
        errors = _validator().validate_instance(
            {"child": {"link": "gts.x.other.v1~"}}, schema
        )
        assert len(errors) == 1
        assert errors[0].field_path == "child.link"

    def test_any_of_no_branch_matched(self):
        schema = {
            "anyOf": [
                {X_GTS_TYPE_REF: "gts.x.test._.a.v1~"},
                {X_GTS_TYPE_REF: "gts.x.test._.b.v1~"},
            ]
        }
        errors = _validator().validate_instance("gts.x.test._.c.v1~", schema)
        assert any("anyOf: no branch matched" in e.reason for e in errors)

    def test_any_of_matches_one_branch(self):
        schema = {
            "anyOf": [
                {X_GTS_TYPE_REF: "gts.x.test._.a.v1~"},
                {X_GTS_TYPE_REF: "gts.x.test._.b.v1~"},
            ]
        }
        errors = _validator().validate_instance("gts.x.test._.a.v1~", schema)
        assert errors == []

    def test_all_of_validates_each_matching_branch(self):
        schema = {"allOf": [{"type": "string", X_GTS_TYPE_REF: "gts.x.test.*"}]}
        errors = _validator().validate_instance("gts.x.other.v1~", schema)
        assert len(errors) == 1
