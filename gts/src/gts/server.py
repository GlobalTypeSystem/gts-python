from __future__ import annotations

from typing import Any, Dict, List

from fastapi import FastAPI, Body, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
import time
import logging

from .ops import GtsOps


class _RequestLoggingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, verbose: int) -> None:
        super().__init__(app)
        self.verbose = verbose

    async def dispatch(self, request, call_next):
        if not self.verbose:
            return await call_next(request)
        start = time.time()
        response = await call_next(request)
        dur = (time.time() - start) * 1000.0
        logging.info(
            f"{request.method} {request.url.path} -> {response.status_code} "
            f"in {dur:.1f}ms"
        )
        return response


class SchemaRegister(BaseModel):
    type_id: str
    schema_content: Dict[str, Any] = Field(..., alias="schema")


class CastRequest(BaseModel):
    instance_id: str
    to_schema_id: str


class ValidateInstanceRequest(BaseModel):
    instance_id: str


class GtsHttpServer:
    def __init__(
        self,
        *,
        ops: GtsOps,
        host: str = "127.0.0.1",
        port: int = 8000,
    ) -> None:
        self.ops = ops
        self.host = host
        self.port = port
        self.base_url = f"http://{self.host}:{self.port}"
        self.app = FastAPI(title="GTS Server", version="0.1.0")
        self.app.add_middleware(
            _RequestLoggingMiddleware,
            verbose=self.ops.verbose,
        )
        self._register_routes()

    # Routes registration grouped here to avoid free functions
    def _register_routes(self) -> None:
        app = self.app

        # Population APIs
        app.add_api_route(
            "/entities",
            self.get_entities,
            methods=["GET"],
            summary="Get all entities in the registry",
            response_class=JSONResponse,
        )
        app.add_api_route(
            "/entities",
            self.add_entity,
            methods=["POST"],
            summary=(
                "Register a single entity (object or schema)"
            ),
            response_class=JSONResponse,
        )
        app.add_api_route(
            "/entities/bulk",
            self.add_entities,
            methods=["POST"],
            summary="Register multiple entities",
            response_class=JSONResponse,
        )
        app.add_api_route(
            "/schemas",
            self.add_schema,
            methods=["POST"],
            summary="Register schema by explicit type_id",
            response_class=JSONResponse,
        )

        # Op #1 - validate id
        app.add_api_route(
            "/validate-id",
            self.validate_id,
            methods=["GET"],
            summary="Validate GTS identifier",
        )
        # Op #2 - extract id
        app.add_api_route(
            "/extract-id",
            self.extract_id,
            methods=["POST"],
            summary=(
                "Extract GTS ID and schema ID from JSON content"
            ),
        )
        # Op #3 - parse
        app.add_api_route(
            "/parse-id",
            self.parse,
            methods=["GET"],
            summary="Parse GTS identifier",
        )
        # Op #4 - wildcard match
        app.add_api_route(
            "/match-id-pattern",
            self.match_id_pattern,
            methods=["GET"],
            summary=(
                "Match candidate against wildcard pattern"
            ),
        )
        # Op #5 - uuid
        app.add_api_route(
            "/uuid",
            self.id_to_uuid,
            methods=["GET"],
            summary="Map GTS ID to UUID",
        )
        # Op #6 - validate instance
        app.add_api_route(
            "/validate-instance",
            self.validate_instance,
            methods=["POST"],
            summary="Validate instance by GTS ID",
        )
        # Op #7 - schema graph / relationships
        app.add_api_route(
            "/resolve-relationships",
            self.schema_graph,
            methods=["GET"],
            summary=(
                "Build schema/entity graph for a GTS ID"
            ),
        )
        # Op #8 - compatibility
        app.add_api_route(
            "/compatibility",
            self.compatibility,
            methods=["GET"],
            summary="Check minor version compatibility",
        )
        # Op #9 - cast
        app.add_api_route(
            "/cast",
            self.cast,
            methods=["POST"],
            summary="Cast instance to target schema",
        )
        # Op #10 - query
        app.add_api_route(
            "/query",
            self.query,
            methods=["GET"],
            summary="Execute GTS query over loaded entities",
        )
        # Op #11 - attribute access
        app.add_api_route(
            "/attr",
            self.attr,
            methods=["GET"],
            summary="Resolve attribute path via '@' selector",
        )

    # Handlers as methods (no free functions)
    async def add_entity(self, body: Dict[str, Any] = Body(...)) -> JSONResponse:
        return JSONResponse(self.ops.add_entity(body).to_dict())

    async def add_entities(self, body: List[Dict[str, Any]] = Body(...)) -> JSONResponse:
        return JSONResponse(self.ops.add_entities(body).to_dict())

    async def add_schema(self, body: SchemaRegister) -> JSONResponse:
        return JSONResponse(
            self.ops.add_schema(body.type_id, body.schema_content).to_dict()
        )

    async def validate_id(self, id: str = Query(..., alias="gts_id")) -> Dict[str, Any]:
        return self.ops.validate_id(id).to_dict()

    async def extract_id(self, body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
        return self.ops.extract_id(body).to_dict()

    async def parse(self, id: str = Query(..., alias="gts_id")) -> Dict[str, Any]:
        return self.ops.parse_id(id).to_dict()

    async def match_id_pattern(
        self,
        candidate: str = Query(...),
        pattern: str = Query(...),
    ) -> Dict[str, Any]:
        return self.ops.match_id_pattern(candidate, pattern).to_dict()

    async def id_to_uuid(self, id: str = Query(..., alias="gts_id")) -> Dict[str, Any]:
        return self.ops.uuid(id).to_dict()

    async def validate_instance(self, body: ValidateInstanceRequest) -> Dict[str, Any]:
        return self.ops.validate_instance(body.instance_id).to_dict()

    async def schema_graph(self, id: str = Query(..., alias="gts_id")) -> Dict[str, Any]:
        return self.ops.schema_graph(id).to_dict()

    async def compatibility(
        self,
        old: str = Query(..., alias="old_schema_id"),
        new: str = Query(..., alias="new_schema_id"),
    ) -> Dict[str, Any]:
        return self.ops.compatibility(old, new).to_dict()

    async def cast(self, body: CastRequest) -> Dict[str, Any]:
        return self.ops.cast(body.instance_id, body.to_schema_id).to_dict()

    async def query(self, expr: str = Query(...), limit: int = Query(100, ge=1, le=1000)) -> Dict[str, Any]:
        return self.ops.query(expr, limit=limit).to_dict()

    async def attr(self, gts_with_path: str = Query(...)) -> Dict[str, Any]:
        return self.ops.attr(gts_with_path).to_dict()

    async def get_entities(self, limit: int = Query(100, ge=1, le=1000)) -> Dict[str, Any]:
        return self.ops.get_entities(limit=limit).to_dict()
