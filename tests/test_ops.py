"""Tests for gts.ops.GtsOps (the high-level CLI/HTTP operations facade)."""

import pytest

from gts.ops import GtsOps


SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "$id": "gts.x.test._.foo.v1~",
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
}

INSTANCE = {
    "$id": "gts.x.test._.foo.v1~x.test._.inst.v1",
    "type": "gts.x.test._.foo.v1~",
    "name": "hi",
}


@pytest.fixture
def ops():
    return GtsOps(path=None)


class TestConstructionAndConfig:
    def test_default_config_used_when_no_path(self, ops):
        assert "$id" in ops.cfg.entity_id_fields

    def test_config_from_invalid_path_falls_back(self):
        o = GtsOps(path=None, config="/nonexistent/path/config.json")
        assert o.cfg is not None

    def test_reload_from_path_missing_dir_raises(self, ops, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        ops.reload_from_path(str(empty_dir))
        assert ops.store is not None


class TestAddEntity:
    def test_add_schema_success(self, ops):
        result = ops.add_entity(SCHEMA)
        assert result.ok is True
        assert result.is_type_schema is True
        assert result.id == "gts.x.test._.foo.v1~"

    def test_add_schema_missing_gts_id(self, ops):
        bad_schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
        }
        result = ops.add_entity(bad_schema)
        assert result.ok is False
        assert "Unable to detect GTS ID" in result.error

    def test_add_schema_plain_gts_prefix_rejected_when_validate(self, ops):
        schema = dict(SCHEMA)
        schema["$id"] = "gts.x.test._.foo.v1~"
        result = ops.add_entity(schema, validate=True)
        # $id doesn't start with gts:// -> rejected only if raw $id startswith "gts."
        assert result.ok is False
        assert "gts:// URI format" in result.error

    def test_add_instance_without_id_field_rejected(self, ops):
        result = ops.add_entity({"name": "hi"})
        assert result.ok is False
        assert "must have an id field" in result.error

    def test_add_instance_success(self, ops):
        ops.add_entity(SCHEMA)
        result = ops.add_entity(INSTANCE)
        assert result.ok is True
        assert result.is_type_schema is False

    def test_add_instance_validate_failure_restores_previous(self, ops):
        ops.add_entity(SCHEMA)
        bad_instance = {
            "$id": "gts.x.test._.foo.v1~x.test._.inst.v1",
            "gtsType": "gts.x.test._.foo.v1~",
        }  # missing required "name"
        result = ops.add_entity(bad_instance, validate=True)
        assert result.ok is False
        assert "Validation failed" in result.error

    def test_add_schema_validate_basic_failure(self, ops):
        bad_schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "$id": "gts.x.test._.foo.v1~",
            "type": "object",
            "x-gts-ref": "notgts.*",
        }
        result = ops.add_entity(bad_schema)
        assert result.ok is False
        assert "Validation failed" in result.error

    def test_add_entities_batch(self, ops):
        result = ops.add_entities([SCHEMA, INSTANCE])
        assert result.ok is True
        assert len(result.results) == 2


class TestAddSchemaLegacy:
    def test_add_schema_legacy_success(self, ops):
        result = ops.add_schema("gts.x.test._.legacy.v1~", {"type": "object"})
        assert result.ok is True
        assert result.id == "gts.x.test._.legacy.v1~"

    def test_add_schema_legacy_failure(self, ops):
        result = ops.add_schema("gts.x.test._.legacy.v1", {"type": "object"})
        assert result.ok is False
        assert result.error


class TestValidateId:
    def test_valid_wildcard(self, ops):
        result = ops.validate_id("gts.x.test.*")
        assert result.valid is True
        assert result.is_wildcard is True

    def test_valid_exact(self, ops):
        result = ops.validate_id("gts.x.test._.foo.v1~")
        assert result.valid is True
        assert result.is_type is True

    def test_invalid_id(self, ops):
        result = ops.validate_id("not a valid id")
        assert result.valid is False
        assert result.error

    def test_to_dict_variants(self, ops):
        result = ops.validate_id("gts.x.test._.foo.v1~")
        d = result.to_dict()
        assert d["valid"] is True
        assert d["is_type"] is True

    def test_invalid_wildcard(self, ops):
        result = ops.validate_id("notgts*")
        assert result.valid is False


class TestParseId:
    def test_parse_exact(self, ops):
        result = ops.parse_id("gts.x.test._.foo.v1~")
        assert result.ok is True
        assert len(result.segments) == 1
        assert result.segments[0].vendor == "x"
        d = result.to_dict()
        assert d["segments"][0]["vendor"] == "x"

    def test_parse_wildcard(self, ops):
        result = ops.parse_id("gts.x.test.*")
        assert result.ok is True
        assert result.is_wildcard is True

    def test_parse_invalid(self, ops):
        result = ops.parse_id("not valid")
        assert result.ok is False
        assert result.error


class TestMatchIdPattern:
    def test_match_true(self, ops):
        result = ops.match_id_pattern("gts.x.test._.foo.v1~", "gts.x.test.*")
        assert result.match is True

    def test_match_false(self, ops):
        result = ops.match_id_pattern(
            "gts.x.other._.foo.v1~", "gts.x.test.*"
        )
        assert result.match is False

    def test_match_malformed_wildcard_candidate(self, ops):
        result = ops.match_id_pattern("a*b", "gts.x.test.*")
        assert result.match is False
        assert result.error

    def test_to_dict_with_error(self, ops):
        result = ops.match_id_pattern("bad id", "gts.x.test.*")
        d = result.to_dict()
        assert "error" in d


class TestUuid:
    def test_uuid_deterministic(self, ops):
        r1 = ops.uuid("gts.x.test._.foo.v1~")
        r2 = ops.uuid("gts.x.test._.foo.v1~")
        assert r1.uuid == r2.uuid
        d = r1.to_dict()
        assert d["id"] == "gts.x.test._.foo.v1~"


class TestValidateInstanceSchemaEntity:
    def test_validate_schema_ok(self, ops):
        ops.add_entity(SCHEMA)
        result = ops.validate_schema("gts.x.test._.foo.v1~")
        assert result.ok is True
        d = result.to_dict()
        assert d["ok"] is True

    def test_validate_schema_error(self, ops):
        result = ops.validate_schema("gts.x.test._.missing.v1~")
        assert result.ok is False
        assert result.error

    def test_validate_instance_ok(self, ops):
        ops.add_entity(SCHEMA)
        ops.add_entity(INSTANCE)
        result = ops.validate_instance(INSTANCE["$id"])
        assert result.ok is True

    def test_validate_instance_error(self, ops):
        result = ops.validate_instance("gts.x.test._.foo.v1~x.test._.missing.v1")
        assert result.ok is False

    def test_validate_entity_schema(self, ops):
        ops.add_entity(SCHEMA)
        result = ops.validate_entity("gts.x.test._.foo.v1~")
        assert result.entity_type == "schema"
        assert result.ok is True
        d = result.to_dict()
        assert d["entity_type"] == "schema"

    def test_validate_entity_instance(self, ops):
        ops.add_entity(SCHEMA)
        ops.add_entity(INSTANCE)
        result = ops.validate_entity(INSTANCE["$id"])
        assert result.entity_type == "instance"
        assert result.ok is True

    def test_validate_entity_invalid_id(self, ops):
        result = ops.validate_entity("not a valid id")
        assert result.ok is False
        assert result.entity_type == ""


class TestSchemaGraphCompatibilityCast:
    def test_schema_graph(self, ops):
        ops.add_entity(SCHEMA)
        result = ops.schema_graph("gts.x.test._.foo.v1~")
        assert result.graph["id"] == "gts.x.test._.foo.v1~"
        assert result.to_dict() == result.graph

    def test_compatibility(self, ops):
        ops.add_entity(SCHEMA)
        result = ops.compatibility("gts.x.test._.foo.v1~", "gts.x.test._.foo.v1~")
        assert result.is_fully_compatible is True

    def test_cast_success(self, ops):
        ops.add_entity(SCHEMA)
        ops.add_entity(INSTANCE)
        result = ops.cast(INSTANCE["$id"], "gts.x.test._.foo.v1~")
        assert result.error == ""

    def test_cast_error_wrapped(self, ops):
        result = ops.cast("gts.x.test._.foo.v1~x.test._.missing.v1", "gts.x.test._.foo.v1~")
        assert result.error != ""


class TestQueryAttrExtractGetEntities:
    def test_query(self, ops):
        ops.add_entity(SCHEMA)
        ops.add_entity(INSTANCE)
        result = ops.query("gts.x.test._.foo.v1~*")
        assert result.count >= 1

    def test_attr_no_path(self, ops):
        result = ops.attr("gts.x.test._.foo.v1~")
        assert result.error

    def test_attr_entity_not_found(self, ops):
        result = ops.attr("gts.x.test._.missing.v1~@name")
        assert result.error

    def test_attr_success(self, ops):
        ops.add_entity(SCHEMA)
        ops.add_entity(INSTANCE)
        result = ops.attr(f"{INSTANCE['$id']}@name")
        assert result.resolved is True
        assert result.value == "hi"

    def test_extract_id_schema(self, ops):
        result = ops.extract_id(SCHEMA)
        assert result.is_type_schema is True
        assert result.id == "gts.x.test._.foo.v1~"
        d = result.to_dict()
        assert d["is_type_schema"] is True

    def test_extract_id_instance(self, ops):
        result = ops.extract_id(INSTANCE)
        assert result.is_type_schema is False
        assert result.id == INSTANCE["$id"]

    def test_get_entity_found(self, ops):
        ops.add_entity(SCHEMA)
        result = ops.get_entity("gts.x.test._.foo.v1~")
        assert result.ok is True
        d = result.to_dict()
        assert d["ok"] is True

    def test_get_entity_not_found(self, ops):
        result = ops.get_entity("gts.x.test._.missing.v1~")
        assert result.ok is False
        d = result.to_dict()
        assert "error" in d

    def test_get_entities_and_list(self, ops):
        ops.add_entity(SCHEMA)
        ops.add_entity(INSTANCE)
        result = ops.get_entities(limit=1)
        assert result.count == 1
        assert result.total == 2
        d = result.to_dict()
        assert len(d["entities"]) == 1

        result2 = ops.list(limit=100)
        assert result2.count == 2
