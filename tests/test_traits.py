"""Tests for gts.traits (OP#13 schema traits validation)."""

from gts._json_pointer import resolve
from gts.traits import (
    build_effective_traits,
    build_effective_traits_schema,
    collect_trait_schema_from_value,
    collect_traits_from_value,
    inline_local_pointers,
    merge_rfc7396_into,
)


class TestCollection:
    def test_collect_trait_schema_from_value_direct(self):
        out = []
        collect_trait_schema_from_value(
            {"x-gts-traits-schema": {"type": "object"}}, out
        )
        assert out == [{"type": "object"}]

    def test_collect_trait_schema_from_value_via_allof(self):
        out = []
        collect_trait_schema_from_value(
            {"allOf": [{"x-gts-traits-schema": {"type": "object"}}]}, out
        )
        assert out == [{"type": "object"}]

    def test_collect_trait_schema_from_value_ignores_non_dict(self):
        out = []
        collect_trait_schema_from_value("not-a-dict", out)
        assert out == []

    def test_collect_traits_from_value_merges_allof(self):
        merged = {}
        collect_traits_from_value(
            {
                "x-gts-traits": {"a": 1},
                "allOf": [{"x-gts-traits": {"b": 2}}],
            },
            merged,
        )
        assert merged == {"b": 2, "a": 1}


class TestJsonPointer:
    def test_decodes_uri_fragment_tokens(self):
        assert resolve({"a b": "value"}, "#/a%20b") == "value"

    def test_rejects_negative_array_index(self):
        assert resolve(["value"], "/-1", default="missing") == "missing"

    def test_rejects_leading_zero_array_index(self):
        assert resolve(["value"], "/01", default="missing") == "missing"


class TestInlineLocalPointers:
    def test_resolves_local_pointer(self):
        root = {"defs": {"foo": {"type": "string"}}}
        fragment = {"$ref": "#/defs/foo"}
        result = inline_local_pointers(fragment, root)
        assert result == {"type": "string"}

    def test_unresolved_pointer_returns_fragment(self):
        root = {}
        fragment = {"$ref": "#/missing"}
        result = inline_local_pointers(fragment, root)
        assert result == fragment

    def test_sibling_keys_merged_with_resolved_ref(self):
        root = {"defs": {"foo": {"type": "string"}}}
        fragment = {"$ref": "#/defs/foo", "minLength": 2}
        result = inline_local_pointers(fragment, root)
        assert result == {"type": "string", "minLength": 2}

    def test_non_ref_dict_recurses(self):
        root = {}
        fragment = {"a": {"b": 1}}
        result = inline_local_pointers(fragment, root)
        assert result == {"a": {"b": 1}}

    def test_list_recursion(self):
        root = {}
        fragment = [{"a": 1}, {"$ref": "#/missing"}]
        result = inline_local_pointers(fragment, root)
        assert result == [{"a": 1}, {"$ref": "#/missing"}]

    def test_array_index_pointer(self):
        root = {"items": [{"type": "string"}, {"type": "integer"}]}
        fragment = {"$ref": "#/items/1"}
        result = inline_local_pointers(fragment, root)
        assert result == {"type": "integer"}

    def test_invalid_array_index_returns_none(self):
        root = {"items": [{"type": "string"}]}
        fragment = {"$ref": "#/items/notanumber"}
        result = inline_local_pointers(fragment, root)
        # unresolved, fragment unchanged
        assert result == fragment


class TestMergeRfc7396:
    def test_merge_overwrites_scalar(self):
        target = {"a": 1}
        merge_rfc7396_into(target, {"a": 2})
        assert target == {"a": 2}

    def test_merge_null_removes_key(self):
        target = {"a": 1, "b": 2}
        merge_rfc7396_into(target, {"a": None})
        assert target == {"b": 2}

    def test_merge_nested_dict(self):
        target = {"a": {"x": 1}}
        merge_rfc7396_into(target, {"a": {"y": 2}})
        assert target == {"a": {"x": 1, "y": 2}}

    def test_merge_replaces_non_dict_with_dict(self):
        target = {"a": 1}
        merge_rfc7396_into(target, {"a": {"y": 2}})
        assert target == {"a": {"y": 2}}


class TestBuildEffectiveTraitsSchema:
    def test_empty_list_returns_empty_dict(self):
        assert build_effective_traits_schema([]) == {}

    def test_single_schema_returned_as_is(self):
        schema = {"type": "object"}
        assert build_effective_traits_schema([schema]) == schema

    def test_multiple_schemas_composed_via_allof(self):
        result = build_effective_traits_schema([{"a": 1}, {"b": 2}])
        assert result["type"] == "object"
        assert result["allOf"] == [{"a": 1}, {"b": 2}]


class TestBuildEffectiveTraits:
    def test_no_schema_no_values(self):
        effective = build_effective_traits([], {}, None)
        assert effective.validate(check_unresolved=True) == []

    def test_values_without_schema_is_error(self):
        effective = build_effective_traits([], {"a": 1}, None)
        errors = effective.validate(check_unresolved=True)
        assert any("no x-gts-traits-schema is defined" in e for e in errors)

    def test_schema_false_prohibits_values(self):
        effective = build_effective_traits([False], {"a": 1}, None)
        errors = effective.validate(check_unresolved=True)
        assert any("values are prohibited" in e for e in errors)

    def test_schema_false_with_no_values_is_ok(self):
        effective = build_effective_traits([False], {}, None)
        assert effective.validate(check_unresolved=True) == []

    def test_default_materialized(self):
        effective = build_effective_traits(
            [{"type": "object", "properties": {"a": {"default": "x"}}}], {}, None
        )
        assert effective.values == {"a": "x"}

    def test_valid_trait_values_pass(self):
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
        }
        effective = build_effective_traits([schema], {"a": "hi"}, None)
        assert effective.validate(check_unresolved=True) == []

    def test_standard_trait_formats_are_enforced(self):
        schema = {
            "type": "object",
            "properties": {
                "email": {"type": "string", "format": "email"},
                "time": {"type": "string", "format": "time"},
            },
        }

        assert (
            build_effective_traits(
                [schema], {"email": "user@example.com", "time": "10:30:00Z"}, None
            ).validate(check_unresolved=True)
            == []
        )
        errors = build_effective_traits(
            [schema], {"email": "not-an-email", "time": "10:30:00Z"}, None
        ).validate(check_unresolved=True)
        assert any("is not a 'email'" in error for error in errors)

    def test_invalid_trait_type_fails(self):
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
        }
        effective = build_effective_traits([schema], {"a": 5}, None)
        errors = effective.validate(check_unresolved=True)
        assert any("trait validation" in e for e in errors)

    def test_required_without_default_unresolved(self):
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
        }
        effective = build_effective_traits([schema], {}, None)
        errors = effective.validate(check_unresolved=True)
        assert any("is not resolved" in e for e in errors)

    def test_abstract_skips_unresolved_check(self):
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
        }
        effective = build_effective_traits([schema], {}, None)
        errors = effective.validate(check_unresolved=False)
        assert errors == []

    def test_incompatible_trait_schema_chain_flagged(self):
        # Second schema narrows type incompatibly with the ancestor.
        effective = build_effective_traits(
            [{"type": "string"}, {"type": "integer"}], {}, None
        )
        errors = effective.validate(check_unresolved=True)
        assert any("incompatible with ancestor trait schema" in e for e in errors)

    def test_invalid_trait_schema_integrity_flagged(self):
        effective = build_effective_traits([{"type": "not-a-real-type"}], {}, None)
        errors = effective.validate(check_unresolved=True)
        assert any("not a valid JSON Schema" in e for e in errors)

    def test_dialect_applied_to_effective_schema(self):
        effective = build_effective_traits(
            [{"type": "object"}], {}, "https://json-schema.org/draft/2020-12/schema"
        )
        assert effective.schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"

    def test_x_gts_ref_errors_prefixed(self):
        schema = {
            "type": "object",
            "properties": {"ref": {"type": "string", "x-gts-ref": "not-valid"}},
        }
        effective = build_effective_traits([schema], {"ref": "also-not-valid"}, None)
        errors = effective.validate(check_unresolved=True)
        assert any(e.startswith("trait x-gts-ref:") for e in errors)
