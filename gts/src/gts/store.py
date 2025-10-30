from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Set, Tuple, List, Any, Optional, Iterator

from jsonschema import validate as js_validate
from jsonschema import RefResolver

from .gts import GtsID, GtsWildcard
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


class StoreGtsEntityNotFound(Exception):
    """Exception raised when a GTS entity is not found in the store."""
    def __init__(self, entity_id: str):
        super().__init__(f"JSON entity with GTS ID '{entity_id}' not found in store")
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


class GtsStoreQueryResultEntry:
    def __init__(self):
        self.id = ""
        self.schema_id = ""
        self.is_schema = bool


class GtsStoreQueryResult:
    def __init__(self):
        self.error = ""
        self.count = 0
        self.limit = 0
        self.results: List[Dict[str, Any]] = []

    def to_dict(self) -> Dict[str, Any]:
        if self.error:
            return {"error": self.error, "count": self.count, "limit": self.limit}
        return {"count": self.count, "limit": self.limit, "error": self.error, "results": self.results}


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

    def _create_ref_resolver(self, schema: Dict[str, Any]) -> RefResolver:
        """Create a custom RefResolver that can resolve GTS ID references from the store."""
        def resolve_gts_ref(uri: str) -> Dict[str, Any]:
            """Resolve a GTS ID reference to its schema content."""
            try:
                return self.get_schema_content(uri)
            except KeyError:
                raise Exception(f"Unresolvable: {uri}")

        # Create a store dict that maps GTS IDs to their schema content
        store = {}
        for entity_id, entity in self._by_id.items():
            if entity.is_schema and isinstance(entity.content, dict):
                store[entity_id] = entity.content

        # Create RefResolver with custom handlers
        resolver = RefResolver.from_schema(schema, store=store, handlers={"": resolve_gts_ref})
        return resolver

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

        # Create custom RefResolver to resolve GTS ID references
        resolver = self._create_ref_resolver(schema)
        js_validate(instance=obj.content, schema=schema, resolver=resolver)

    def cast(
        self,
        from_id: str,
        target_schema_id: str,
    ) -> JsonEntityCastResult:
        from_entity = self.get(from_id)
        if not from_entity:
            raise StoreGtsEntityNotFound(from_id)

        to_schema = self.get(target_schema_id)
        if not to_schema:
            raise StoreGtsObjectNotFound(target_schema_id)

        # Get the source schema
        if from_entity.is_schema:
            from_schema = from_entity
            from_schema_id = from_entity.gts_id.id
        else:
            from_schema_id = from_entity.schemaId
            if not from_schema_id:
                raise StoreGtsSchemaForInstanceNotFound(from_id)
            from_schema = self.get(from_schema_id)
            if not from_schema:
                raise StoreGtsObjectNotFound(from_schema_id)

        # Create a resolver to handle $ref in schemas
        resolver = self._create_ref_resolver(to_schema.content)

        return from_entity.cast(to_schema, from_schema, resolver=resolver)

    def is_minor_compatible(
        self,
        old_schema_id: str,
        new_schema_id: str,
    ) -> JsonEntityCastResult:
        """
        Check compatibility between two schemas.

        Args:
            old_schema_id: ID of the old schema
            new_schema_id: ID of the new schema

        Returns:
            JsonEntityCastResult with backward, forward, and full compatibility flags
        """
        old_entity = self.get(old_schema_id)
        new_entity = self.get(new_schema_id)

        if not old_entity or not new_entity:
            return JsonEntityCastResult(
                from_id=old_schema_id,
                to_id=new_schema_id,
                direction="unknown",
                added_properties=[],
                removed_properties=[],
                changed_properties=[],
                is_fully_compatible=False,
                is_backward_compatible=False,
                is_forward_compatible=False,
                incompatibility_reasons=["Schema not found"],
                backward_errors=["Schema not found"],
                forward_errors=["Schema not found"],
                casted_entity=None,
            )

        old_schema = old_entity.content if isinstance(old_entity.content, dict) else {}
        new_schema = new_entity.content if isinstance(new_entity.content, dict) else {}

        # Use the cast method's compatibility checking logic
        is_backward, backward_errors = JsonEntityCastResult._check_backward_compatibility(
            old_schema, new_schema
        )
        is_forward, forward_errors = JsonEntityCastResult._check_forward_compatibility(
            old_schema, new_schema
        )

        # Determine direction
        direction = JsonEntityCastResult._infer_direction(old_schema_id, new_schema_id)

        return JsonEntityCastResult(
            from_id=old_schema_id,
            to_id=new_schema_id,
            direction=direction,
            added_properties=[],
            removed_properties=[],
            changed_properties=[],
            is_fully_compatible=is_backward and is_forward,
            is_backward_compatible=is_backward,
            is_forward_compatible=is_forward,
            incompatibility_reasons=[],
            backward_errors=backward_errors,
            forward_errors=forward_errors,
            casted_entity=None,
        )

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
                    refs[r["sourcePath"]] = gts2node(r["id"], seen_gts_ids)
                if refs:
                    ret["refs"] = refs
                if entity.schemaId:
                    if not entity.schemaId.startswith("http://json-schema.org") and not entity.schemaId.startswith("https://json-schema.org"):
                        ret["schema_id"] = gts2node(entity.schemaId, seen_gts_ids)
                else:
                    ret["errors"] = ret.get("errors", []) + ["Schema not recognized"]
            else:
                ret["errors"] = ret.get("errors", []) + ["Entity not found"]

            return ret

        return gts2node(gts_id, seen_gts_ids)

    def _parse_query_filters(self, filter_str: str) -> Dict[str, str]:
        """Parse filter expressions from query string.

        Args:
            filter_str: Filter string like 'status=active, category=order'

        Returns:
            Dictionary of filter key-value pairs
        """
        filters: Dict[str, str] = {}
        if not filter_str:
            return filters

        # Split by comma to handle multiple filters
        parts = [p.strip() for p in filter_str.split(",")]
        for part in parts:
            if "=" in part:
                k, v = part.split("=", 1)
                # Remove quotes from value if present
                v = v.strip().strip('"').strip("'")
                filters[k.strip()] = v
        return filters

    def _validate_query_pattern(self, base_pattern: str, is_wildcard: bool) -> Tuple[Optional[GtsWildcard], Optional[GtsID], str]:
        """Validate and parse the query pattern.

        Args:
            base_pattern: The base GTS ID pattern
            is_wildcard: Whether the pattern contains wildcards

        Returns:
            Tuple of (wildcard_pattern, exact_gts_id, error_message)
        """
        if is_wildcard:
            # Wildcard pattern must end with .* or ~*
            if not (base_pattern.endswith(".*") or base_pattern.endswith("~*")):
                return None, None, "Invalid query: wildcard patterns must end with .* or ~*"
            try:
                wildcard_pattern = GtsWildcard(base_pattern)
                return wildcard_pattern, None, ""
            except Exception as e:
                return None, None, f"Invalid query: {str(e)}"
        else:
            # Non-wildcard pattern must be a complete valid GTS ID
            try:
                exact_gts_id = GtsID(base_pattern)
                if not exact_gts_id.gts_id_segments:
                    return None, None, "Invalid query: GTS ID has no valid segments"
                return None, exact_gts_id, ""
            except Exception as e:
                return None, None, f"Invalid query: {str(e)}"

    def _matches_id_pattern(self, entity_id: GtsID, base_pattern: str, is_wildcard: bool,
                           wildcard_pattern: Optional[GtsWildcard], exact_gts_id: Optional[GtsID]) -> bool:
        """Check if entity ID matches the query pattern.

        Args:
            entity_id: The entity's GTS ID
            base_pattern: The base pattern string
            is_wildcard: Whether pattern is a wildcard
            wildcard_pattern: Parsed wildcard pattern (if applicable)
            exact_gts_id: Parsed exact GTS ID (if applicable)

        Returns:
            True if entity ID matches the pattern
        """
        if is_wildcard and wildcard_pattern:
            return entity_id.wildcard_match(wildcard_pattern)

        # For non-wildcard patterns, use wildcard_match to support version flexibility
        # This allows patterns like "gts.x.test.v1~" to match "gts.x.test.v1.0~"
        if exact_gts_id:
            try:
                pattern_as_wildcard = GtsWildcard(base_pattern)
                return entity_id.wildcard_match(pattern_as_wildcard)
            except Exception:
                # If it can't be converted to wildcard, fall back to exact match
                return entity_id.id == base_pattern

        return entity_id.id == base_pattern

    def _matches_filters(self, entity_content: Dict[str, Any], filters: Dict[str, str]) -> bool:
        """Check if entity content matches all filter criteria.

        Args:
            entity_content: The entity's content dictionary
            filters: Dictionary of filter key-value pairs

        Returns:
            True if all filters match
        """
        if not filters:
            return True

        for key, value in filters.items():
            entity_value = str(entity_content.get(key, ""))
            # Support wildcard in filter values
            if value == "*":
                # Wildcard matches any non-empty value
                if not entity_value or entity_value == "None":
                    return False
            elif entity_value != value:
                return False
        return True

    def query(self, expr: str, limit: int = 100) -> GtsStoreQueryResult:
        """Filter entities by a GTS query expression.

        Supports:
        - Exact match: "gts.x.core.events.event.v1~"
        - Wildcard match: "gts.x.core.events.*"
        - With filters: "gts.x.core.events.event.v1~[status=active]"
        - Wildcard with filters: "gts.x.core.*[status=active]"
        - Wildcard filter values: "gts.x.core.*[status=active, category=*]"

        Uses each entity's detected GTS ID field (selected_entity_field) with a
        fallback to 'gtsId'. Returns a list of matching entity contents or error dict.
        """
        result = GtsStoreQueryResult()
        result.limit = limit

        # Parse the query expression to extract base pattern and filters
        base, _, filt = expr.partition("[")
        base_pattern = base.strip()
        is_wildcard = "*" in base_pattern

        # Parse filters if present
        filter_str = filt.rsplit("]", 1)[0] if filt else ""
        filters = self._parse_query_filters(filter_str)

        # Validate and create pattern
        wildcard_pattern, exact_gts_id, error = self._validate_query_pattern(base_pattern, is_wildcard)
        if error:
            result.error = error
            return result

        # Filter entities
        for entity in self._by_id.values():
            if len(result.results) >= limit:
                break
            if not isinstance(entity.content, dict) or not entity.gts_id:
                continue

            # Check if ID matches the pattern
            if not self._matches_id_pattern(entity.gts_id, base_pattern, is_wildcard,
                                           wildcard_pattern, exact_gts_id):
                continue

            # Check filters
            if not self._matches_filters(entity.content, filters):
                continue

            result.results.append(entity.content)

        result.count = len(result.results)
        return result
