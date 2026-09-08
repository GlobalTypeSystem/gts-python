from __future__ import annotations

import builtins
import json
from dataclasses import dataclass, field
from pathlib import Path as SysPath
from typing import Any

from .entities import DEFAULT_GTS_CONFIG, GtsConfig, GtsEntity
from .files_reader import GtsFileReader
from .gts import GtsID, GtsWildcard
from .path_resolver import GtsPathResolver
from .schema_cast import GtsEntityCastResult
from .store import GtsStore, GtsStoreQueryResult

# Interface helpers


@dataclass
class GtsIdValidationResult:
    """Result of validating a GTS ID format."""

    id: str
    valid: bool
    error: str = ""
    is_type: bool | None = None
    is_wildcard: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "valid": self.valid,
            "is_wildcard": self.is_wildcard,
        }
        if self.error:
            d["error"] = self.error
        if self.is_type is not None:
            d["is_type"] = self.is_type
        return d


@dataclass
class GtsIdSegment:
    """Represents a single segment of a GTS ID."""

    vendor: str
    package: str
    namespace: str
    type: str
    ver_major: int | None
    ver_minor: int | None
    is_type: bool

    def to_dict(self) -> dict[str, Any]:
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
    segments: list[GtsIdSegment] = field(default_factory=list)
    error: str = ""
    is_type: bool | None = None
    is_wildcard: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "ok": self.ok,
            "segments": [s.to_dict() for s in self.segments],
            "is_wildcard": self.is_wildcard,
        }
        if self.error:
            d["error"] = self.error
        if self.is_type is not None:
            d["is_type"] = self.is_type
        return d


@dataclass
class GtsIdMatchResult:
    """Result of matching a GTS ID against a pattern."""

    candidate: str
    pattern: str
    match: bool
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = {
            "candidate": self.candidate,
            "pattern": self.pattern,
            "match": self.match,
        }
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class GtsUuidResult:
    """Result of generating a UUID from a GTS ID."""

    id: str
    uuid: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "uuid": self.uuid}


@dataclass
class GtsValidationResult:
    """Result of validating an instance against its schema."""

    id: str
    ok: bool
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = {"id": self.id, "ok": self.ok}
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class GtsEntityValidationResult:
    """Result of validating an entity (instance or type schema)."""

    id: str
    ok: bool
    entity_type: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "ok": self.ok}
        if self.entity_type:
            result["entity_type"] = self.entity_type
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class GtsSchemaGraphResult:
    """Result of building a schema graph for an entity."""

    graph: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.graph


@dataclass
class GtsEntityInfo:
    """Information about a single entity."""

    id: str
    type_id: str | None
    is_type_schema: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type_id": self.type_id,
            "is_type_schema": self.is_type_schema,
        }


@dataclass
class GtsGetEntityResult:
    """Result of getting a single entity."""

    ok: bool
    id: str = ""
    type_id: str | None = None
    is_type_schema: bool = False
    content: Any = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            result["id"] = self.id
            result["type_id"] = self.type_id
            result["is_type_schema"] = self.is_type_schema
            result["content"] = self.content
        else:
            result["error"] = self.error
        return result


@dataclass
class GtsEntitiesListResult:
    """Result of listing entities."""

    entities: list[GtsEntityInfo]
    count: int
    total: int

    def to_dict(self) -> dict[str, Any]:
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
    type_id: str | None = None
    is_type_schema: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            result["id"] = self.id
            result["type_id"] = self.type_id
            result["is_type_schema"] = self.is_type_schema
        else:
            result["error"] = self.error
            result["is_type_schema"] = self.is_type_schema
        return result


@dataclass
class GtsAddEntitiesResult:
    """Result of adding multiple entities to the store."""

    ok: bool
    results: list[GtsAddEntityResult]

    def to_dict(self) -> dict[str, Any]:
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

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            result["id"] = self.id
        else:
            result["error"] = self.error
        return result


@dataclass
class GtsExtractIdResult:
    """Result of extracting ID information from content."""

    id: str
    type_id: str | None
    selected_entity_field: str | None
    selected_type_id_field: str | None
    is_type_schema: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type_id": self.type_id,
            "selected_entity_field": self.selected_entity_field,
            "selected_type_id_field": self.selected_type_id_field,
            "is_type_schema": self.is_type_schema,
        }


class GtsOps:
    def __init__(
        self,
        *,
        path: str | builtins.list[str] | None = None,
        config: str | None = None,
        verbose: int = 0,
    ) -> None:
        self.verbose = verbose
        self.cfg = self._load_config(config)
        self.path: str | list[str] | None = path
        self._reader = GtsFileReader(self.path, cfg=self.cfg) if self.path else None
        self.store = GtsStore(self._reader) if self._reader else GtsStore(reader=None)  # type: ignore[arg-type]

    @staticmethod
    def _create_config_from_data(data: dict[str, Any]) -> GtsConfig:
        """Create GtsConfig from JSON data with defaults."""
        return GtsConfig(
            entity_id_fields=list(
                data.get("entity_id_fields", DEFAULT_GTS_CONFIG.entity_id_fields)
            ),
            schema_id_fields=list(
                data.get("schema_id_fields", DEFAULT_GTS_CONFIG.schema_id_fields)
            ),
        )

    @staticmethod
    def _load_config_from_path(path: SysPath) -> GtsConfig | None:
        """Try to load config from a path, return None on failure."""
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return GtsOps._create_config_from_data(data)
        except Exception:  # noqa: BLE001 - fall back to defaults on any load failure
            return None

    def _load_config(self, config_path: str | None) -> GtsConfig:
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

    def reload_from_path(self, path: str | builtins.list[str]) -> None:
        self.path = path
        self._reader = GtsFileReader(self.path, cfg=self.cfg)
        self.store = GtsStore(self._reader)

    def add_entity(
        self, content: dict[str, Any], validate: bool = False
    ) -> GtsAddEntityResult:
        entity = GtsEntity(content=content, cfg=self.cfg)

        # For instances (non-schemas), require an id field from entity_id_fields
        # (not just derived from schema)
        if not entity.is_schema and (
            not entity.raw_id or not entity.selected_entity_field
        ):
            return GtsAddEntityResult(
                ok=False,
                error="Instance must have an id field",
                is_type_schema=False,
            )

        # Schemas MUST have a valid GTS ID
        if entity.is_schema and not entity.gts_id:
            return GtsAddEntityResult(
                ok=False, error="Unable to detect GTS ID in schema"
            )

        # Validate $id prefix for schemas: must use gts:// URI, not plain gts.
        if entity.is_schema and validate:
            raw_id = content.get("$id", "")
            # Reject plain gts. prefix (without gts://)
            if (
                isinstance(raw_id, str)
                and raw_id.startswith("gts.")
                and not raw_id.startswith("gts://")
            ):
                return GtsAddEntityResult(
                    ok=False,
                    error="Schema $id must use gts:// URI format, not plain gts. prefix",
                    is_type_schema=True,
                )

        store_key = entity.gts_id.id if entity.is_schema else entity.raw_id
        previous = self.store.get(store_key)
        self.store.register(entity)

        try:
            if entity.is_schema:
                self.store.validate_schema_basic(entity.gts_id.id)
                if validate:
                    self.store.validate_schema(entity.gts_id.id)
            elif validate:
                self.store.validate_instance(entity.raw_id or entity.gts_id.id)
        except Exception as e:  # noqa: BLE001 - converted to a result object at API boundary
            self.store.unregister(store_key)
            if previous:
                self.store.register(previous)
            return GtsAddEntityResult(
                ok=False,
                error=f"Validation failed: {e!s}",
                is_type_schema=entity.is_schema,
            )

        # Return gts_id if available, otherwise raw_id
        entity_id = entity.gts_id.id if entity.gts_id else (entity.raw_id or "")
        return GtsAddEntityResult(
            ok=True,
            id=entity_id,
            type_id=entity.type_id,
            is_type_schema=entity.is_schema,
        )

    def add_entities(
        self, items: builtins.list[dict[str, Any]]
    ) -> GtsAddEntitiesResult:
        results: list[GtsAddEntityResult] = []
        for it in items:
            results.append(self.add_entity(it))
        ok = all(r.ok for r in results)
        return GtsAddEntitiesResult(ok=ok, results=results)

    def add_schema(self, type_id: str, schema: dict[str, Any]) -> GtsAddSchemaResult:
        try:
            self.store.register_schema(type_id, schema)
            return GtsAddSchemaResult(ok=True, id=type_id)
        except Exception as e:  # noqa: BLE001 - converted to a result object at API boundary
            return GtsAddSchemaResult(ok=False, error=str(e))

    def validate_id(self, gts_id: str) -> GtsIdValidationResult:
        # Check if it's a wildcard pattern (contains *)
        is_wildcard = "*" in gts_id
        try:
            if is_wildcard:
                # For wildcards, try parsing as GtsWildcard
                _ = GtsWildcard(gts_id)
                return GtsIdValidationResult(
                    id=gts_id, valid=True, is_type=False, is_wildcard=True
                )
            else:
                parsed = GtsID(gts_id)
                return GtsIdValidationResult(
                    id=gts_id, valid=True, is_type=parsed.is_type, is_wildcard=False
                )
        except Exception as e:  # noqa: BLE001 - converted to a result object at API boundary
            return GtsIdValidationResult(
                id=gts_id,
                valid=False,
                error=str(e),
                is_type=None,
                is_wildcard=is_wildcard,
            )

    def parse_id(self, gts_id: str) -> GtsIdParseResult:
        # Check if it's a wildcard pattern (contains *)
        is_wildcard = "*" in gts_id
        try:
            if is_wildcard:
                parsed = GtsWildcard(gts_id)
                segs = parsed.gts_id_segments
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
                return GtsIdParseResult(
                    id=gts_id,
                    ok=True,
                    segments=segments,
                    is_type=False,
                    is_wildcard=True,
                )
            else:
                parsed = GtsID(gts_id)
                segs = parsed.gts_id_segments
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
                return GtsIdParseResult(
                    id=gts_id,
                    ok=True,
                    segments=segments,
                    is_type=parsed.is_type,
                    is_wildcard=False,
                )
        except Exception as e:  # noqa: BLE001 - converted to a result object at API boundary
            return GtsIdParseResult(
                id=gts_id, ok=False, error=str(e), is_type=None, is_wildcard=is_wildcard
            )

    def match_id_pattern(self, candidate: str, pattern: str) -> GtsIdMatchResult:
        try:
            # If candidate contains '*', validate it as a wildcard pattern
            # This catches malformed wildcards like 'a*' (wildcard not on token boundary)
            if "*" in candidate:
                # Validate candidate as a wildcard pattern first
                _ = GtsWildcard(candidate)
            c = GtsID(candidate)
            p = GtsWildcard(pattern)
            match = c.wildcard_match(p)
            return GtsIdMatchResult(candidate=candidate, pattern=pattern, match=match)
        except Exception as e:  # noqa: BLE001 - converted to a result object at API boundary
            return GtsIdMatchResult(
                candidate=candidate, pattern=pattern, match=False, error=str(e)
            )

    def uuid(self, gts_id: str) -> GtsUuidResult:
        g = GtsID(gts_id)
        return GtsUuidResult(id=g.id, uuid=str(g.to_uuid()))

    def validate_instance(self, gts_id: str) -> GtsValidationResult:
        try:
            self.store.validate_instance(gts_id)
            return GtsValidationResult(id=gts_id, ok=True)
        except Exception as e:  # noqa: BLE001 - converted to a result object at API boundary
            return GtsValidationResult(id=gts_id, ok=False, error=str(e))

    def validate_schema(self, gts_id: str) -> GtsValidationResult:
        try:
            self.store.validate_schema(gts_id)
            return GtsValidationResult(id=gts_id, ok=True)
        except Exception as e:  # noqa: BLE001 - converted to a result object at API boundary
            return GtsValidationResult(id=gts_id, ok=False, error=str(e))

    def validate_entity(self, gts_id: str) -> GtsEntityValidationResult:
        try:
            parsed = GtsID(gts_id)
        except Exception as e:  # noqa: BLE001 - converted to a result object at API boundary
            return GtsEntityValidationResult(
                id=gts_id, ok=False, entity_type="", error=str(e)
            )

        if parsed.is_type:
            entity_type = "schema"
            result = self.validate_schema(gts_id)
        else:
            entity_type = "instance"
            result = self.validate_instance(gts_id)

        return GtsEntityValidationResult(
            id=result.id, ok=result.ok, entity_type=entity_type, error=result.error
        )

    def schema_graph(self, gts_id: str) -> GtsSchemaGraphResult:
        graph = self.store.build_schema_graph(gts_id)
        return GtsSchemaGraphResult(graph=graph)

    def compatibility(
        self, old_schema_id: str, new_schema_id: str
    ) -> GtsEntityCastResult:
        return self.store.is_minor_compatible(old_schema_id, new_schema_id)

    def cast(self, from_id: str, to_schema_id: str) -> GtsEntityCastResult:
        try:
            return self.store.cast(from_id, to_schema_id)
        except Exception as e:  # noqa: BLE001 - converted to a result object at API boundary
            return GtsEntityCastResult(error=str(e))

    def query(self, expr: str, limit: int = 100) -> GtsStoreQueryResult:
        return self.store.query(expr, limit)

    def attr(self, gts_with_path: str) -> GtsPathResolver:
        gts, path = GtsID.split_at_path(gts_with_path)
        if path is None:
            return GtsPathResolver(gts_id=gts, content=None).failure(
                "", "Attribute selector requires '@path' in the identifier"
            )
        entity = self.store.get(gts)
        if not entity:
            return GtsPathResolver(gts_id=gts, content=None).failure(
                path, f"Entity not found: {gts}"
            )
        return entity.resolve_path(path)

    def extract_id(self, content: dict[str, Any]) -> GtsExtractIdResult:
        entity = GtsEntity(content=content, cfg=self.cfg)
        # Use effective_id: raw_id for non-schemas, gts_id for schemas
        if entity.is_schema:
            id_value = entity.gts_id.id if entity.gts_id else (entity.raw_id or "")
        else:
            id_value = entity.raw_id or ""
        return GtsExtractIdResult(
            id=id_value,
            type_id=entity.type_id,
            selected_entity_field=entity.selected_entity_field,
            selected_type_id_field=entity.selected_type_id_field,
            is_type_schema=entity.is_schema,
        )

    def get_entity(self, gts_id: str) -> GtsGetEntityResult:
        """Get a single entity by its GTS ID.

        Args:
            gts_id: The GTS ID of the entity to retrieve

        Returns:
            GtsGetEntityResult with entity details or error
        """
        try:
            entity = self.store.get(gts_id)
            if not entity:
                return GtsGetEntityResult(
                    ok=False, error=f"Entity '{gts_id}' not found"
                )
            return GtsGetEntityResult(
                ok=True,
                id=entity.gts_id.id if entity.gts_id else gts_id,
                type_id=entity.type_id,
                is_type_schema=entity.is_schema,
                content=entity.content,
            )
        except Exception as e:  # noqa: BLE001 - converted to a result object at API boundary
            return GtsGetEntityResult(ok=False, error=str(e))

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
                type_id=entity.type_id,
                is_type_schema=entity.is_schema,
            )
            for entity_id, entity in all_entities[:limit]
        ]
        return GtsEntitiesListResult(
            entities=entities, count=len(entities), total=total
        )

    def list(self, limit: int = 100) -> GtsEntitiesListResult:
        """Alias for get_entities. List all discovered entities.

        Args:
            limit: Maximum number of entities to return (default: 100)

        Returns:
            GtsEntitiesListResult with entities list, count, and total
        """
        return self.get_entities(limit=limit)
