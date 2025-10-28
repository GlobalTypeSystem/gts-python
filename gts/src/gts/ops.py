from __future__ import annotations

from typing import Any, Dict, List, Optional

import json
from pathlib import Path as SysPath

from .gts import GtsID, GtsWildcard
from .entities import DEFAULT_GTS_CONFIG, GtsConfig, JsonEntity
from .files_reader import GtsFileReader
from .path_resolver import JsonPathResolver
from .store import GtsStore


class GtsOps:
    def __init__(self, *, path: Optional[str | List[str]] = None, config: Optional[str] = None, verbose: int = 0) -> None:
        self.verbose = verbose
        self.cfg = self._load_config(config)
        self.path: Optional[str | List[str]] = path
        self._reader = GtsFileReader(self.path, cfg=self.cfg) if self.path else None
        self.store = GtsStore(self._reader) if self._reader else GtsStore(reader=None)  # type: ignore[arg-type]

    def _load_config(self, config_path: Optional[str]) -> GtsConfig:
        if config_path:
            p = SysPath(config_path).expanduser()
            try:
                with p.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                return GtsConfig(
                    entity_id_fields=list(data.get("entity_id_fields", DEFAULT_GTS_CONFIG.entity_id_fields)),
                    schema_id_fields=list(data.get("schema_id_fields", DEFAULT_GTS_CONFIG.schema_id_fields)),
                )
            except Exception:
                pass
        try:
            default_path = SysPath(__file__).resolve().parents[2] / "gts.config.json"
            with default_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return GtsConfig(
                entity_id_fields=list(data.get("entity_id_fields", DEFAULT_GTS_CONFIG.entity_id_fields)),
                schema_id_fields=list(data.get("schema_id_fields", DEFAULT_GTS_CONFIG.schema_id_fields)),
            )
        except Exception:
            return DEFAULT_GTS_CONFIG

    def reload_from_path(self, path: str | List[str]) -> None:
        self.path = path
        self._reader = GtsFileReader(self.path, cfg=self.cfg)
        self.store = GtsStore(self._reader)

    def add_entity(self, content: Dict[str, Any]) -> Dict[str, Any]:
        entity = JsonEntity(content=content, cfg=self.cfg)
        if not entity.gts_id:
            return {"ok": False, "error": "Unable to detect GTS ID in entity"}
        self.store.register(entity)
        return {"ok": True, "id": entity.gts_id.id, "schema_id": entity.schemaId, "is_schema": entity.is_schema}

    def add_entities(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        results: List[Dict[str, Any]] = []
        for it in items:
            results.append(self.add_entity(it))
        ok = all(r.get("ok") for r in results)
        return {"ok": ok, "results": results}

    def add_schema(self, type_id: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        try:
            self.store.register_schema(type_id, schema)
            return {"ok": True, "id": type_id}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def validate_id(self, gts_id: str) -> Dict[str, Any]:
        try:
            _ = GtsID(gts_id)
            return {"id": gts_id, "valid": True, "error": ""}
        except Exception as e:
            return {"id": gts_id, "valid": False, "error": str(e)}

    def parse(self, gts_id: str) -> Dict[str, Any]:
        ret: Dict[str, Any] = {"id": gts_id}
        try:
            segs = GtsID(gts_id).gts_id_segments
            ret["segments"] = [
                {
                    "vendor": s.vendor,
                    "package": s.package,
                    "namespace": s.namespace,
                    "type": s.type,
                    "ver_major": s.ver_major,
                    "ver_minor": s.ver_minor,
                    "is_type": s.is_type,
                }
                for s in segs
            ]
        except Exception as e:
            ret["error"] = str(e)
        return ret

    def wildcard_match(self, candidate: str, pattern: str) -> Dict[str, Any]:
        ret = {"candidate": candidate, "pattern": pattern, "match": False}
        try:
            c = GtsID(candidate)
            p = GtsWildcard(pattern)
            ret["match"] = c.wildcard_match(p)
        except Exception as e:
            ret["error"] = str(e)
        return ret

    def uuid(self, gts_id: str) -> Dict[str, Any]:
        g = GtsID(gts_id)
        return {"id": g.id, "uuid": str(g.to_uuid())}

    def validate_instance(self, gts_id: str) -> Dict[str, Any]:
        ret: Dict[str, Any] = {"id": gts_id, "ok": True}
        try:
            self.store.validate_instance(gts_id)
        except Exception as e:
            ret["ok"] = False
            ret["error"] = str(e)
        return ret

    def schema_graph(self, gts_id: str) -> Any:
        return self.store.build_schema_graph(gts_id)

    def compatibility(self, old_schema_id: str, new_schema_id: str) -> Dict[str, Any]:
        ok = self.store.is_minor_compatible(old_schema_id, new_schema_id)
        return {"old": old_schema_id, "new": new_schema_id, "minor_compatible": ok}

    def cast(self, instance_id: str, to_schema_id: str) -> Dict[str, Any]:
        try:
            return self.store.cast(instance_id, to_schema_id).to_dict()
        except Exception as e:
            return {"error": str(e)}

    def query(self, expr: str) -> Any:
        return self.store.query(expr)

    def attr(self, gts_with_path: str) -> Dict[str, Any]:
        gts, path = GtsID.split_at_path(gts_with_path)
        if path is None:
            return JsonPathResolver(gts_id=gts, content=None).failure("", "Attribute selector requires '@path' in the identifier").to_dict()
        entity = self.store.get(gts)
        if not entity:
            return JsonPathResolver(gts_id=gts, content=None).failure(path, f"Entity not found: {gts}").to_dict()
        return entity.resolve_path(path).to_dict()

    def extract_id(self, content: Dict[str, Any]) -> Dict[str, Any]:
        entity = JsonEntity(content=content, cfg=self.cfg)
        return {
            "id": entity.gts_id.id if entity.gts_id else "",
            "schema_id": entity.schemaId,
            "selected_entity_field": entity.selected_entity_field,
            "selected_schema_id_field": entity.selected_schema_id_field,
            "is_schema": entity.is_schema,
        }
