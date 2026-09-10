"""Tests for gts._server (FastAPI route handlers), called directly as async
coroutines via asyncio.run to avoid pulling in an HTTP test client dependency.
"""

import asyncio

import pytest

from gts.ops import GtsOps
from gts._server import GtsHttpServer, ValidateEntityRequest, _RequestLoggingMiddleware


SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "$id": "gts://gts.x.test._.foo.v1~",
    "type": "object",
    "properties": {"name": {"type": "string"}},
}

INSTANCE = {
    "$id": "gts.x.test._.foo.v1~x.test._.inst.v1",
    "type": "gts.x.test._.foo.v1~",
    "name": "hi",
}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def server():
    ops = GtsOps(path=None)
    return GtsHttpServer(ops=ops)


class TestServerConstruction:
    def test_app_and_routes_registered(self, server):
        assert server.app is not None
        assert server.base_url == "http://127.0.0.1:8000"
        paths = {route.path for route in server.app.routes}
        assert "/entities" in paths
        assert "/query" in paths


class TestValidateEntityRequestModel:
    def test_requires_id(self):
        with pytest.raises(Exception):
            ValidateEntityRequest()

    def test_mismatched_ids_raise(self):
        with pytest.raises(Exception):
            ValidateEntityRequest(entity_id="a", gts_id="b")

    def test_entity_id_used(self):
        req = ValidateEntityRequest(entity_id="gts.x.test._.foo.v1~")
        assert req.resolved_id == "gts.x.test._.foo.v1~"

    def test_gts_id_used_when_entity_id_absent(self):
        req = ValidateEntityRequest(gts_id="gts.x.test._.foo.v1~")
        assert req.resolved_id == "gts.x.test._.foo.v1~"

    def test_matching_ids_ok(self):
        req = ValidateEntityRequest(entity_id="a", gts_id="a")
        assert req.resolved_id == "a"


class TestHandlers:
    def test_add_entity_success(self, server):
        resp = run(server.add_entity(body=SCHEMA, validate=False))
        assert resp.status_code == 200

    def test_add_entity_failure(self, server):
        resp = run(server.add_entity(body={"no": "id"}, validate=False))
        assert resp.status_code == 422

    def test_add_entities(self, server):
        resp = run(server.add_entities(body=[SCHEMA, INSTANCE]))
        assert resp.status_code == 200

    def test_add_schema(self, server):
        from gts._server import SchemaRegister

        body = SchemaRegister(type_id="gts.x.test._.bar.v1~", type_schema={"type": "object"})
        resp = run(server.add_schema(body))
        assert resp.status_code == 200

    def test_validate_id(self, server):
        result = run(server.validate_id(id="gts.x.test._.foo.v1~"))
        assert result["valid"] is True

    def test_extract_id(self, server):
        result = run(server.extract_id(body=SCHEMA))
        assert result["is_type_schema"] is True

    def test_parse(self, server):
        result = run(server.parse(id="gts.x.test._.foo.v1~"))
        assert result["ok"] is True

    def test_match_id_pattern(self, server):
        result = run(
            server.match_id_pattern(candidate="gts.x.test._.foo.v1~", pattern="gts.x.test.*")
        )
        assert result["match"] is True

    def test_id_to_uuid(self, server):
        result = run(server.id_to_uuid(id="gts.x.test._.foo.v1~"))
        assert "uuid" in result

    def test_validate_instance(self, server):
        from gts._server import ValidateInstanceRequest

        run(server.add_entity(body=SCHEMA, validate=False))
        run(server.add_entity(body=INSTANCE, validate=False))
        result = run(
            server.validate_instance(ValidateInstanceRequest(instance_id=INSTANCE["$id"]))
        )
        assert result["ok"] is True

    def test_validate_type_schema(self, server):
        from gts._server import ValidateTypeSchemaRequest

        run(server.add_entity(body=SCHEMA, validate=False))
        result = run(
            server.validate_type_schema(
                ValidateTypeSchemaRequest(type_id="gts.x.test._.foo.v1~")
            )
        )
        assert result["ok"] is True

    def test_validate_entity(self, server):
        run(server.add_entity(body=SCHEMA, validate=False))
        result = run(
            server.validate_entity(ValidateEntityRequest(entity_id="gts.x.test._.foo.v1~"))
        )
        assert result["ok"] is True

    def test_schema_graph(self, server):
        run(server.add_entity(body=SCHEMA, validate=False))
        result = run(server.schema_graph(id="gts.x.test._.foo.v1~"))
        assert result["id"] == "gts.x.test._.foo.v1~"

    def test_compatibility(self, server):
        run(server.add_entity(body=SCHEMA, validate=False))
        result = run(
            server.compatibility(
                old="gts.x.test._.foo.v1~", new="gts.x.test._.foo.v1~"
            )
        )
        assert result["is_fully_compatible"] is True

    def test_cast(self, server):
        from gts._server import CastRequest

        run(server.add_entity(body=SCHEMA, validate=False))
        run(server.add_entity(body=INSTANCE, validate=False))
        result = run(
            server.cast(
                CastRequest(instance_id=INSTANCE["$id"], to_type_id="gts.x.test._.foo.v1~")
            )
        )
        assert "error" not in result

    def test_query(self, server):
        run(server.add_entity(body=SCHEMA, validate=False))
        run(server.add_entity(body=INSTANCE, validate=False))
        result = run(server.query(expr="gts.x.test._.foo.v1~*", limit=10))
        assert result["count"] >= 1

    def test_attr(self, server):
        run(server.add_entity(body=SCHEMA, validate=False))
        run(server.add_entity(body=INSTANCE, validate=False))
        result = run(server.attr(gts_with_path=f"{INSTANCE['$id']}@name"))
        assert result["value"] == "hi"

    def test_get_entity(self, server):
        run(server.add_entity(body=SCHEMA, validate=False))
        result = run(server.get_entity(gts_id="gts.x.test._.foo.v1~"))
        assert result["ok"] is True

    def test_get_entities(self, server):
        run(server.add_entity(body=SCHEMA, validate=False))
        run(server.add_entity(body=INSTANCE, validate=False))
        result = run(server.get_entities(limit=10))
        assert result["total"] == 2


class TestRequestLoggingMiddlewareVerboseOff:
    def test_dispatch_skips_when_not_verbose(self, server):
        middleware = _RequestLoggingMiddleware(server.app, verbose=0)

        async def call_next(request):
            return "response-sentinel"

        result = run(middleware.dispatch(request=None, call_next=call_next))
        assert result == "response-sentinel"
