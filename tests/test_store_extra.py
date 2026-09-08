"""Additional coverage-focused tests for gts.store.GtsStore."""

import pytest
from typing import Iterator, Optional

from gts.store import GtsStore, GtsReader, StoreGtsEntityNotFound, StoreGtsObjectNotFound
from gts.entities import GtsEntity, DEFAULT_GTS_CONFIG
from gts.gts import GtsID


class MockGtsReader(GtsReader):
    def __init__(self, entities, extra_by_id=None):
        self._entities = entities
        self._extra_by_id = extra_by_id or {}
        self._index = 0

    def __iter__(self) -> Iterator[GtsEntity]:
        self._index = 0
        return self

    def __next__(self) -> GtsEntity:
        if self._index >= len(self._entities):
            raise StopIteration
        entity = self._entities[self._index]
        self._index += 1
        return entity

    def read_by_id(self, entity_id: str) -> Optional[GtsEntity]:
        for entity in self._entities:
            if entity.gts_id and entity.gts_id.id == entity_id:
                return entity
        return self._extra_by_id.get(entity_id)

    def reset(self) -> None:
        self._index = 0


def _schema_entity(gts_id: str, content_extra=None):
    content = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "$id": gts_id,
        "type": "object",
        "properties": {"name": {"type": "string"}},
    }
    if content_extra:
        content.update(content_extra)
    return GtsEntity(content=content, gts_id=GtsID(gts_id), is_schema=True)


class TestRegisterEdgeCases:
    def test_register_raises_without_id(self):
        store = GtsStore(reader=None)
        entity = GtsEntity(content={"a": 1})
        with pytest.raises(ValueError):
            store.register(entity)

    def test_get_falls_back_to_reader_not_in_initial_iter(self):
        schema = _schema_entity("gts.x.test._.foo.v1~")
        extra = _schema_entity("gts.x.test._.bar.v1~")
        reader = MockGtsReader([schema], extra_by_id={"gts.x.test._.bar.v1~": extra})
        store = GtsStore(reader)
        result = store.get("gts.x.test._.bar.v1~")
        assert result is not None
        assert result.content["$id"] == "gts.x.test._.bar.v1~"

    def test_get_returns_none_when_reader_absent_and_missing(self):
        store = GtsStore(reader=None)
        assert store.get("gts.x.test._.missing.v1~") is None

    def test_unregister_missing_id_noop(self):
        store = GtsStore(reader=None)
        store.unregister("gts.x.test._.missing.v1~")  # should not raise


class TestValidateSchemaRefs:
    def test_local_ref_valid(self):
        GtsStore._validate_schema_refs({"$ref": "#/defs/foo"})

    def test_gts_ref_valid(self):
        GtsStore._validate_schema_refs({"$ref": "gts://gts.x.test._.foo.v1~"})

    def test_gts_ref_invalid_id_raises(self):
        with pytest.raises(ValueError, match="invalid GTS identifier"):
            GtsStore._validate_schema_refs({"$ref": "gts://not a valid id"})

    def test_other_ref_raises(self):
        with pytest.raises(ValueError, match="must be a local ref"):
            GtsStore._validate_schema_refs({"$ref": "http://example.com/schema"})

    def test_recurses_into_list(self):
        with pytest.raises(ValueError):
            GtsStore._validate_schema_refs(
                {"allOf": [{"$ref": "http://example.com/schema"}]}
            )


class TestValidateGtsKeywords:
    def test_final_must_be_bool(self):
        with pytest.raises(ValueError, match="x-gts-final must be a boolean"):
            GtsStore._validate_gts_keywords({"x-gts-final": "yes"})

    def test_abstract_must_be_bool(self):
        with pytest.raises(ValueError, match="x-gts-abstract must be a boolean"):
            GtsStore._validate_gts_keywords({"x-gts-abstract": "yes"})

    def test_mutual_exclusion(self):
        with pytest.raises(ValueError, match="cannot declare both"):
            GtsStore._validate_gts_keywords(
                {"x-gts-final": True, "x-gts-abstract": True}
            )

    def test_nested_keyword_placement_raises(self):
        with pytest.raises(ValueError, match="must be at the schema top level"):
            GtsStore._validate_gts_keywords(
                {"properties": {"a": {"x-gts-final": True}}}
            )

    def test_valid_top_level_keywords_pass(self):
        GtsStore._validate_gts_keywords({"x-gts-final": True})
        GtsStore._validate_gts_keywords({"x-gts-abstract": True})

    def test_content_is_abstract_and_final(self):
        assert GtsStore._content_is_abstract({"x-gts-abstract": True}) is True
        assert GtsStore._content_is_abstract({}) is False
        assert GtsStore._content_is_final({"x-gts-final": True}) is True
        assert GtsStore._content_is_final({}) is False


class TestValidateSchemaXGtsRefs:
    def test_non_schema_id_raises(self):
        store = GtsStore(reader=None)
        with pytest.raises(ValueError, match="not a schema"):
            store._validate_schema_x_gts_refs("gts.x.test._.foo.v1")

    def test_missing_schema_raises(self):
        store = GtsStore(reader=None)
        from gts.store import StoreGtsSchemaNotFound

        with pytest.raises(StoreGtsSchemaNotFound):
            store._validate_schema_x_gts_refs("gts.x.test._.missing.v1~")

    def test_entity_not_schema_raises(self):
        entity = GtsEntity(
            content={"a": 1}, gts_id=GtsID("gts.x.test._.foo.v1~"), is_schema=False
        )
        store = GtsStore(reader=None)
        store.register(entity)
        with pytest.raises(ValueError, match="is not a schema"):
            store._validate_schema_x_gts_refs("gts.x.test._.foo.v1~")

    def test_invalid_x_gts_ref_raises(self):
        schema = _schema_entity(
            "gts.x.test._.foo.v1~", {"x-gts-ref": "notgts.*"}
        )
        store = GtsStore(reader=None)
        store.register(schema)
        with pytest.raises(Exception, match="x-gts-ref validation failed"):
            store._validate_schema_x_gts_refs("gts.x.test._.foo.v1~")


class TestValidateSchemaChain:
    def test_single_segment_no_parent_ok(self):
        store = GtsStore(reader=None)
        store._validate_schema_chain("gts.x.test._.foo.v1~")

    def test_final_base_blocks_derivation(self):
        base = _schema_entity("gts.x.test._.base.v1~", {"x-gts-final": True})
        derived = _schema_entity("gts.x.test._.base.v1~x.test._.derived.v1~")
        store = GtsStore(reader=None)
        store.register(base)
        store.register(derived)
        with pytest.raises(ValueError, match="is final"):
            store._validate_schema_chain("gts.x.test._.base.v1~x.test._.derived.v1~")

    def test_missing_base_schema_raises(self):
        derived = _schema_entity("gts.x.test._.base.v1~x.test._.derived.v1~")
        store = GtsStore(reader=None)
        store.register(derived)
        with pytest.raises(ValueError, match="not found for chain validation"):
            store._validate_schema_chain("gts.x.test._.base.v1~x.test._.derived.v1~")

    def test_incompatible_derivation_raises(self):
        base = _schema_entity(
            "gts.x.test._.base.v1~",
            {"properties": {"name": {"type": "string"}, "a": {"type": "string"}}},
        )
        derived = _schema_entity(
            "gts.x.test._.base.v1~x.test._.derived.v1~",
            {"properties": {"a": {"type": "integer"}}},
        )
        store = GtsStore(reader=None)
        store.register(base)
        store.register(derived)
        with pytest.raises(ValueError, match="is not compatible with base"):
            store._validate_schema_chain("gts.x.test._.base.v1~x.test._.derived.v1~")


class TestResolveSchemaRefsAndInline:
    def test_resolves_gts_ref(self):
        target = _schema_entity("gts.x.test._.target.v1~")
        store = GtsStore(reader=None)
        store.register(target)
        schema = {"$ref": "gts://gts.x.test._.target.v1~"}
        resolved = store._resolve_schema_refs(schema)
        assert resolved["type"] == "object"

    def test_unresolvable_ref_left_unresolved(self):
        store = GtsStore(reader=None)
        schema = {"$ref": "gts://gts.x.test._.missing.v1~"}
        resolved = store._resolve_schema_refs(schema)
        assert resolved == schema

    def test_cyclic_ref_left_unresolved(self):
        a = _schema_entity(
            "gts.x.test._.a.v1~", {"$ref": "gts://gts.x.test._.b.v1~"}
        )
        b = _schema_entity(
            "gts.x.test._.b.v1~", {"$ref": "gts://gts.x.test._.a.v1~"}
        )
        store = GtsStore(reader=None)
        store.register(a)
        store.register(b)
        resolved = store._resolve_schema_refs(a.content)
        assert "$ref" in str(resolved)

    def test_ref_with_siblings_creates_allof(self):
        target = _schema_entity("gts.x.test._.target.v1~")
        store = GtsStore(reader=None)
        store.register(target)
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": "gts://gts.x.test._.target.v1~",
            "title": "sibling",
        }
        resolved = store._resolve_schema_refs(schema)
        assert "allOf" in resolved

    def test_supports_ref_siblings_false_for_non_dict(self):
        assert GtsStore._supports_ref_siblings("nope") is False

    def test_inline_refs_list_recursion(self):
        store = GtsStore(reader=None)
        node = [{"a": 1}, {"b": 2}]
        result = store._inline_refs(node, set(), False)
        assert result == node

    def test_inline_refs_scalar_passthrough(self):
        store = GtsStore(reader=None)
        assert store._inline_refs(5, set(), False) == 5


class TestCastAndCompatibility:
    def _build_store(self):
        old_schema = _schema_entity("gts.x.test._.foo.v1.0~")
        new_schema = _schema_entity(
            "gts.x.test._.foo.v1.5~",
            {"properties": {"name": {"type": "string"}, "extra": {"type": "string", "default": "d"}}},
        )
        instance = GtsEntity(
            content={
                "$id": "gts.x.test._.foo.v1.0~x.test._.inst.v1.0",
                "gtsType": "gts.x.test._.foo.v1.0~",
                "name": "hi",
            },
            cfg=DEFAULT_GTS_CONFIG,
        )
        store = GtsStore(reader=None)
        store.register(old_schema)
        store.register(new_schema)
        store.register(instance)
        return store, old_schema, new_schema, instance

    def test_cast_success(self):
        store, old_schema, new_schema, instance = self._build_store()
        result = store.cast(instance.raw_id, "gts.x.test._.foo.v1.5~")
        assert result.casted_entity is not None
        assert result.casted_entity["extra"] == "d"

    def test_cast_from_missing_entity_raises(self):
        store, *_ = self._build_store()
        with pytest.raises(StoreGtsEntityNotFound):
            store.cast("gts.x.test._.foo.v1.0~x.test._.missing.v1.0", "gts.x.test._.foo.v1.5~")

    def test_cast_from_schema_raises(self):
        store, old_schema, new_schema, instance = self._build_store()
        from gts.store import StoreGtsCastFromSchemaNotAllowed

        with pytest.raises(StoreGtsCastFromSchemaNotAllowed):
            store.cast(old_schema.gts_id.id, new_schema.gts_id.id)

    def test_cast_to_missing_schema_raises(self):
        store, old_schema, new_schema, instance = self._build_store()
        with pytest.raises(StoreGtsObjectNotFound):
            store.cast(instance.raw_id, "gts.x.test._.missing.v1~")

    def test_is_minor_compatible_missing_entity(self):
        store = GtsStore(reader=None)
        result = store.is_minor_compatible("gts.x.test._.a.v1~", "gts.x.test._.b.v1~")
        assert result.is_fully_compatible is False
        assert "Schema not found" in result.incompatibility_reasons

    def test_is_minor_compatible_valid(self):
        store, old_schema, new_schema, instance = self._build_store()
        result = store.is_minor_compatible(old_schema.gts_id.id, new_schema.gts_id.id)
        assert result.backward_verdict is not None


class TestBuildSchemaGraphWithRefs:
    def test_graph_includes_refs_and_type_id(self):
        schema = _schema_entity("gts.x.test._.foo.v1~")
        instance = GtsEntity(
            content={
                "$id": "gts.x.test._.foo.v1~x.test._.inst.v1",
                "gtsType": "gts.x.test._.foo.v1~",
                "name": "hi",
            },
            cfg=DEFAULT_GTS_CONFIG,
        )
        store = GtsStore(reader=None)
        store.register(schema)
        store.register(instance)
        graph = store.build_schema_graph(instance.gts_id.id)
        assert graph["id"] == instance.gts_id.id
        assert "type_id" in graph

    def test_graph_skips_json_schema_org_refs(self):
        schema = _schema_entity("gts.x.test._.foo.v1~")
        store = GtsStore(reader=None)
        store.register(schema)
        graph = store.build_schema_graph("gts.x.test._.foo.v1~")
        assert "refs" not in graph or "http://json-schema.org" not in str(graph)


class TestQueryEdgeCases:
    def test_query_filter_wildcard_value(self):
        entity = GtsEntity(
            content={
                "$id": "gts.x.test._.foo.v1~x.test._.a.v1",
                "status": "active",
            },
            cfg=DEFAULT_GTS_CONFIG,
        )
        store = GtsStore(reader=None)
        store.register(entity)
        result = store.query("gts.x.test._.foo.*[status=*]")
        assert result.count == 1

    def test_query_filter_wildcard_value_excludes_empty(self):
        entity = GtsEntity(
            content={
                "$id": "gts.x.test._.foo.v1~x.test._.a.v1",
            },
            cfg=DEFAULT_GTS_CONFIG,
        )
        store = GtsStore(reader=None)
        store.register(entity)
        result = store.query("gts.x.test._.foo.*[status=*]")
        assert result.count == 0

    def test_parse_query_filters_empty_string(self):
        store = GtsStore(reader=None)
        assert store._parse_query_filters("") == {}

    def test_query_result_to_dict_error(self):
        from gts.store import GtsStoreQueryResult

        r = GtsStoreQueryResult()
        r.error = "bad"
        r.count = 0
        r.limit = 10
        assert r.to_dict() == {"error": "bad", "count": 0, "limit": 10}

    def test_query_result_to_dict_ok(self):
        from gts.store import GtsStoreQueryResult

        r = GtsStoreQueryResult()
        r.results = [{"a": 1}]
        r.count = 1
        d = r.to_dict()
        assert d["results"] == [{"a": 1}]


class TestValidateSchemaFullFlow:
    def test_meta_schema_url_rejects_gts_id(self):
        schema = _schema_entity(
            "gts.x.test._.foo.v1~", {"$schema": "gts.x.other.v1~"}
        )
        store = GtsStore(reader=None)
        store.register(schema)
        with pytest.raises(ValueError, match="must be a standard JSON Schema URL"):
            store.validate_schema("gts.x.test._.foo.v1~")

    def test_validate_schema_basic_success(self):
        schema = _schema_entity("gts.x.test._.foo.v1~")
        store = GtsStore(reader=None)
        store.register(schema)
        store.validate_schema_basic("gts.x.test._.foo.v1~")

    def test_validate_schema_with_traits_error(self):
        schema = _schema_entity(
            "gts.x.test._.foo.v1~",
            {
                "x-gts-traits-schema": {
                    "type": "object",
                    "properties": {"a": {"type": "string"}},
                    "required": ["a"],
                }
            },
        )
        store = GtsStore(reader=None)
        store.register(schema)
        with pytest.raises(ValueError, match="trait validation failed"):
            store.validate_schema("gts.x.test._.foo.v1~")

    def test_validate_instance_abstract_type_rejected(self):
        schema = _schema_entity(
            "gts.x.test._.foo.v1~", {"x-gts-abstract": True}
        )
        instance = GtsEntity(
            content={
                "$id": "gts.x.test._.foo.v1~x.test._.inst.v1",
                "gtsType": "gts.x.test._.foo.v1~",
                "name": "hi",
            },
            cfg=DEFAULT_GTS_CONFIG,
        )
        store = GtsStore(reader=None)
        store.register(schema)
        store.register(instance)
        with pytest.raises(ValueError, match="is abstract"):
            store.validate_instance(instance.gts_id.id)

    def test_validate_instance_by_uuid(self):
        schema = _schema_entity("gts.x.test._.foo.v1~")
        store = GtsStore(reader=None)
        store.register(schema)
        entity = GtsEntity(
            content={
                "type": "gts.x.test._.foo.v1~",
                "id": "12345678-1234-5678-1234-567812345678",
                "name": "hi",
            },
            cfg=DEFAULT_GTS_CONFIG,
        )
        store.register(entity)
        store.validate_instance(entity.raw_id)

    def test_validate_instance_invalid_non_uuid_non_gts_raises(self):
        store = GtsStore(reader=None)
        with pytest.raises(StoreGtsObjectNotFound):
            store.validate_instance("totally-not-valid")
