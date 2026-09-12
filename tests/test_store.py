"""Tests for GtsStore and GtsReader."""

from typing import Iterator, Optional

import pytest
from jsonschema.exceptions import ValidationError

from gts.store import (
    GtsStore,
    GtsReader,
    StoreGtsObjectNotFound,
    StoreGtsSchemaNotFound,
    StoreGtsEntityNotFound,
    StoreGtsSchemaForInstanceNotFound,
    StoreGtsCastFromSchemaNotAllowed,
)
from gts.entities import GtsEntity, DEFAULT_GTS_CONFIG
from gts.gts import GtsID


class MockGtsReader(GtsReader):
    """Mock reader for testing."""

    def __init__(self, entities: list[GtsEntity]):
        self._entities = entities
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
        return None

    def reset(self) -> None:
        self._index = 0


class TestGtsStore:
    """Tests for GtsStore class."""

    def test_store_creation_empty(self):
        """Test creating empty store."""
        reader = MockGtsReader([])
        store = GtsStore(reader)
        assert store.get("gts.any.id~") is None

    def test_store_population_from_reader(self):
        """Test store is populated from reader."""
        gts_id = GtsID("gts.vendor.package.namespace.type.v1~")
        entity = GtsEntity(
            content={"name": "test"},
            gts_id=gts_id,
            is_schema=True,
        )
        reader = MockGtsReader([entity])
        store = GtsStore(reader)

        result = store.get("gts.vendor.package.namespace.type.v1~")
        assert result is not None
        assert result.content == {"name": "test"}

    def test_store_register_entity(self):
        """Test registering an entity directly."""
        reader = MockGtsReader([])
        store = GtsStore(reader)

        gts_id = GtsID("gts.vendor.package.namespace.type.v1~")
        entity = GtsEntity(
            content={"registered": True},
            gts_id=gts_id,
        )
        store.register(entity)

        result = store.get("gts.vendor.package.namespace.type.v1~")
        assert result is not None
        assert result.content["registered"] is True

    def test_store_register_schema(self):
        """Test registering a schema."""
        reader = MockGtsReader([])
        store = GtsStore(reader)

        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        store.register_schema("gts.vendor.package.namespace.type.v1~", schema)

        result = store.get("gts.vendor.package.namespace.type.v1~")
        assert result is not None
        assert result.is_schema is True

    def test_store_register_schema_invalid_id(self):
        """Test registering schema with invalid ID (not ending with ~)."""
        reader = MockGtsReader([])
        store = GtsStore(reader)

        with pytest.raises(ValueError) as exc_info:
            store.register_schema("gts.vendor.package.namespace.type.v1", {})
        assert "must end with '~'" in str(exc_info.value)

    def test_store_get_schema_content(self):
        """Test getting schema content."""
        gts_id = GtsID("gts.vendor.package.namespace.type.v1~")
        entity = GtsEntity(
            content={"type": "object"},
            gts_id=gts_id,
            is_schema=True,
        )
        reader = MockGtsReader([entity])
        store = GtsStore(reader)

        content = store.get_schema_content("gts.vendor.package.namespace.type.v1~")
        assert content == {"type": "object"}

    def test_store_get_schema_content_not_found(self):
        """Test getting non-existent schema content raises KeyError."""
        reader = MockGtsReader([])
        store = GtsStore(reader)

        with pytest.raises(KeyError):
            store.get_schema_content("gts.vendor.package.namespace.nonexistent.v1~")

    def test_store_items(self):
        """Test iterating over store items."""
        entities = [
            GtsEntity(
                content={"name": f"entity{i}"},
                gts_id=GtsID(f"gts.vendor.package.namespace.type{i}.v1~"),
            )
            for i in range(3)
        ]
        reader = MockGtsReader(entities)
        store = GtsStore(reader)

        items = list(store.items())
        assert len(items) == 3


class TestGtsStoreQuery:
    """Tests for GtsStore query functionality."""

    def _create_store_with_entities(self):
        """Helper to create a store with test entities."""
        entities = [
            GtsEntity(
                content={
                    "$id": "gts.vendor.package.namespace.user.v1~vendor.package.namespace.alice.v1",
                    "name": "alice",
                    "status": "active",
                },
                cfg=DEFAULT_GTS_CONFIG,
            ),
            GtsEntity(
                content={
                    "$id": "gts.vendor.package.namespace.user.v1~vendor.package.namespace.bob.v1",
                    "name": "bob",
                    "status": "inactive",
                },
                cfg=DEFAULT_GTS_CONFIG,
            ),
            GtsEntity(
                content={
                    "$id": "gts.vendor.package.namespace.order.v1~vendor.package.namespace.order1.v1",
                    "orderId": "order1",
                    "status": "active",
                },
                cfg=DEFAULT_GTS_CONFIG,
            ),
        ]
        reader = MockGtsReader(entities)
        return GtsStore(reader)

    def test_query_exact_match(self):
        """Test exact match query."""
        store = self._create_store_with_entities()
        result = store.query(
            "gts.vendor.package.namespace.user.v1~vendor.package.namespace.alice.v1"
        )

        assert result.error == ""
        assert result.count == 1
        assert result.results[0]["name"] == "alice"

    def test_query_wildcard_match(self):
        """Test wildcard match query."""
        store = self._create_store_with_entities()
        result = store.query("gts.vendor.package.namespace.user.*")

        assert result.error == ""
        assert result.count == 2
        names = [r["name"] for r in result.results]
        assert "alice" in names
        assert "bob" in names

    def test_query_with_filter(self):
        """Test query with filter."""
        store = self._create_store_with_entities()
        result = store.query("gts.vendor.package.namespace.*[status=active]")

        assert result.error == ""
        assert result.count == 2
        for r in result.results:
            assert r["status"] == "active"

    def test_query_with_limit(self):
        """Test query with limit."""
        store = self._create_store_with_entities()
        result = store.query("gts.vendor.package.namespace.*", limit=1)

        assert result.count == 1
        assert result.limit == 1

    def test_query_no_match(self):
        """Test query with no matches."""
        store = self._create_store_with_entities()
        result = store.query("gts.vendor.other.*")

        assert result.error == ""
        assert result.count == 0

    def test_query_invalid_pattern(self):
        """Test query with invalid pattern."""
        store = self._create_store_with_entities()
        result = store.query("gts.vendor.package.namespace.user.v1~alice*")

        assert result.error != ""
        assert "invalid" in result.error.lower() or "wildcard" in result.error.lower()


class TestGtsStoreValidation:
    """Tests for GtsStore validation methods."""

    def _create_store_with_schema_and_instance(self):
        """Helper to create store with schema and instance."""
        schema = GtsEntity(
            content={
                "$schema": "http://json-schema.org/draft-07/schema#",
                "$id": "gts://gts.vendor.package.namespace.type.v1~",
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            },
            cfg=DEFAULT_GTS_CONFIG,
        )

        instance = GtsEntity(
            content={
                "$id": "gts.vendor.package.namespace.type.v1~vendor.package.namespace.inst.v1",
                "gtsType": "gts.vendor.package.namespace.type.v1~",
                "name": "test",
            },
            cfg=DEFAULT_GTS_CONFIG,
        )

        reader = MockGtsReader([schema, instance])
        return GtsStore(reader)

    def test_validate_schema_valid(self):
        """Test validating a valid schema."""
        store = self._create_store_with_schema_and_instance()
        # Should not raise
        store.validate_schema("gts.vendor.package.namespace.type.v1~")

    def test_validate_schema_not_found(self):
        """Test validating non-existent schema."""
        reader = MockGtsReader([])
        store = GtsStore(reader)

        with pytest.raises(StoreGtsSchemaNotFound):
            store.validate_schema("gts.vendor.package.namespace.nonexistent.v1~")

    def test_validate_schema_not_type_id(self):
        """Test validating with non-type ID."""
        reader = MockGtsReader([])
        store = GtsStore(reader)

        with pytest.raises(ValueError) as exc_info:
            store.validate_schema("gts.vendor.package.namespace.type.v1~instance")
        assert "not a schema" in str(exc_info.value)

    def test_validate_instance_valid(self):
        """Test validating a valid instance."""
        store = self._create_store_with_schema_and_instance()
        # Should not raise
        store.validate_instance(
            "gts.vendor.package.namespace.type.v1~vendor.package.namespace.inst.v1"
        )

    def test_validate_instance_content_enforces_standard_formats(self):
        store = GtsStore(MockGtsReader([]))
        type_id = "gts.vendor.package.namespace.type.v1~"
        store.register_schema(
            type_id,
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "properties": {
                    "uuid": {"type": "string", "format": "uuid"},
                    "time": {"type": "string", "format": "time"},
                },
                "required": ["uuid", "time"],
            },
        )

        # RFC 3339 "time" (draft-07) requires a timezone offset, so a valid
        # value must carry one (e.g. the "Z" UTC designator).
        store.validate_instance_content(
            {"uuid": "550e8400-e29b-41d4-a716-446655440000", "time": "10:30:00Z"},
            type_id,
        )
        with pytest.raises(ValidationError):
            store.validate_instance_content(
                {"uuid": "not-a-uuid", "time": "10:30:00Z"}, type_id
            )
        with pytest.raises(ValidationError):
            store.validate_instance_content(
                {"uuid": "550e8400-e29b-41d4-a716-446655440000", "time": "25:99:99Z"},
                type_id,
            )

    def test_validate_instance_not_found(self):
        """Test validating non-existent instance."""
        reader = MockGtsReader([])
        store = GtsStore(reader)

        with pytest.raises(StoreGtsObjectNotFound):
            store.validate_instance(
                "gts.vendor.package.namespace.type.v1~vendor.package.namespace.nonexistent.v1"
            )


class TestGtsStoreBuildGraph:
    """Tests for build_schema_graph method."""

    def test_build_graph_simple(self):
        """Test building a simple graph."""
        schema = GtsEntity(
            content={
                "$id": "gts://gts.vendor.package.namespace.type.v1~",
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
            },
            cfg=DEFAULT_GTS_CONFIG,
        )

        reader = MockGtsReader([schema])
        store = GtsStore(reader)

        graph = store.build_schema_graph("gts.vendor.package.namespace.type.v1~")
        assert graph["id"] == "gts.vendor.package.namespace.type.v1~"

    def test_build_graph_not_found(self):
        """Test building graph for non-existent entity."""
        reader = MockGtsReader([])
        store = GtsStore(reader)

        graph = store.build_schema_graph("gts.vendor.package.namespace.nonexistent.v1~")
        assert "errors" in graph
        assert "Entity not found" in graph["errors"]


class TestStoreExceptions:
    """Tests for store exception classes."""

    def test_store_gts_object_not_found(self):
        """Test StoreGtsObjectNotFound exception."""
        exc = StoreGtsObjectNotFound("gts.test~")
        assert "gts.test~" in str(exc)
        assert exc.entity_id == "gts.test~"

    def test_store_gts_schema_not_found(self):
        """Test StoreGtsSchemaNotFound exception."""
        exc = StoreGtsSchemaNotFound("gts.test~")
        assert "gts.test~" in str(exc)
        assert exc.entity_id == "gts.test~"

    def test_store_gts_entity_not_found(self):
        """Test StoreGtsEntityNotFound exception."""
        exc = StoreGtsEntityNotFound("gts.test~")
        assert "gts.test~" in str(exc)
        assert exc.entity_id == "gts.test~"

    def test_store_gts_schema_for_instance_not_found(self):
        """Test StoreGtsSchemaForInstanceNotFound exception."""
        exc = StoreGtsSchemaForInstanceNotFound("gts.test~instance")
        assert "gts.test~instance" in str(exc)

    def test_store_gts_cast_from_schema_not_allowed(self):
        """Test StoreGtsCastFromSchemaNotAllowed exception."""
        exc = StoreGtsCastFromSchemaNotAllowed("gts.test~")
        assert "gts.test~" in str(exc)
        assert "instance" in str(exc).lower()
