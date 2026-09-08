from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema.validators import validator_for

from .entities import GtsEntity, GtsFile
from .gts import GtsID
from .store import GtsStore


@dataclass
class GtsJsonValidationIssue:
    file: str
    stage: str
    message: str
    index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "file": self.file,
            "stage": self.stage,
            "message": self.message,
        }
        if self.index is not None:
            result["index"] = self.index
        return result


@dataclass
class GtsJsonValidationResult:
    files: int = 0
    documents: int = 0
    gts_entities: int = 0
    schemas: int = 0
    instances: int = 0
    issues: list[GtsJsonValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "files": self.files,
            "documents": self.documents,
            "gts_entities": self.gts_entities,
            "schemas": self.schemas,
            "instances": self.instances,
            "issues": [issue.to_dict() for issue in self.issues],
        }


class GtsJsonValidator:
    def __init__(self, path: str, cfg: Any) -> None:
        self.path = Path(path).expanduser()
        self.cfg = cfg
        self.result = GtsJsonValidationResult()
        self.entities: list[GtsEntity] = []

    def validate(self) -> GtsJsonValidationResult:
        for file_path in self._json_files():
            self._read_file(file_path)
        self._validate_json_schemas()
        store = self._register_gts_entities()
        self._validate_schemas(store)
        self._validate_instances(store)
        return self.result

    def _json_files(self) -> list[Path]:
        resolved = self.path.resolve(strict=False)
        if resolved.is_file():
            if resolved.suffix.lower() == ".json":
                return [resolved]
            self._issue(resolved, "discovery", "Expected a .json file")
            return []
        if not resolved.is_dir():
            self._issue(
                resolved, "discovery", "Path does not exist or is not accessible"
            )
            return []

        files: list[Path] = []
        for root, dirs, names in os.walk(resolved, followlinks=True):
            dirs[:] = [
                name for name in dirs if name not in {"node_modules", "dist", "build"}
            ]
            files.extend(
                Path(root, name).resolve(strict=False)
                for name in names
                if Path(name).suffix.lower() == ".json"
            )
        return sorted(set(files))

    def _read_file(self, file_path: Path) -> None:
        self.result.files += 1
        try:
            with file_path.open(encoding="utf-8") as source:
                content = json.load(source)
        except Exception as error:  # noqa: BLE001 - report this document and continue
            self._issue(file_path, "json", str(error))
            return

        values = content if isinstance(content, list) else [content]
        file = GtsFile(path=str(file_path), name=file_path.name, content=content)
        for index, value in enumerate(values):
            self.result.documents += 1
            self.entities.append(
                GtsEntity(
                    file=file,
                    list_sequence=index if isinstance(content, list) else None,
                    content=value,
                    cfg=self.cfg,
                )
            )

    def _validate_json_schemas(self) -> None:
        for entity in self.entities:
            content = entity.content
            if not isinstance(content, dict) or "$schema" not in content:
                continue
            try:
                validator_for(content).check_schema(content)
            except Exception as error:  # noqa: BLE001 - report this document and continue
                self._issue(entity, "json-schema", str(error))

    def _register_gts_entities(self) -> GtsStore:
        store = GtsStore(reader=None)  # type: ignore[arg-type]
        keys: set[str] = set()
        for entity in self.entities:
            if not self._is_gts_related(entity.content):
                continue
            key = self._registry_key(entity)
            if key is None:
                self._issue(
                    entity, "registry", "GTS-related document has no registrable GTS ID"
                )
                continue
            if not entity.is_schema and entity.selected_entity_field is None:
                raw_id = entity.raw_id
                if raw_id is not None:
                    entity.raw_id = str(uuid.uuid5(uuid.NAMESPACE_URL, raw_id))
                    key = entity.raw_id
            if key in keys:
                self._issue(entity, "registry", f"Duplicate GTS entity ID '{key}'")
                continue
            keys.add(key)
            store.register(entity)
            self.result.gts_entities += 1
            if entity.is_schema:
                self.result.schemas += 1
            else:
                self.result.instances += 1
        return store

    @staticmethod
    def _is_gts_related(value: Any) -> bool:
        if isinstance(value, str):
            return "gts." in value
        if isinstance(value, dict):
            return any(
                GtsJsonValidator._is_gts_related(item) for item in value.values()
            )
        if isinstance(value, list):
            return any(GtsJsonValidator._is_gts_related(item) for item in value)
        return False

    @staticmethod
    def _registry_key(entity: GtsEntity) -> str | None:
        if entity.is_schema and entity.gts_id:
            return entity.gts_id.id
        if (
            not entity.is_schema
            and entity.raw_id
            and (
                entity.gts_id
                or (entity.type_id is not None and GtsID.is_valid(entity.type_id))
            )
        ):
            return entity.raw_id
        return None

    def _validate_schemas(self, store: GtsStore) -> None:
        schemas = sorted(
            (
                entity
                for entity in self.entities
                if entity.is_schema
                and entity.gts_id
                and store.get(entity.gts_id.id) is entity
            ),
            key=self._schema_depth,
        )
        for stage, depth in (("base-type", 1), ("derived-type", None)):
            for entity in schemas:
                if (depth == 1) != (self._schema_depth(entity) == 1):
                    continue
                gts_id = entity.gts_id
                if not gts_id:
                    continue
                try:
                    store.validate_schema(gts_id.id)
                except Exception as error:  # noqa: BLE001 - report this document and continue
                    self._issue(entity, stage, str(error))

    @staticmethod
    def _schema_depth(entity: GtsEntity) -> int:
        return len(entity.gts_id.gts_id_segments) if entity.gts_id else 0

    def _validate_instances(self, store: GtsStore) -> None:
        for entity in self.entities:
            key = self._registry_key(entity)
            if (
                entity.is_schema
                or key is None
                or not self._is_gts_related(entity.content)
            ):
                continue
            try:
                store.validate_instance(key)
            except Exception as error:  # noqa: BLE001 - report this document and continue
                self._issue(entity, "instance", str(error))

    def _issue(
        self,
        source: Path | GtsEntity,
        stage: str,
        message: str,
    ) -> None:
        if isinstance(source, GtsEntity):
            file = source.file.path if source.file else source.label
            index = source.list_sequence
        else:
            file = str(source)
            index = None
        self.result.issues.append(
            GtsJsonValidationIssue(file=file, stage=stage, message=message, index=index)
        )
