from __future__ import annotations

from typing import Any, Dict, List, Optional
import sys

from fastapi import FastAPI, Body, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, model_validator
from starlette.middleware.base import BaseHTTPMiddleware
import time
import logging

from .ops import GtsOps


# ANSI color codes
class Colors:
    # Check if output is a TTY (terminal)
    _USE_COLORS = sys.stderr.isatty()

    RESET = "\033[0m" if _USE_COLORS else ""
    BOLD = "\033[1m" if _USE_COLORS else ""
    DIM = "\033[2m" if _USE_COLORS else ""

    # Status code colors
    GREEN = "\033[92m" if _USE_COLORS else ""  # 2xx success
    YELLOW = "\033[93m" if _USE_COLORS else ""  # 3xx redirect
    RED = "\033[91m" if _USE_COLORS else ""  # 4xx, 5xx errors
    CYAN = "\033[96m" if _USE_COLORS else ""  # Method
    BLUE = "\033[94m" if _USE_COLORS else ""  # Path
    MAGENTA = "\033[95m" if _USE_COLORS else ""  # Duration
    GRAY = "\033[90m" if _USE_COLORS else ""  # DEBUG content


class _RequestLoggingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, verbose: int) -> None:
        super().__init__(app)
        self.verbose = verbose

    async def dispatch(self, request, call_next):
        if not self.verbose:
            return await call_next(request)

        start = time.time()

        # Cache request body for DEBUG logging (verbose >= 2)
        cached_body = None
        if self.verbose >= 2:
            # Read and cache the request body
            cached_body = await request.body()

            # Create a new request with the cached body
            from starlette.requests import Request

            async def receive():
                return {"type": "http.request", "body": cached_body}

            request = Request(request.scope, receive)

        response = await call_next(request)
        dur = (time.time() - start) * 1000.0

        # Determine status color
        if 200 <= response.status_code < 300:
            status_color = Colors.GREEN
        elif 300 <= response.status_code < 400:
            status_color = Colors.YELLOW
        else:
            status_color = Colors.RED

        # Log response at INFO level (verbose >= 1)
        logging.info(
            f"{Colors.CYAN}{request.method}{Colors.RESET} "
            f"{Colors.BLUE}{request.url.path}{Colors.RESET} -> "
            f"{status_color}{response.status_code}{Colors.RESET} "
            f"in {Colors.MAGENTA}{dur:.1f}ms{Colors.RESET}"
        )

        # Log request body at DEBUG level (verbose >= 2)
        if self.verbose >= 2 and cached_body:
            try:
                import json

                body_json = json.loads(cached_body.decode("utf-8"))
                body_str = json.dumps(body_json, indent=2)
                logging.debug(
                    f"{Colors.DIM}Request body:{Colors.RESET}\n"
                    f"{Colors.GRAY}{body_str}{Colors.RESET}"
                )
            except Exception:
                body_str = cached_body.decode("utf-8", errors="replace")
                logging.debug(
                    f"{Colors.DIM}Request body (raw):{Colors.RESET}\n"
                    f"{Colors.GRAY}{body_str}{Colors.RESET}"
                )

        # Log response body at DEBUG level (verbose >= 2)
        if self.verbose >= 2:
            # Read response body
            from starlette.responses import StreamingResponse, Response

            if isinstance(response, (Response, StreamingResponse)):
                response_body = b""
                async for chunk in response.body_iterator:
                    response_body += chunk

                if response_body:
                    try:
                        import json

                        body_json = json.loads(response_body.decode("utf-8"))
                        body_str = json.dumps(body_json, indent=2)
                        logging.debug(
                            f"{Colors.DIM}Response body:{Colors.RESET}\n"
                            f"{Colors.GRAY}{body_str}{Colors.RESET}"
                        )
                    except Exception:
                        body_str = response_body.decode("utf-8", errors="replace")
                        logging.debug(
                            f"{Colors.DIM}Response body (raw):{Colors.RESET}\n"
                            f"{Colors.GRAY}{body_str}{Colors.RESET}"
                        )

                # Recreate response with the body
                return Response(
                    content=response_body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                )

        return response


class SchemaRegister(BaseModel):
    type_id: str
    type_schema: Dict[str, Any]


class CastRequest(BaseModel):
    instance_id: str
    to_type_id: str


class ValidateInstanceRequest(BaseModel):
    instance_id: str


class ValidateTypeSchemaRequest(BaseModel):
    type_id: str


class ValidateEntityRequest(BaseModel):
    entity_id: Optional[str] = None
    gts_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_id(self) -> "ValidateEntityRequest":
        if not self.entity_id and not self.gts_id:
            raise ValueError("entity_id (or gts_id) is required")
        if self.entity_id and self.gts_id and self.entity_id != self.gts_id:
            raise ValueError("entity_id and gts_id must match when both are provided")
        return self

    @property
    def resolved_id(self) -> str:
        return self.entity_id or self.gts_id or ""


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
            "/entities/{gts_id:path}",
            self.get_entity,
            methods=["GET"],
            summary="Get a specific entity by GTS ID",
            response_class=JSONResponse,
        )
        app.add_api_route(
            "/entities",
            self.add_entity,
            methods=["POST"],
            summary=("Register a single entity (object or schema)"),
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
            "/type-schemas",
            self.add_schema,
            methods=["POST"],
            summary="Register a GTS Type Schema under an explicit type_id",
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
            summary=("Extract GTS ID and schema ID from JSON content"),
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
            summary=("Match candidate against wildcard pattern"),
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
        # Op #12 - validate type schema
        app.add_api_route(
            "/validate-type-schema",
            self.validate_type_schema,
            methods=["POST"],
            summary="Validate that a derived GTS Type Schema correctly extends its base chain",
        )
        # validate entity (instance or schema)
        app.add_api_route(
            "/validate-entity",
            self.validate_entity,
            methods=["POST"],
            summary="Validate entity (instance or type schema) by GTS Identifier",
        )
        # Op #7 - schema graph / relationships
        app.add_api_route(
            "/resolve-relationships",
            self.schema_graph,
            methods=["GET"],
            summary=("Build schema/entity graph for a GTS ID"),
        )
        # Op #8 - compatibility
        app.add_api_route(
            "/compatibility",
            self.compatibility,
            methods=["GET"],
            summary="Check Type Schema evolution compatibility",
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
    async def add_entity(
        self, body: Dict[str, Any] = Body(...), validate: bool = Query(False)
    ) -> JSONResponse:
        result = self.ops.add_entity(body, validate=validate)
        status_code = 200 if result.ok else 422
        return JSONResponse(result.to_dict(), status_code=status_code)

    async def add_entities(
        self, body: List[Dict[str, Any]] = Body(...)
    ) -> JSONResponse:
        return JSONResponse(self.ops.add_entities(body).to_dict())

    async def add_schema(self, body: SchemaRegister) -> JSONResponse:
        return JSONResponse(
            self.ops.add_schema(body.type_id, body.type_schema).to_dict()
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

    async def validate_type_schema(
        self, body: ValidateTypeSchemaRequest
    ) -> Dict[str, Any]:
        return self.ops.validate_schema(body.type_id).to_dict()

    async def validate_entity(self, body: ValidateEntityRequest) -> Dict[str, Any]:
        return self.ops.validate_entity(body.resolved_id).to_dict()

    async def schema_graph(
        self, id: str = Query(..., alias="gts_id")
    ) -> Dict[str, Any]:
        return self.ops.schema_graph(id).to_dict()

    async def compatibility(
        self,
        old: str = Query(..., alias="old_type_id"),
        new: str = Query(..., alias="new_type_id"),
    ) -> Dict[str, Any]:
        return self.ops.compatibility(old, new).to_dict()

    async def cast(self, body: CastRequest) -> Dict[str, Any]:
        return self.ops.cast(body.instance_id, body.to_type_id).to_dict()

    async def query(
        self, expr: str = Query(...), limit: int = Query(100, ge=1, le=1000)
    ) -> Dict[str, Any]:
        return self.ops.query(expr, limit=limit).to_dict()

    async def attr(self, gts_with_path: str = Query(...)) -> Dict[str, Any]:
        return self.ops.attr(gts_with_path).to_dict()

    async def get_entity(self, gts_id: str) -> Dict[str, Any]:
        return self.ops.get_entity(gts_id).to_dict()

    async def get_entities(
        self, limit: int = Query(100, ge=1, le=1000)
    ) -> Dict[str, Any]:
        return self.ops.get_entities(limit=limit).to_dict()
