from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Set, Tuple, List, Any, Optional, Iterator
import uuid

from jsonschema import RefResolver
from jsonschema.validators import validator_for
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from .gts import GtsID, GtsWildcard
from .entities import GtsEntity
from .schema_cast import GtsEntityCastResult
from .x_gts_ref import XGtsRefValidator, _without_x_gts_ref
from . import compatibility
from . import derivation
from . import traits

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
        super().__init__(
            f"Can't determine JSON schema ID for instance with GTS ID '{entity_id}'"
        )
        self.entity_id = entity_id


class StoreGtsCastFromSchemaNotAllowed(Exception):
    """Exception raised when attempting to cast from a schema ID."""

    def __init__(self, from_id: str):
        super().__init__(
            f"Cannot cast from schema ID '{from_id}'. "
            f"The from_id must be an instance (not ending with '~')."
        )
        self.from_id = from_id


class GtsReader(ABC):
    """Abstract base class for reading JSON entities from various sources."""

    @abstractmethod
    def __iter__(self) -> Iterator[GtsEntity]:
        """Return an iterator that yields JsonEntity objects."""
        pass

    @abstractmethod
    def read_by_id(self, entity_id: str) -> Optional[GtsEntity]:
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


class GtsStoreQueryResult:
    def __init__(self):
        self.error = ""
        self.count = 0
        self.limit = 0
        self.results: List[Dict[str, Any]] = []

    def to_dict(self) -> Dict[str, Any]:
        if self.error:
            return {"error": self.error, "count": self.count, "limit": self.limit}
        return {
            "count": self.count,
            "limit": self.limit,
            "error": self.error,
            "results": self.results,
        }


class GtsStore:
    def __init__(self, reader: GtsReader) -> None:
        """
        Initialize GtsStore with an optional GtsReader.

        Args:
            reader: GtsReader instance to populate entities from
        """
        self._by_id: Dict[str, GtsEntity] = {}
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

    def register(self, entity: GtsEntity) -> None:
        """Register a GtsEntity in the store.

        If entity has a valid gts_id, use that as the key.
        Otherwise, use raw_id for non-GTS entities.
        """
        # Instances should remain addressable by the id value they carry.
        # For plain UUID anonymous instances, `gts_id` may be inferred from
        # the `type` field while `raw_id` is the UUID we must look up by.
        if not entity.is_schema and entity.raw_id:
            self._by_id[entity.raw_id] = entity
            return

        if entity.gts_id and entity.gts_id.id:
            self._by_id[entity.gts_id.id] = entity
        elif entity.raw_id:
            # Allow non-GTS entities with raw_id (e.g., UUIDs or simple strings)
            self._by_id[entity.raw_id] = entity
        else:
            raise ValueError("Entity must have a valid gts_id or raw_id")

    def unregister(self, entity_id: str) -> None:
        """Remove an entity from the in-memory registry if it is present."""
        self._by_id.pop(entity_id, None)

    def register_schema(self, type_id: str, schema: Dict[str, Any]) -> None:
        """
        Register a schema (legacy method for backward compatibility).
        Creates a JsonEntity from the schema dict.
        """
        if not type_id.endswith("~"):
            raise ValueError("Schema type_id must end with '~'")
        # parse sanity
        gts_id = GtsID(type_id)
        entity = GtsEntity(content=schema, gts_id=gts_id, is_schema=True)
        self._by_id[type_id] = entity

    def get(self, entity_id: str) -> Optional[GtsEntity]:
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
            # Issue #32: handle gts:// prefix
            if uri.startswith("gts://"):
                uri = uri[6:]
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
        # Issue #32: Support "gts" scheme
        handlers = {"": resolve_gts_ref, "gts": resolve_gts_ref}
        resolver = RefResolver.from_schema(schema, store=store, handlers=handlers)
        return resolver

    def _create_reference_registry(self) -> Registry:
        registry = Registry()
        for entity_id, entity in self._by_id.items():
            if entity.is_schema and isinstance(entity.content, dict):
                resource = Resource.from_contents(
                    _without_x_gts_ref(entity.content),
                    default_specification=DRAFT202012,
                )
                registry = registry.with_resource(f"gts://{entity_id}", resource)
        return registry

    def items(self):
        """Return all entity ID and entity pairs."""
        return self._by_id.items()

    @staticmethod
    def _validate_schema_refs(schema: Dict[str, Any], path: str = "") -> None:
        """
        Validate all $ref values in a schema.

        Rules:
        - Local refs (starting with #) are always valid
        - External refs MUST use gts:// URI format
        - The GTS ID after gts:// must be a valid GTS identifier

        Args:
            schema: Schema content to validate
            path: Current path in schema (for error messages)

        Raises:
            ValueError: If any $ref is invalid
        """
        if isinstance(schema, dict):
            # Check $ref if present
            if "$ref" in schema:
                ref_uri = schema["$ref"]
                if isinstance(ref_uri, str):
                    current_path = f"{path}.$ref" if path else "$ref"

                    # Local refs (JSON Pointer) are always valid
                    if ref_uri.startswith("#"):
                        pass  # Valid local ref
                    # GTS refs must use gts:// URI format
                    elif ref_uri.startswith("gts://"):
                        gts_id = ref_uri[6:]  # Strip prefix
                        # Validate the GTS ID
                        if not GtsID.is_valid(gts_id):
                            raise ValueError(
                                f"Invalid $ref at '{current_path}': '{ref_uri}' contains invalid GTS identifier '{gts_id}'"
                            )
                    # Any other external ref is invalid
                    else:
                        raise ValueError(
                            f"Invalid $ref at '{current_path}': '{ref_uri}' must be a local ref (starting with '#') "
                            f"or a GTS URI (starting with 'gts://')"
                        )

            # Recursively validate nested objects
            for key, value in schema.items():
                if key == "$ref":
                    continue  # Already validated above
                nested_path = f"{path}.{key}" if path else key
                GtsStore._validate_schema_refs(value, nested_path)

        elif isinstance(schema, list):
            for idx, item in enumerate(schema):
                nested_path = f"{path}[{idx}]"
                GtsStore._validate_schema_refs(item, nested_path)

    def _validate_schema_x_gts_refs(self, gts_id: str) -> None:
        """
        Validate a schema's x-gts-ref fields.

        Args:
            gts_id: The GTS ID of the schema to validate
        """
        if not gts_id.endswith("~"):
            raise ValueError(f"ID '{gts_id}' is not a schema (must end with '~')")

        schema_entity = self.get(gts_id)
        if not schema_entity:
            raise StoreGtsSchemaNotFound(gts_id)

        if not schema_entity.is_schema:
            raise ValueError(f"Entity '{gts_id}' is not a schema")

        logging.info(f"Validating schema x-gts-ref fields for {gts_id}")

        # Validate x-gts-ref constraints in the schema
        x_gts_ref_validator = XGtsRefValidator(store=self)
        x_gts_ref_errors = x_gts_ref_validator.validate_schema(schema_entity.content)
        if x_gts_ref_errors:
            error_messages = [
                f"{err.field_path}: {err.reason}" for err in x_gts_ref_errors
            ]
            raise Exception(
                f"Schema x-gts-ref validation failed: {'; '.join(error_messages)}"
            )

    @staticmethod
    def _validate_gts_keywords(content: Dict[str, Any]) -> None:
        """Validate x-gts-final, x-gts-abstract, x-gts-traits, x-gts-traits-schema placement."""

        def _contains_key_recursive(value: Any, key: str) -> bool:
            if isinstance(value, dict):
                if key in value:
                    return True
                return any(_contains_key_recursive(v, key) for v in value.values())
            elif isinstance(value, list):
                return any(_contains_key_recursive(v, key) for v in value)
            return False

        # Validate x-gts-final
        final_val = content.get("x-gts-final")
        if final_val is not None:
            if not isinstance(final_val, bool):
                raise ValueError(
                    f"x-gts-final must be a boolean, got {type(final_val).__name__}"
                )

        # Validate x-gts-abstract
        abstract_val = content.get("x-gts-abstract")
        if abstract_val is not None:
            if not isinstance(abstract_val, bool):
                raise ValueError(
                    f"x-gts-abstract must be a boolean, got {type(abstract_val).__name__}"
                )

        # Mutual exclusion
        if final_val is True and abstract_val is True:
            raise ValueError(
                "schema cannot declare both x-gts-final and x-gts-abstract as true"
            )

        # Check that x-gts-final/x-gts-abstract/x-gts-traits/x-gts-traits-schema
        # appear only at the top level
        top_level_keywords = {
            "x-gts-final",
            "x-gts-abstract",
            "x-gts-traits",
            "x-gts-traits-schema",
        }
        for key, value in content.items():
            if key in top_level_keywords:
                continue
            for kw in top_level_keywords:
                if _contains_key_recursive(value, kw):
                    raise ValueError(f"{kw} must be at the schema top level")

    @staticmethod
    def _content_is_abstract(content: Dict[str, Any]) -> bool:
        return content.get("x-gts-abstract") is True

    @staticmethod
    def _content_is_final(content: Dict[str, Any]) -> bool:
        return content.get("x-gts-final") is True

    def _validate_schema_chain(self, gts_id: str) -> None:
        """Validate OP#12: schema derivation chain compatibility."""
        gid = GtsID(gts_id)
        segments = gid.gts_id_segments

        # Single-segment schemas have no parent to validate against
        if len(segments) < 2:
            return

        # Build chain IDs
        chain_ids = []
        prefix = "gts."
        for seg in segments:
            chain_ids.append(prefix + seg.segment)
            prefix = prefix + seg.segment

        # Validate each adjacent pair
        for i in range(len(chain_ids) - 1):
            base_id = chain_ids[i]
            derived_id = chain_ids[i + 1]

            base_entity = self.get(base_id)
            derived_entity = self.get(derived_id)

            # Check x-gts-final: if the base type is final, derivation is not allowed.
            if base_entity and isinstance(base_entity.content, dict):
                if self._content_is_final(base_entity.content):
                    raise ValueError(
                        f"base type '{base_id}' is final and cannot be extended"
                    )

            logging.info(
                f"OP#12: Validating schema chain pair: base={base_id} derived={derived_id}"
            )

            if not base_entity or not isinstance(base_entity.content, dict):
                raise ValueError(
                    f"Base schema '{base_id}' not found for chain validation"
                )
            if not derived_entity or not isinstance(derived_entity.content, dict):
                raise ValueError(
                    f"Derived schema '{derived_id}' not found for chain validation"
                )

            # Resolve both schemas (inline $refs)
            base_resolved = self._resolve_schema_refs(base_entity.content)
            derived_resolved = self._resolve_schema_refs(derived_entity.content)

            # Validate derivation compatibility (OP#12): accepted-instance-set
            # inclusion on declared schemas plus GTS admission rules.
            errors = derivation.validate_derivation_compatibility(
                base_resolved, derived_resolved, base_id, derived_id
            )
            if errors:
                raise ValueError(
                    f"Schema '{derived_id}' is not compatible with base '{base_id}': "
                    + "; ".join(errors)
                )

    def _resolve_schema_refs(self, schema: Any) -> Any:
        """Resolve $ref references in a schema by inlining referenced schemas.

        References are inlined recursively so that a schema reached through an
        intermediate (A referenced via A~B) is fully expanded. Cyclic
        references are left unresolved: the surviving $ref makes the effective
        schema unprovable, which is the intended admission failure.
        """
        import copy

        return self._inline_refs(
            copy.deepcopy(schema), set(), self._supports_ref_siblings(schema)
        )

    @staticmethod
    def _supports_ref_siblings(schema: Any) -> bool:
        dialect = schema.get("$schema") if isinstance(schema, dict) else None
        return isinstance(dialect, str) and (
            "/draft/2019-09/" in dialect or "/draft/2020-12/" in dialect
        )

    def _inline_refs(
        self, node: Any, seen: Set[str], supports_ref_siblings: bool
    ) -> Any:
        """Recursively inline $ref references, guarding against cycles."""
        if isinstance(node, dict):
            ref_uri = node.get("$ref")
            if isinstance(ref_uri, str):
                ref_id: Optional[str] = None
                if ref_uri.startswith("gts://"):
                    ref_id = ref_uri[6:]
                elif not ref_uri.startswith("#"):
                    ref_id = ref_uri
                if ref_id is not None:
                    if ref_id in seen:
                        # Cycle detected: leave the $ref unresolved.
                        return node
                    try:
                        ref_schema = self.get_schema_content(ref_id)
                    except KeyError:
                        return node  # Leave unresolved
                    import copy

                    resolved = self._inline_refs(
                        copy.deepcopy(ref_schema),
                        seen | {ref_id},
                        self._supports_ref_siblings(ref_schema),
                    )
                    if supports_ref_siblings and len(node) > 1:
                        siblings = {
                            key: value for key, value in node.items() if key != "$ref"
                        }
                        return {
                            "allOf": [
                                resolved,
                                self._inline_refs(
                                    siblings, seen, supports_ref_siblings
                                ),
                            ]
                        }
                    return resolved
            return {
                key: self._inline_refs(value, seen, supports_ref_siblings)
                for key, value in node.items()
            }
        if isinstance(node, list):
            return [
                self._inline_refs(item, seen, supports_ref_siblings) for item in node
            ]
        return node

    def _build_effective_traits(self, gts_id: str) -> "traits.EffectiveTraits":
        """Build OP#13 EffectiveTraits by walking the type's chain (root -> leaf)."""
        gid = GtsID(gts_id)
        segments = gid.gts_id_segments

        chain_ids: List[str] = []
        prefix = "gts."
        for seg in segments:
            chain_ids.append(prefix + seg.segment)
            prefix = prefix + seg.segment

        trait_schemas: List[Any] = []
        merged_traits: Dict[str, Any] = {}

        for schema_id in chain_ids:
            entity = self.get(schema_id)
            if not entity or not isinstance(entity.content, dict):
                continue
            content = entity.content

            level_schemas: List[Any] = []
            traits.collect_trait_schema_from_value(content, level_schemas)
            for ts in level_schemas:
                # Inline local JSON Pointer refs against the host document, then
                # resolve any gts:// refs so the composed schema is self-contained.
                inlined = traits.inline_local_pointers(ts, content)
                trait_schemas.append(self._resolve_schema_refs(inlined))

            level_traits: Dict[str, Any] = {}
            traits.collect_traits_from_value(content, level_traits)
            traits.merge_rfc7396_into(merged_traits, level_traits)

        leaf = self.get(chain_ids[-1]) if chain_ids else None
        dialect = None
        if leaf and isinstance(leaf.content, dict):
            ds = leaf.content.get("$schema")
            if isinstance(ds, str):
                dialect = ds

        return traits.build_effective_traits(trait_schemas, merged_traits, dialect)

    def _validate_traits(self, gts_id: str, is_abstract: bool) -> None:
        """Validate OP#13: schema traits for a type."""
        effective = self._build_effective_traits(gts_id)
        errors = effective.validate(check_unresolved=not is_abstract)
        if errors:
            raise ValueError(
                f"Schema '{gts_id}' trait validation failed: " + "; ".join(errors)
            )

    def validate_schema_basic(self, gts_id: str) -> None:
        """Basic schema validation during registration (no chain validation).

        Checks:
        1. $ref URI format
        2. x-gts-ref field validation
        3. GTS keyword validation (x-gts-final, x-gts-abstract, placement)
        4. JSON Schema meta-schema validation
        """
        if not gts_id.endswith("~"):
            raise ValueError(f"ID '{gts_id}' is not a schema (must end with '~')")

        schema_entity = self.get(gts_id)
        if not schema_entity:
            raise StoreGtsSchemaNotFound(gts_id)

        if not schema_entity.is_schema:
            raise ValueError(f"Entity '{gts_id}' is not a schema")

        schema_content = schema_entity.content
        if not isinstance(schema_content, dict):
            raise ValueError(f"Schema '{gts_id}' content must be a dictionary")

        meta_schema_url = schema_content.get("$schema")
        if meta_schema_url and isinstance(meta_schema_url, str):
            if meta_schema_url.startswith("gts.") or meta_schema_url.startswith(
                "gts://"
            ):
                raise ValueError(
                    f"Invalid $schema URL '{meta_schema_url}': must be a standard JSON Schema URL, not a GTS ID"
                )

        # 1. Validate $ref fields
        self._validate_schema_refs(schema_content, "")

        # 2. Validate x-gts-ref fields
        self._validate_schema_x_gts_refs(gts_id)

        # 3. Validate GTS keywords (x-gts-final, x-gts-abstract, placement)
        self._validate_gts_keywords(schema_content)

    def validate_schema(self, gts_id: str) -> None:
        """
        Full schema validation including:
        1. JSON Schema meta-schema validation
        2. x-gts-ref field validation
        3. GTS keyword validation (x-gts-final, x-gts-abstract, placement)
        4. Schema chain derivation validation (OP#12)

        Args:
            gts_id: The GTS ID of the schema to validate
        """
        if not gts_id.endswith("~"):
            raise ValueError(f"ID '{gts_id}' is not a schema (must end with '~')")

        schema_entity = self.get(gts_id)
        if not schema_entity:
            raise StoreGtsSchemaNotFound(gts_id)

        if not schema_entity.is_schema:
            raise ValueError(f"Entity '{gts_id}' is not a schema")

        schema_content = schema_entity.content
        if not isinstance(schema_content, dict):
            raise ValueError(f"Schema '{gts_id}' content must be a dictionary")

        # Issue #25: strict check, no GTS IDs in $schema
        meta_schema_url = schema_content.get("$schema")
        if meta_schema_url and isinstance(meta_schema_url, str):
            if meta_schema_url.startswith("gts.") or meta_schema_url.startswith(
                "gts://"
            ):
                raise ValueError(
                    f"Invalid $schema URL '{meta_schema_url}': must be a standard JSON Schema URL, not a GTS ID"
                )

        logging.info(f"Validating schema {gts_id}")

        # 1. Validate $ref fields - must be local (#...) or gts:// URIs
        self._validate_schema_refs(schema_content, "")

        # 2. Validate x-gts-ref fields
        self._validate_schema_x_gts_refs(gts_id)

        # 3. Validate GTS keywords (x-gts-final, x-gts-abstract, placement)
        self._validate_gts_keywords(schema_content)

        # 4. Validate schema derivation chain (OP#12)
        self._validate_schema_chain(gts_id)

        # 5. Validate against JSON Schema meta-schema
        try:
            from jsonschema import Draft7Validator
            from jsonschema.validators import validator_for

            if meta_schema_url:
                validator_class = validator_for({"$schema": meta_schema_url})
                validator_class.check_schema(schema_content)
            else:
                Draft7Validator.check_schema(schema_content)

            logging.info(f"Schema {gts_id} passed JSON Schema meta-schema validation")
        except Exception as e:
            raise Exception(f"JSON Schema validation failed for '{gts_id}': {str(e)}")

        # 6. Validate traits (OP#13)
        is_abstract = self._content_is_abstract(schema_content)
        self._validate_traits(gts_id, is_abstract)

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
        obj = None
        # Well-known and combined-anonymous IDs are valid GTS IDs.
        if GtsID.is_valid(gts_id):
            gid = GtsID(gts_id)
            obj = self.get(gid.id)
            lookup_id = gid.id
        else:
            # Anonymous instance ID path: allow plain UUID and resolve by raw id.
            try:
                _ = uuid.UUID(gts_id)
            except Exception:
                raise StoreGtsObjectNotFound(gts_id)
            obj = self.get(gts_id)
            lookup_id = gts_id

        if not obj:
            raise StoreGtsObjectNotFound(gts_id)
        if not obj.type_id:
            raise StoreGtsSchemaForInstanceNotFound(lookup_id)
        try:
            schema = self.get_schema_content(obj.type_id)
        except KeyError:
            raise StoreGtsSchemaNotFound(obj.type_id)

        logging.info(f"Validating instance {gts_id} against schema {obj.type_id}")

        # Check if the schema is abstract - abstract types cannot have direct instances
        if isinstance(schema, dict) and self._content_is_abstract(schema):
            raise ValueError(
                f"type '{obj.type_id}' is abstract and cannot have direct instances"
            )

        schema_for_validation = _without_x_gts_ref(schema)
        validator_class = validator_for(schema_for_validation)
        validator = validator_class(
            schema_for_validation, registry=self._create_reference_registry()
        )
        validator.validate(obj.content)

        # Validate x-gts-ref constraints against the ref-resolved schema.
        x_gts_ref_validator = XGtsRefValidator(store=self)
        x_gts_ref_errors = x_gts_ref_validator.validate_instance(
            obj.content, self._resolve_schema_refs(schema)
        )
        if x_gts_ref_errors:
            error_messages = [
                f"{err.field_path}: {err.reason}" for err in x_gts_ref_errors
            ]
            raise Exception(f"x-gts-ref validation failed: {'; '.join(error_messages)}")

    def cast(
        self,
        from_id: str,
        target_schema_id: str,
    ) -> GtsEntityCastResult:
        from_entity = self.get(from_id)
        if not from_entity:
            raise StoreGtsEntityNotFound(from_id)

        if from_entity.is_schema:
            raise StoreGtsCastFromSchemaNotAllowed(from_id)

        to_schema = self.get(target_schema_id)
        if not to_schema:
            raise StoreGtsObjectNotFound(target_schema_id)

        # Get the source schema
        if from_entity.is_schema:
            from_schema = from_entity
            from_schema_id = from_entity.gts_id.id
        else:
            from_schema_id = from_entity.type_id
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
    ) -> GtsEntityCastResult:
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
            return GtsEntityCastResult(
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

        # Compatibility follows accepted-instance-set inclusion on the effective
        # (ref-resolved) schemas. Resolve $ref first so the verdict reflects the
        # referenced targets (spec sec 4.3).
        old_resolved = self._resolve_schema_refs(old_schema)
        new_resolved = self._resolve_schema_refs(new_schema)

        backward = compatibility.check_backward_compatibility(
            old_resolved, new_resolved
        )
        forward = compatibility.check_forward_compatibility(old_resolved, new_resolved)
        full = compatibility.full_verdict(backward, forward)

        # Determine direction
        direction = GtsEntityCastResult._infer_direction(old_schema_id, new_schema_id)

        return GtsEntityCastResult(
            from_id=old_schema_id,
            to_id=new_schema_id,
            direction=direction,
            added_properties=[],
            removed_properties=[],
            changed_properties=[],
            is_fully_compatible=full == compatibility.COMPATIBLE,
            is_backward_compatible=backward == compatibility.COMPATIBLE,
            is_forward_compatible=forward == compatibility.COMPATIBLE,
            incompatibility_reasons=[],
            backward_errors=[],
            forward_errors=[],
            casted_entity=None,
            backward_verdict=backward,
            forward_verdict=forward,
            full_verdict=full,
        )

    def build_schema_graph(self, gts_id: str) -> Tuple[Dict[str, Set[str]], List[str]]:
        seen_gts_ids = set()

        def gts2node(gts_id: str, seen_gts_ids: Set[str]) -> str:
            ret = {"id": gts_id}

            if gts_id in seen_gts_ids:
                return ret

            seen_gts_ids.add(gts_id)

            entity = self.get(gts_id)
            if entity:
                refs = {}
                for r in entity.gts_refs:
                    if r["id"] == gts_id:
                        continue
                    if r["id"].startswith("http://json-schema.org") or r[
                        "id"
                    ].startswith("https://json-schema.org"):
                        continue
                    refs[r["sourcePath"]] = gts2node(r["id"], seen_gts_ids)
                if refs:
                    ret["refs"] = refs
                if entity.type_id:
                    if not entity.type_id.startswith(
                        "http://json-schema.org"
                    ) and not entity.type_id.startswith("https://json-schema.org"):
                        ret["schema_id"] = gts2node(entity.type_id, seen_gts_ids)
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

    def _validate_query_pattern(
        self, base_pattern: str, is_wildcard: bool
    ) -> Tuple[Optional[GtsWildcard], Optional[GtsID], str]:
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
                return (
                    None,
                    None,
                    "Invalid query: wildcard patterns must end with .* or ~*",
                )
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

    def _matches_id_pattern(
        self,
        entity_id: GtsID,
        base_pattern: str,
        is_wildcard: bool,
        wildcard_pattern: Optional[GtsWildcard],
        exact_gts_id: Optional[GtsID],
    ) -> bool:
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
            matched = entity_id.wildcard_match(wildcard_pattern)
            if not matched:
                return False
            if base_pattern.endswith("~*"):
                base_depth = max(0, len(wildcard_pattern.gts_id_segments) - 1)
                if len(entity_id.gts_id_segments) <= base_depth:
                    return False
            return True

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

    def _matches_filters(
        self, entity_content: Dict[str, Any], filters: Dict[str, str]
    ) -> bool:
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
        wildcard_pattern, exact_gts_id, error = self._validate_query_pattern(
            base_pattern, is_wildcard
        )
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
            if not self._matches_id_pattern(
                entity.gts_id, base_pattern, is_wildcard, wildcard_pattern, exact_gts_id
            ):
                continue

            # Check filters
            if not self._matches_filters(entity.content, filters):
                continue

            result.results.append(entity.content)

        result.count = len(result.results)
        return result
