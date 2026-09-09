from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .entities import GtsEntity, GtsFile
from .files_reader import DEFAULT_EXCLUDE_LIST
from .gts import GTS_PREFIX, GTS_URI_PREFIX, GtsID
from .store import GtsStore

_X_GTS_REF_KEYWORD = "x-gts-ref"


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
    def __init__(self, path: str, cfg: Any, exclude: list[str] | None = None) -> None:
        self.path = Path(path).expanduser()
        self.cfg = cfg
        self.exclude = list(exclude) if exclude else list(DEFAULT_EXCLUDE_LIST)
        self.result = GtsJsonValidationResult()
        self.entities: list[GtsEntity] = []

    def validate(self) -> GtsJsonValidationResult:
        for file_path in self._json_files():
            self._read_file(file_path)
        self._check_schema_field_type()
        store = self._register_gts_entities()
        schemas_count, instances_count = self._count_schema_instance()
        self.result.schemas = schemas_count
        self.result.instances = instances_count
        self.result.gts_entities = schemas_count + instances_count
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
        seen_files: set[Path] = set()
        seen_dirs: set[tuple[int, int]] = set()
        walk_errors: list[OSError] = []

        def _on_walk_error(err: OSError) -> None:
            walk_errors.append(err)

        for root, dirs, names in os.walk(
            resolved, followlinks=True, onerror=_on_walk_error
        ):
            # Prevent symlink cycles by tracking visited directory identities
            root_stat = os.stat(root)
            dir_id = (root_stat.st_dev, root_stat.st_ino)
            if dir_id in seen_dirs:
                dirs.clear()
                continue
            seen_dirs.add(dir_id)

            # Prune excluded directories (defaults to DEFAULT_EXCLUDE_LIST,
            # overridable via the CLI --exclude option)
            dirs[:] = [d for d in dirs if d not in self.exclude]

            for name in names:
                if Path(name).suffix.lower() == ".json":
                    rp = Path(root, name).resolve(strict=False)
                    if rp not in seen_files:
                        seen_files.add(rp)
                        files.append(rp)

        for err in walk_errors:
            self._issue(resolved, "discovery", f"Traversal error: {err}")

        return sorted(files)

    @staticmethod
    def _is_gts_marker(text: str) -> bool:
        return (
            GTS_PREFIX in text or GTS_URI_PREFIX in text or _X_GTS_REF_KEYWORD in text
        )

    def _read_file(self, file_path: Path) -> None:
        try:
            content_str = file_path.read_text(encoding="utf-8")
        except Exception as error:  # noqa: BLE001 - report this document and continue
            self._issue(file_path, "json", str(error))
            return

        if not self._is_gts_marker(content_str):
            return
        self.result.files += 1

        try:
            content = json.loads(content_str)
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

    def _check_schema_field_type(self) -> None:
        for entity in self.entities:
            content = entity.content
            if not isinstance(content, dict):
                continue
            schema_val = content.get("$schema")
            if schema_val is not None and not isinstance(schema_val, str):
                self._issue(entity, "json-schema", "$schema must be a string")

    def _register_gts_entities(self) -> GtsStore:
        store = GtsStore(reader=None)  # type: ignore[arg-type]
        keys: set[str] = set()
        for entity in self.entities:
            if not self._is_gts_related(entity.content):
                continue
            key = self._registry_key(entity)
            if key is None:
                if entity.is_schema:
                    self._issue(
                        entity,
                        "registry",
                        "GTS schema has a malformed or non-GTS $id",
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
        return store

    def _count_schema_instance(self) -> tuple[int, int]:
        schemas = 0
        instances = 0
        for entity in self.entities:
            if self._registry_key(entity) is None:
                continue
            if entity.is_schema:
                schemas += 1
            else:
                instances += 1
        return schemas, instances

    def _is_gts_related(self, value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        # Check configured identifier fields for GTS IDs.
        # Accept valid IDs and also detect likely-but-malformed ones
        # (gts:// or gts. prefix) so they get diagnosed during registration
        # rather than silently skipped.
        for f in self.cfg.entity_id_fields:
            v = value.get(f)
            if isinstance(v, str) and self._looks_gts(v):
                return True
        for f in self.cfg.schema_id_fields:
            v = value.get(f)
            if isinstance(v, str) and self._looks_gts(v):
                return True
        return False

    @staticmethod
    def _looks_gts(v: str) -> bool:
        normalized = v.removeprefix(GTS_URI_PREFIX)
        return normalized.startswith(GTS_PREFIX) or v.startswith(GTS_URI_PREFIX)

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
        pending: list[tuple[int, str, str, int | None, GtsEntity]] = []
        for entity in self.entities:
            if not entity.is_schema or not entity.gts_id:
                continue
            gid = entity.gts_id
            if store.get(gid.id) is not entity:
                continue
            depth = len(gid.gts_id_segments)
            file = entity.file.path if entity.file else entity.label
            pending.append((depth, gid.id, file, entity.list_sequence, entity))
        pending.sort(key=lambda t: (t[0], t[1], t[2], t[3] if t[3] is not None else -1))

        for depth, _gts_id, _file, _idx, entity in pending:
            stage = "base-type" if depth <= 1 else "derived-type"
            try:
                store.validate_schema(entity.gts_id.id)  # type: ignore[union-attr]
            except Exception as error:  # noqa: BLE001 - report this document and continue
                self._issue(entity, stage, str(error))

    @staticmethod
    def _schema_depth(entity: GtsEntity) -> int:
        return len(entity.gts_id.gts_id_segments) if entity.gts_id else 0

    @staticmethod
    def _entity_depth(entity: GtsEntity) -> int:
        if entity.gts_id:
            return len(entity.gts_id.gts_id_segments)
        if entity.type_id and GtsID.is_valid(entity.type_id):
            return len(GtsID(entity.type_id).gts_id_segments)
        return 0

    def _validate_instances(self, store: GtsStore) -> None:
        pending: list[tuple[int, str, str, int | None, str, GtsEntity]] = []
        for entity in self.entities:
            if entity.is_schema:
                continue
            key = self._registry_key(entity)
            if key is None:
                continue
            # Skip rejected duplicates: only validate the registered entity
            if store.get(key) is not entity:
                continue
            depth = self._entity_depth(entity)
            gts_id_str = entity.gts_id.id if entity.gts_id else ""
            file = entity.file.path if entity.file else entity.label
            pending.append((depth, gts_id_str, file, entity.list_sequence, key, entity))
        pending.sort(key=lambda t: (t[0], t[1], t[2], t[3] if t[3] is not None else -1))

        for _depth, _gts_id, _file, _idx, registry_key, entity in pending:
            try:
                store.validate_instance(registry_key)
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
