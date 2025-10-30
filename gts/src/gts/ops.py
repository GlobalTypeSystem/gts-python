from __future__ import annotations

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

import json
from pathlib import Path as SysPath

from .gts import GtsID, GtsWildcard
from .entities import DEFAULT_GTS_CONFIG, GtsConfig, JsonEntity
from .files_reader import GtsFileReader
from .path_resolver import JsonPathResolver
from .store import GtsStore, GtsStoreQueryResult
from .schema_cast import JsonEntityCastResult

# Interface helpers


@dataclass
class GtsIdValidationResult:
    """Result of validating a GTS ID format."""
    id: str
    valid: bool
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "valid": self.valid, "error": self.error}


@dataclass
class GtsIdSegment:
    """Represents a single segment of a GTS ID."""
    vendor: str
    package: str
    namespace: str
    type: str
    ver_major: Optional[int]
    ver_minor: Optional[int]
    is_type: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vendor": self.vendor,
            "package": self.package,
            "namespace": self.namespace,
            "type": self.type,
            "ver_major": self.ver_major,
            "ver_minor": self.ver_minor,
            "is_type": self.is_type,
        }


@dataclass
class GtsIdParseResult:
    """Result of parsing a GTS ID into its components."""
    id: str
    ok: bool
    segments: List[GtsIdSegment] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "ok": self.ok,
            "segments": [s.to_dict() for s in self.segments],
            "error": self.error,
        }


@dataclass
class GtsIdMatchResult:
    """Result of matching a GTS ID against a pattern."""
    candidate: str
    pattern: str
    match: bool
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "candidate": self.candidate,
            "pattern": self.pattern,
            "match": self.match
        }
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class GtsUuidResult:
    """Result of generating a UUID from a GTS ID."""
    id: str
    uuid: str

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "uuid": self.uuid}


@dataclass
class GtsValidationResult:
    """Result of validating an instance against its schema."""
    id: str
    ok: bool
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result = {"id": self.id, "ok": self.ok}
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class GtsSchemaGraphResult:
    """Result of building a schema graph for an entity."""
    graph: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return self.graph


@dataclass
class GtsEntityInfo:
    """Information about a single entity."""
    id: str
    schema_id: Optional[str]
    is_schema: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "schema_id": self.schema_id,
            "is_schema": self.is_schema,
        }


@dataclass
class GtsEntitiesListResult:
    """Result of listing entities."""
    entities: List[GtsEntityInfo]
    count: int
    total: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "count": self.count,
            "total": self.total,
        }


@dataclass
class GtsAddEntityResult:
    """Result of adding an entity to the store."""
    ok: bool
    id: str = ""
    schema_id: Optional[str] = None
    is_schema: bool = False
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"ok": self.ok}
        if self.ok:
            result["id"] = self.id
            result["schema_id"] = self.schema_id
            result["is_schema"] = self.is_schema
        else:
            result["error"] = self.error
        return result


@dataclass
class GtsAddEntitiesResult:
    """Result of adding multiple entities to the store."""
    ok: bool
    results: List[GtsAddEntityResult]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "results": [r.to_dict() for r in self.results],
        }


@dataclass
class GtsAddSchemaResult:
    """Result of adding a schema to the store."""
    ok: bool
    id: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"ok": self.ok}
        if self.ok:
            result["id"] = self.id
        else:
            result["error"] = self.error
        return result


@dataclass
class GtsExtractIdResult:
    """Result of extracting ID information from content."""
    id: str
    schema_id: Optional[str]
    selected_entity_field: Optional[str]
    selected_schema_id_field: Optional[str]
    is_schema: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "schema_id": self.schema_id,
            "selected_entity_field": self.selected_entity_field,
            "selected_schema_id_field": self.selected_schema_id_field,
            "is_schema": self.is_schema,
        }


class GtsOps:
    def __init__(self, *, path: Optional[str | List[str]] = None, config: Optional[str] = None, verbose: int = 0) -> None:
        self.verbose = verbose
        self.cfg = self._load_config(config)
        self.path: Optional[str | List[str]] = path
        self._reader = GtsFileReader(self.path, cfg=self.cfg) if self.path else None
        self.store = GtsStore(self._reader) if self._reader else GtsStore(reader=None)  # type: ignore[arg-type]

    @staticmethod
    def _create_config_from_data(data: Dict[str, Any]) -> GtsConfig:
        """Create GtsConfig from JSON data with defaults."""
        return GtsConfig(
            entity_id_fields=list(data.get("entity_id_fields", DEFAULT_GTS_CONFIG.entity_id_fields)),
            schema_id_fields=list(data.get("schema_id_fields", DEFAULT_GTS_CONFIG.schema_id_fields)),
        )

    @staticmethod
    def _load_config_from_path(path: SysPath) -> Optional[GtsConfig]:
        """Try to load config from a path, return None on failure."""
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return GtsOps._create_config_from_data(data)
        except Exception:
            return None

    def _load_config(self, config_path: Optional[str]) -> GtsConfig:
        """Load config from user path, default path, or use defaults."""
        # Try user-provided path
        if config_path:
            config = self._load_config_from_path(SysPath(config_path).expanduser())
            if config:
                return config

        # Try default path
        default_path = SysPath(__file__).resolve().parents[2] / "gts.config.json"
        config = self._load_config_from_path(default_path)
        if config:
            return config

        # Fall back to defaults
        return DEFAULT_GTS_CONFIG

    def reload_from_path(self, path: str | List[str]) -> None:
        self.path = path
        self._reader = GtsFileReader(self.path, cfg=self.cfg)
        self.store = GtsStore(self._reader)

    def add_entity(self, content: Dict[str, Any]) -> GtsAddEntityResult:
        entity = JsonEntity(content=content, cfg=self.cfg)
        if not entity.gts_id:
            return GtsAddEntityResult(ok=False, error="Unable to detect GTS ID in entity")
        self.store.register(entity)
        return GtsAddEntityResult(
            ok=True,
            id=entity.gts_id.id,
            schema_id=entity.schemaId,
            is_schema=entity.is_schema
        )

    def add_entities(self, items: List[Dict[str, Any]]) -> GtsAddEntitiesResult:
        results: List[GtsAddEntityResult] = []
        for it in items:
            results.append(self.add_entity(it))
        ok = all(r.ok for r in results)
        return GtsAddEntitiesResult(ok=ok, results=results)

    def add_schema(self, type_id: str, schema: Dict[str, Any]) -> GtsAddSchemaResult:
        try:
            self.store.register_schema(type_id, schema)
            return GtsAddSchemaResult(ok=True, id=type_id)
        except Exception as e:
            return GtsAddSchemaResult(ok=False, error=str(e))

    def validate_id(self, gts_id: str) -> GtsIdValidationResult:
        try:
            _ = GtsID(gts_id)
            return GtsIdValidationResult(id=gts_id, valid=True)
        except Exception as e:
            return GtsIdValidationResult(id=gts_id, valid=False, error=str(e))

    def parse_id(self, gts_id: str) -> GtsIdParseResult:
        try:
            segs = GtsID(gts_id).gts_id_segments
            segments = [
                GtsIdSegment(
                    vendor=s.vendor,
                    package=s.package,
                    namespace=s.namespace,
                    type=s.type,
                    ver_major=s.ver_major,
                    ver_minor=s.ver_minor,
                    is_type=s.is_type,
                )
                for s in segs
            ]
            return GtsIdParseResult(id=gts_id, ok=True, segments=segments)
        except Exception as e:
            return GtsIdParseResult(id=gts_id, ok=False, error=str(e))

    def match_id_pattern(self, candidate: str, pattern: str) -> GtsIdMatchResult:
        try:
            c = GtsID(candidate)
            p = GtsWildcard(pattern)
            match = c.wildcard_match(p)
            return GtsIdMatchResult(candidate=candidate, pattern=pattern, match=match)
        except Exception as e:
            return GtsIdMatchResult(candidate=candidate, pattern=pattern, match=False, error=str(e))

    def uuid(self, gts_id: str) -> GtsUuidResult:
        g = GtsID(gts_id)
        return GtsUuidResult(id=g.id, uuid=str(g.to_uuid()))

    def validate_instance(self, gts_id: str) -> GtsValidationResult:
        try:
            self.store.validate_instance(gts_id)
            return GtsValidationResult(id=gts_id, ok=True)
        except Exception as e:
            return GtsValidationResult(id=gts_id, ok=False, error=str(e))

    def schema_graph(self, gts_id: str) -> GtsSchemaGraphResult:
        graph = self.store.build_schema_graph(gts_id)
        return GtsSchemaGraphResult(graph=graph)

    def compatibility(self, old_schema_id: str, new_schema_id: str) -> JsonEntityCastResult:
        return self.store.is_minor_compatible(old_schema_id, new_schema_id)

    def cast(self, from_id: str, to_schema_id: str) -> JsonEntityCastResult:
        try:
            return self.store.cast(from_id, to_schema_id)
        except Exception as e:
            return JsonEntityCastResult(error=str(e))

    def query(self, expr: str, limit: int = 100) -> GtsStoreQueryResult:
        return self.store.query(expr, limit)

    def attr(self, gts_with_path: str) -> JsonPathResolver:
        gts, path = GtsID.split_at_path(gts_with_path)
        if path is None:
            return JsonPathResolver(gts_id=gts, content=None).failure("", "Attribute selector requires '@path' in the identifier")
        entity = self.store.get(gts)
        if not entity:
            return JsonPathResolver(gts_id=gts, content=None).failure(path, f"Entity not found: {gts}")
        return entity.resolve_path(path)

    def extract_id(self, content: Dict[str, Any]) -> GtsExtractIdResult:
        entity = JsonEntity(content=content, cfg=self.cfg)
        return GtsExtractIdResult(
            id=entity.gts_id.id if entity.gts_id else "",
            schema_id=entity.schemaId,
            selected_entity_field=entity.selected_entity_field,
            selected_schema_id_field=entity.selected_schema_id_field,
            is_schema=entity.is_schema,
        )

    def get_entities(self, limit: int = 100) -> GtsEntitiesListResult:
        """Get all entities in the registry.

        Args:
            limit: Maximum number of entities to return (default: 100)

        Returns:
            GtsEntitiesListResult with entities list, count, and total
        """
        all_entities = list(self.store.items())
        total = len(all_entities)
        entities = [
            GtsEntityInfo(
                id=entity_id,
                schema_id=entity.schemaId,
                is_schema=entity.is_schema,
            )
            for entity_id, entity in all_entities[:limit]
        ]
        return GtsEntitiesListResult(entities=entities, count=len(entities), total=total)

    def list(self, limit: int = 100) -> GtsEntitiesListResult:
        """Alias for get_entities. List all discovered entities.

        Args:
            limit: Maximum number of entities to return (default: 100)

        Returns:
            GtsEntitiesListResult with entities list, count, and total
        """
        return self.get_entities(limit=limit)
