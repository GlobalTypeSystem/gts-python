from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Set, Tuple, List, Any, Optional, Iterator

from jsonschema import validate as js_validate

from .gts import GtsID
from .entities import JsonEntity
from .schema_cast import JsonEntityCastResult

import logging


class StoreGtsObjectNotFound(Exception):
    """Exception raised when a GTS entity is not found in the store."""
    def __init__(self, entity_id: str):
        super().__init__(f"JSON object with GTS ID '{entity_id}' not found in store")
        self.entity_id = entity_id


class StoreGtsSchemaNotFound(Exception):
    """Exception raised when a GTS schema is not found in the store."""
    def __init__(self, entity_id: str):
        super().__init__(f"JSON schema with GTS ID '{entity_id}' not found in store")
        self.entity_id = entity_id


class StoreGtsSchemaForInstanceNotFound(Exception):
    """Exception raised when a GTS schema for an instance is not found in the store."""
    def __init__(self, entity_id: str):
        super().__init__(f"Can't determine JSON schema ID for instance with GTS ID '{entity_id}'")
        self.entity_id = entity_id


class GtsReader(ABC):
    """Abstract base class for reading JSON entities from various sources."""

    @abstractmethod
    def __iter__(self) -> Iterator[JsonEntity]:
        """Return an iterator that yields JsonEntity objects."""
        pass

    @abstractmethod
    def read_by_id(self, entity_id: str) -> Optional[JsonEntity]:
        """
        Read a JsonEntity by its ID.
        Returns None if the entity is not found.
        Used for cache miss scenarios.
        """
        pass

    @abstractmethod
    def reset(self) -> None:
        """Reset the iterator to start from the beginning."""
        pass


class GtsStore:
    def __init__(self, reader: GtsReader) -> None:
        """
        Initialize GtsStore with an optional GtsReader.

        Args:
            reader: GtsReader instance to populate entities from
        """
        self._by_id: Dict[str, JsonEntity] = {}
        self._reader = reader

        # Populate entities from reader if provided
        if self._reader:
            self._populate_from_reader()

        logging.info(f"Populated GtsStore with {len(self._by_id)} entities")

    def _populate_from_reader(self) -> None:
        """Populate the store by iterating through the reader."""
        if not self._reader:
            return

        for entity in self._reader:
            if entity.gts_id and entity.gts_id.id:
                self._by_id[entity.gts_id.id] = entity

    def register(self, entity: JsonEntity) -> None:
        """Register a JsonEntity in the store."""
        if not entity.gts_id or not entity.gts_id.id:
            raise ValueError("Entity must have a valid gts_id")
        self._by_id[entity.gts_id.id] = entity

    def register_schema(self, type_id: str, schema: Dict[str, Any]) -> None:
        """
        Register a schema (legacy method for backward compatibility).
        Creates a JsonEntity from the schema dict.
        """
        if not type_id.endswith("~"):
            raise ValueError("Schema type_id must end with '~'")
        # parse sanity
        gts_id = GtsID(type_id)
        entity = JsonEntity(
            content=schema,
            gts_id=gts_id,
            is_schema=True
        )
        self._by_id[type_id] = entity

    def get(self, entity_id: str) -> Optional[JsonEntity]:
        """
        Get a JsonEntity by its ID.
        If not found in cache, try to fetch from reader.
        Returns None if not found.
        """
        # Check cache first
        if entity_id in self._by_id:
            return self._by_id[entity_id]

        # Try to fetch from reader
        if self._reader:
            entity = self._reader.read_by_id(entity_id)
            if entity:
                self._by_id[entity_id] = entity
                return entity

        return None

    def get_schema_content(self, type_id: str) -> Dict[str, Any]:
        """Get schema content as dict (legacy method for backward compatibility)."""
        entity = self.get(type_id)
        if entity and isinstance(entity.content, dict):
            return entity.content
        raise KeyError(f"Schema not found: {type_id}")

    def items(self):
        """Return all entity ID and entity pairs."""
        return self._by_id.items()

    def validate_instance(
        self,
        gts_id: str,
    ) -> None:
        """
        Validate an object instance against its schema.

        Args:
            obj: The object to validate
            gts_id: The GTS ID of the object (used to find the schema)
        """
        gid = GtsID(gts_id)
        obj = self.get(gid.id)
        if not obj:
            raise StoreGtsObjectNotFound(gts_id)
        if not obj.schemaId:
            raise StoreGtsSchemaForInstanceNotFound(gid.id)
        try:
            schema = self.get_schema_content(obj.schemaId)
        except KeyError:
            raise StoreGtsSchemaNotFound(obj.schemaId)

        logging.info(f"Validating instance {gts_id} against schema {obj.schemaId}")
        js_validate(instance=obj.content, schema=schema)

    def cast(
        self,
        instance_id: str,
        target_schema_id: str,
    ) -> JsonEntityCastResult:
        from_entity = self.get(instance_id)
        if not from_entity:
            raise StoreGtsObjectNotFound(instance_id)

        to_schema = self.get(target_schema_id)
        if not to_schema:
            raise StoreGtsObjectNotFound(target_schema_id)

        return from_entity.cast(to_schema)

    def is_minor_compatible(
        self,
        old_schema_id: str,
        new_schema_id: str,
    ) -> bool:
        """
        Check if two schemas are fully compatible under strict rules.

        Args:
            old_schema_id: ID of the old schema
            new_schema_id: ID of the new schema

        Returns:
            True if only optional fields were added or removed; False otherwise
        """
        old_entity = self.get(old_schema_id)
        new_entity = self.get(new_schema_id)

        if not old_entity or not new_entity:
            return False

        old_schema = old_entity.content if isinstance(old_entity.content, dict) else {}
        new_schema = new_entity.content if isinstance(new_entity.content, dict) else {}

        old_props = old_schema.get("properties", {})
        new_props = new_schema.get("properties", {})
        old_req = set(old_schema.get("required", []))
        new_req = set(new_schema.get("required", []))

        # No changes to required fields allowed
        if old_req != new_req:
            return False

        # For properties present in both versions, their definitions must be identical
        common_keys = set(old_props.keys()) & set(new_props.keys())
        for k in common_keys:
            if old_props.get(k) != new_props.get(k):
                return False

        # Properties can be added or removed (these are optional by definition here since required did not change)
        # Any schema-level changes outside properties/required are ignored in this simplified check
        return True

    def build_schema_graph(self, gts_id: str) -> Tuple[Dict[str, Set[str]], List[str]]:
        seen_gts_ids = set()

        def gts2node(gts_id: str, seen_gts_ids: Set[str]) -> str:
            ret = {
                "id": gts_id
            }

            if gts_id in seen_gts_ids:
                return ret

            seen_gts_ids.add(gts_id)

            entity = self.get(gts_id)
            if entity:
                refs = {}
                for r in entity.gts_refs:
                    if r["id"] == gts_id:
                        continue
                    if r["id"].startswith("http://json-schema.org") or r["id"].startswith("https://json-schema.org"):
                        continue
                    refs[r["sourcePath"]] = gts2node(r["id"], seen)
                if refs:
                    ret["refs"] = refs
                if entity.schemaId:
                    if not entity.schemaId.startswith("http://json-schema.org") and not entity.schemaId.startswith("https://json-schema.org"):
                        ret["schema_id"] = gts2node(entity.schemaId, seen)
                else:
                    ret["errors"] = ret.get("errors", []) + ["Schema not recognized"]
            else:
                ret["errors"] = ret.get("errors", []) + ["Entity not found"]

            return ret

        return gts2node(gts_id, seen_gts_ids)

    def query(self, expr: str) -> List[Dict[str, Any]]:
        """Filter entities by a GTS query expression.

        Uses each entity's detected GTS ID field (selected_entity_field) with a
        fallback to 'gtsId'. Returns a list of matching entity contents.
        """
        results: List[Dict[str, Any]] = []
        for entity in self._by_id.values():
            if isinstance(entity.content, dict) and entity.gts_id:
                gts_field = entity.selected_entity_field or "gtsId"
                if entity.gts_id.match_query(entity.content, gts_field, expr):
                    results.append(entity.content)
        return results
