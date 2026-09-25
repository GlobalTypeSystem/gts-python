from __future__ import annotations

import copy
import logging
import threading
import uuid
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from . import compatibility, derivation, schema_dialect, schema_resolver, traits
from ._errors import (
    GtsNotFoundError,
    GtsUnresolvedRefError,
    GtsValidationError,
)
from ._json_pointer import resolve as resolve_json_pointer
from ._naming import (
    GTS_PREFIX,
    GTS_URI_PREFIX,
    looks_like_gts,
    strip_scheme,
    with_scheme,
)
from .entities import GtsEntity
from .gts import GtsID, GtsRef, GtsWildcard
from .gts_ref_validation import GtsRefValidationMode
from .schema_cast import GtsEntityCastResult

# Re-exported for backward compatibility; the limit now lives in schema_resolver.
from .schema_resolver import MAX_SCHEMA_REF_EXPANSIONS  # noqa: F401
from .schema_validation import FORMAT_CHECKER, iter_schema_nodes, validator_for
from .x_gts_ref import XGtsRefValidator, _without_x_gts_ref

logger = logging.getLogger(__name__)


def _require_schema_id(value: str) -> GtsID:
    try:
        return GtsID.parse_type(value)
    except ValueError as error:
        raise ValueError(f"ID '{value}' is not a schema (must end with '~')") from error


class StoreGtsObjectNotFound(GtsNotFoundError):
    """Exception raised when a GTS entity is not found in the store."""

    def __init__(self, entity_id: str):
        super().__init__(f"JSON object with GTS ID '{entity_id}' not found in store")
        self.entity_id = entity_id


class StoreGtsSchemaNotFound(GtsNotFoundError):
    """Exception raised when a GTS schema is not found in the store."""

    def __init__(self, entity_id: str):
        super().__init__(f"JSON schema with GTS ID '{entity_id}' not found in store")
        self.entity_id = entity_id


class StoreGtsEntityNotFound(GtsNotFoundError):
    """Exception raised when a GTS entity is not found in the store."""

    def __init__(self, entity_id: str):
        super().__init__(f"JSON entity with GTS ID '{entity_id}' not found in store")
        self.entity_id = entity_id


class StoreGtsSchemaForInstanceNotFound(GtsNotFoundError):
    """Exception raised when a GTS schema for an instance is not found in the store."""

    def __init__(self, entity_id: str):
        super().__init__(
            f"Can't determine JSON schema ID for instance with GTS ID '{entity_id}'"
        )
        self.entity_id = entity_id


class StoreGtsCastFromSchemaNotAllowed(GtsValidationError):
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

    @abstractmethod
    def read_by_id(self, entity_id: str) -> GtsEntity | None:
        """
        Read a JsonEntity by its ID.
        Returns None if the entity is not found.
        Used for cache miss scenarios.
        """

    @abstractmethod
    def reset(self) -> None:
        """Reset the iterator to start from the beginning."""


class GtsStoreQueryResult:
    def __init__(self):
        self.error = ""
        self.count = 0
        self.limit = 0
        self.results: list[dict[str, Any]] = []

    def to_dict(self) -> dict[str, Any]:
        if self.error:
            return {"error": self.error, "count": self.count, "limit": self.limit}
        return {
            "count": self.count,
            "limit": self.limit,
            "error": self.error,
            "results": self.results,
        }


class GtsStore:
    def __init__(self, reader: GtsReader | None = None) -> None:
        """
        Initialize GtsStore with an optional GtsReader.

        Args:
            reader: GtsReader instance to populate entities from
        """
        self._by_id: dict[str, GtsEntity] = {}
        self._reader = reader
        self._lock = threading.RLock()
        self._reference_registry: Registry | None = None

        # Populate entities from reader if provided
        if self._reader:
            self._populate_from_reader()

        logger.info(f"Populated GtsStore with {len(self._by_id)} entities")

    def _populate_from_reader(self) -> None:
        """Populate the store by iterating through the reader."""
        if not self._reader:
            return

        for entity in self._reader:
            if entity.gts_id and entity.gts_id.id:
                self._by_id[entity.gts_id.id] = copy.deepcopy(entity)
        self._reference_registry = None

    def _invalidate_reference_registry(self) -> None:
        self._reference_registry = None

    def register(self, entity: GtsEntity) -> None:
        """Register a GtsEntity in the store.

        If entity has a valid gts_id, use that as the key.
        Otherwise, use raw_id for non-GTS entities.
        """
        # Instances should remain addressable by the id value they carry.
        # For plain UUID anonymous instances, `gts_id` may be inferred from
        # the `type` field while `raw_id` is the UUID we must look up by.
        stored = copy.deepcopy(entity)
        with self._lock:
            if not stored.is_schema and stored.raw_id:
                self._by_id[stored.raw_id] = stored
                return

            if stored.gts_id and stored.gts_id.id:
                self._by_id[stored.gts_id.id] = stored
            elif stored.raw_id:
                # Allow non-GTS entities with raw_id (e.g., UUIDs or simple strings)
                self._by_id[stored.raw_id] = stored
            else:
                raise ValueError("Entity must have a valid gts_id or raw_id")
            if stored.is_schema:
                self._invalidate_reference_registry()

    def unregister(self, entity_id: str) -> None:
        """Remove an entity from the in-memory registry if it is present."""
        with self._lock:
            removed = self._by_id.pop(entity_id, None)
            if removed is not None and removed.is_schema:
                self._invalidate_reference_registry()

    @contextmanager
    def transaction(self):
        with self._lock:
            yield

    def register_schema(self, type_id: str, schema: dict[str, Any]) -> None:
        """
        Register a schema (legacy method for backward compatibility).
        Creates a JsonEntity from the schema dict.
        """
        gts_id = GtsID.parse_type(type_id)
        dialect = schema.get("$schema")
        if not isinstance(dialect, str) or not dialect:
            raise ValueError("Type Schema must contain a top-level $schema field")
        embedded_id = schema.get("$id")
        if not isinstance(embedded_id, str) or not embedded_id.startswith(
            GTS_URI_PREFIX + GTS_PREFIX
        ):
            raise ValueError("Type Schema must contain a top-level $id in gts:// form")
        try:
            normalized_id = GtsID.parse_type(strip_scheme(embedded_id))
        except ValueError as error:
            raise ValueError(f"Invalid GTS Type Schema $id: {embedded_id}") from error
        if normalized_id.id != gts_id.id:
            raise ValueError(
                f"Embedded $id '{embedded_id}' must match external type_id '{type_id}'"
            )
        entity = GtsEntity(content=copy.deepcopy(schema), gts_id=gts_id, is_schema=True)
        with self._lock:
            self._by_id[gts_id.id] = entity
            self._invalidate_reference_registry()

    def get(self, entity_id: str) -> GtsEntity | None:
        """
        Get a JsonEntity by its ID.
        If not found in cache, try to fetch from reader.
        Returns None if not found.

        Lookups are normalized to the canonical bare form here, so callers may
        pass either a bare ``gts.`` id or a ``gts://`` URI without stripping the
        scheme themselves.
        """
        entity_id = strip_scheme(entity_id)
        with self._lock:
            entity = self._by_id.get(entity_id)
            if entity is not None:
                return copy.deepcopy(entity)

            if self._reader:
                entity = self._reader.read_by_id(entity_id)
                if entity:
                    stored = copy.deepcopy(entity)
                    self._by_id[entity_id] = stored
                    if stored.is_schema:
                        self._invalidate_reference_registry()
                    return copy.deepcopy(stored)

            return None

    def get_schema_content(self, type_id: str) -> dict[str, Any]:
        """Get schema content as dict (legacy method for backward compatibility)."""
        entity = self.get(type_id)
        if entity and entity.is_schema and isinstance(entity.content, dict):
            return entity.content
        raise KeyError(f"Schema not found: {type_id}")

    def _create_reference_registry(self) -> Registry:
        with self._lock:
            if self._reference_registry is not None:
                return self._reference_registry
            registry = Registry()
            for entity_id, entity in self._by_id.items():
                if entity.is_schema and isinstance(entity.content, dict):
                    resource = Resource.from_contents(
                        _without_x_gts_ref(entity.content),
                        default_specification=DRAFT202012,
                    )
                    registry = registry.with_resource(with_scheme(entity_id), resource)
            self._reference_registry = registry
            return registry

    def items(self):
        """Return all entity ID and entity pairs."""
        with self._lock:
            return tuple(
                (entity_id, copy.deepcopy(entity))
                for entity_id, entity in self._by_id.items()
            )

    def keys(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._by_id)

    def values(self) -> tuple[GtsEntity, ...]:
        with self._lock:
            return tuple(copy.deepcopy(entity) for entity in self._by_id.values())

    @staticmethod
    def _validate_schema_refs(schema: dict[str, Any], path: str = "") -> None:
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
                    ref = GtsRef.parse(ref_uri)

                    # Local refs (JSON Pointer) are always valid.
                    if ref.is_local:
                        pass
                    # External GTS refs MUST use the gts:// URI form.
                    elif ref.is_gts and ref.has_scheme:
                        if not GtsID.is_valid(ref.target_id):
                            raise ValueError(
                                f"Invalid $ref at '{current_path}': '{ref_uri}' contains invalid GTS identifier '{ref.target_id}'"
                            )
                    # Anything else (bare gts., external URL, ...) is invalid.
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

    def _validate_schema_ref_targets(
        self, schema: Any, path: str = "", visited: set[str] | None = None
    ) -> None:
        visited = visited if visited is not None else set()
        if isinstance(schema, dict):
            ref_uri = schema.get("$ref")
            if isinstance(ref_uri, str):
                ref = GtsRef.parse(ref_uri)
                if not ref.is_local and ref.is_gts and ref.has_scheme:
                    current_path = f"{path}.$ref" if path else "$ref"
                    try:
                        target = self.get_schema_content(ref.target_id)
                    except KeyError as error:
                        raise GtsUnresolvedRefError(
                            f"Unresolvable $ref at '{current_path}': '{ref_uri}'"
                        ) from error
                    if ref.target_id not in visited:
                        visited.add(ref.target_id)
                        self._validate_schema_ref_targets(target, current_path, visited)
            for key, value in schema.items():
                if key != "$ref":
                    nested_path = f"{path}.{key}" if path else key
                    self._validate_schema_ref_targets(value, nested_path, visited)
        elif isinstance(schema, list):
            for index, item in enumerate(schema):
                self._validate_schema_ref_targets(item, f"{path}[{index}]", visited)

    def _validate_schema_x_gts_refs(self, gts_id: str) -> None:
        """
        Validate a schema's x-gts-ref fields.

        Args:
            gts_id: The GTS ID of the schema to validate
        """
        schema_id = _require_schema_id(gts_id)
        schema_entity = self.get(schema_id.id)
        if not schema_entity:
            raise StoreGtsSchemaNotFound(schema_id.id)

        if not schema_entity.is_schema:
            raise ValueError(f"Entity '{schema_id.id}' is not a schema")

        self._validate_schema_x_gts_refs_content(schema_id.id, schema_entity.content)

    def _validate_schema_x_gts_refs_content(
        self, gts_id: str, schema_content: dict[str, Any]
    ) -> None:
        logger.info(f"Validating schema x-gts-ref fields for {gts_id}")

        # Validate x-gts-ref constraints in the schema
        x_gts_ref_validator = XGtsRefValidator(store=self)
        x_gts_ref_errors = x_gts_ref_validator.validate_schema(schema_content)
        if x_gts_ref_errors:
            error_messages = [
                f"{err.field_path}: {err.reason}" for err in x_gts_ref_errors
            ]
            raise ValueError(
                f"Schema x-gts-ref validation failed: {'; '.join(error_messages)}"
            )

    @staticmethod
    def _validate_gts_keywords(content: dict[str, Any]) -> None:
        """Validate supported GTS extensions and their placement."""

        top_level_keywords = {
            "x-gts-final",
            "x-gts-abstract",
            "x-gts-traits",
            "x-gts-traits-schema",
        }
        supported_keywords = top_level_keywords | {"x-gts-ref"}

        # Validate x-gts-final
        final_val = content.get("x-gts-final")
        if final_val is not None and not isinstance(final_val, bool):
            raise ValueError(
                f"x-gts-final must be a boolean, got {type(final_val).__name__}"
            )

        # Validate x-gts-abstract
        abstract_val = content.get("x-gts-abstract")
        if abstract_val is not None and not isinstance(abstract_val, bool):
            raise ValueError(
                f"x-gts-abstract must be a boolean, got {type(abstract_val).__name__}"
            )

        # Mutual exclusion
        if final_val is True and abstract_val is True:
            raise ValueError(
                "schema cannot declare both x-gts-final and x-gts-abstract as true"
            )

        for schema_node, _ in iter_schema_nodes(content):
            for key in schema_node:
                if key.startswith("x-gts-") and key not in supported_keywords:
                    raise ValueError(f"Unsupported GTS extension keyword: {key}")

        # Check that x-gts-final/x-gts-abstract/x-gts-traits/x-gts-traits-schema
        # appear only at the top level.
        for schema_node, path in iter_schema_nodes(content):
            if not path:
                continue
            for keyword in top_level_keywords:
                if keyword in schema_node:
                    raise ValueError(f"{keyword} must be at the schema top level")

    @staticmethod
    def _content_is_abstract(content: dict[str, Any]) -> bool:
        return content.get("x-gts-abstract") is True

    @staticmethod
    def _content_is_final(content: dict[str, Any]) -> bool:
        return content.get("x-gts-final") is True

    @staticmethod
    def _schema_dialect(schema: dict[str, Any]) -> str:
        return schema_dialect.document_dialect(schema)

    @staticmethod
    def _schema_dialect_uri(schema: dict[str, Any]) -> str:
        return schema_dialect.dialect_uri(schema)

    def _validate_local_ref_dialects(
        self, schema: dict[str, Any], root_id: str, root_dialect: str
    ) -> None:
        for subschema, _schema_path in iter_schema_nodes(schema):
            ref = subschema.get("$ref")
            if not isinstance(ref, str) or not ref.startswith("#"):
                continue
            target = resolve_json_pointer(schema, ref)
            if not isinstance(target, dict) or "$schema" not in target:
                continue
            target_dialect = self._schema_dialect(target)
            if target_dialect != root_dialect:
                raise ValueError(
                    "GTS schema reference graph mixes JSON Schema dialects: "
                    f"root type '{root_id}' uses {root_dialect} but local $ref "
                    f"target '{ref}' uses {target_dialect}"
                )

    def _validate_chain_dialect(
        self,
        gts_id: str,
        chain_ids: list[str],
        transient_schema: dict[str, Any] | None,
    ) -> None:
        root_entity = self.get(chain_ids[0])
        root_content = (
            transient_schema
            if chain_ids[0] == gts_id and transient_schema is not None
            else root_entity.content
            if root_entity
            else None
        )
        if not isinstance(root_content, dict):
            return
        root_dialect = self._schema_dialect(root_content)

        for chain_id in chain_ids:
            entity = self.get(chain_id)
            content = (
                transient_schema
                if chain_id == gts_id and transient_schema is not None
                else entity.content
                if entity
                else None
            )
            if isinstance(content, dict):
                chain_dialect = self._schema_dialect(content)
                if chain_dialect != root_dialect:
                    raise ValueError(
                        "GTS derivation chain mixes JSON Schema dialects: "
                        f"root type '{chain_ids[0]}' uses {root_dialect} but "
                        f"'{chain_id}' uses {chain_dialect}; every type in a chained "
                        "$id hierarchy must use the root type's dialect"
                    )
                self._validate_local_ref_dialects(content, chain_ids[0], root_dialect)

        visited: set[str] = set()
        queue: deque[tuple[str, dict[str, Any]]] = deque(
            [(gts_id, transient_schema)] if transient_schema is not None else []
        )
        if not queue:
            entity = self.get(gts_id)
            if entity and isinstance(entity.content, dict):
                queue.append((gts_id, entity.content))
        while queue:
            current_id, content = queue.popleft()
            if current_id in visited:
                continue
            visited.add(current_id)
            for dependency_id, dependency_is_type in self._schema_dependencies(
                content, include_gts_refs=False
            ):
                if not dependency_is_type or dependency_id == current_id:
                    continue
                target = self.get(dependency_id)
                if (
                    not target
                    or not target.is_schema
                    or not isinstance(target.content, dict)
                ):
                    continue
                target_dialect = self._schema_dialect(target.content)
                if target_dialect != root_dialect:
                    raise ValueError(
                        "GTS derivation mixes JSON Schema dialects: "
                        f"root type '{chain_ids[0]}' uses {root_dialect} but $ref "
                        f"target '{dependency_id}' uses {target_dialect}; every type "
                        "in the chain and its transitive gts:// $ref targets must use "
                        "the root type's dialect"
                    )
                self._validate_local_ref_dialects(
                    target.content, chain_ids[0], root_dialect
                )
                queue.append((dependency_id, target.content))

    def _validate_schema_chain(
        self, gts_id: str, transient_schema: dict[str, Any] | None = None
    ) -> None:
        """Validate OP#12: schema derivation chain compatibility."""
        gid = GtsID(gts_id)
        segments = gid.gts_id_segments

        chain_ids = []
        prefix = GTS_PREFIX
        for seg in segments:
            chain_ids.append(prefix + seg.segment)
            prefix = prefix + seg.segment

        self._validate_chain_dialect(gts_id, chain_ids, transient_schema)

        # Single-segment schemas have no parent to validate against
        if len(segments) < 2:
            return

        # Validate each adjacent pair
        for i in range(len(chain_ids) - 1):
            base_id = chain_ids[i]
            derived_id = chain_ids[i + 1]

            base_entity = self.get(base_id)
            derived_entity = self.get(derived_id)
            derived_content = (
                transient_schema
                if transient_schema is not None and derived_id == gts_id
                else derived_entity.content
                if derived_entity
                else None
            )

            # Check x-gts-final: if the base type is final, derivation is not allowed.
            if (
                base_entity
                and isinstance(base_entity.content, dict)
                and self._content_is_final(base_entity.content)
            ):
                raise ValueError(
                    f"base type '{base_id}' is final and cannot be extended"
                )

            logger.info(
                f"OP#12: Validating schema chain pair: base={base_id} derived={derived_id}"
            )

            if not base_entity or not isinstance(base_entity.content, dict):
                raise ValueError(
                    f"Base schema '{base_id}' not found for chain validation"
                )
            if not isinstance(derived_content, dict):
                raise TypeError(
                    f"Derived schema '{derived_id}' not found for chain validation"
                )

            # Resolve both schemas (inline $refs)
            base_resolved = self._resolve_schema_refs(base_entity.content)
            derived_resolved = self._resolve_schema_refs(derived_content)

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
        """Resolve external ``$ref`` targets by inlining them from the store.

        Thin adapter over :func:`schema_resolver.resolve_schema_refs`, passing
        the store's :meth:`get_schema_content` as the reference provider.
        """
        return schema_resolver.resolve_schema_refs(schema, self.get_schema_content)

    @staticmethod
    def _supports_ref_siblings(schema: Any) -> bool:
        return schema_dialect.supports_ref_siblings(schema)

    def _inline_refs(
        self,
        node: Any,
        seen: set[str],
        supports_ref_siblings: bool,
        expansions: list[int] | None = None,
    ) -> Any:
        """Recursively inline ``$ref`` references, guarding against cycles."""
        return schema_resolver.inline_refs(
            node, seen, supports_ref_siblings, self.get_schema_content, expansions
        )

    def _build_effective_traits(
        self, gts_id: str, transient_schema: dict[str, Any] | None = None
    ) -> traits.EffectiveTraits:
        """Build OP#13 EffectiveTraits by walking the type's chain (root -> leaf)."""
        gid = GtsID(gts_id)
        segments = gid.gts_id_segments

        chain_ids: list[str] = []
        prefix = GTS_PREFIX
        for seg in segments:
            chain_ids.append(prefix + seg.segment)
            prefix = prefix + seg.segment

        trait_schemas: list[Any] = []
        merged_traits: dict[str, Any] = {}

        for schema_id in chain_ids:
            entity = self.get(schema_id)
            content = (
                transient_schema
                if transient_schema is not None and schema_id == gts_id
                else entity.content
                if entity
                else None
            )
            if not isinstance(content, dict):
                continue

            level_schemas: list[Any] = []
            traits.collect_trait_schema_from_value(content, level_schemas)
            for ts in level_schemas:
                if isinstance(ts, dict) and "$schema" in ts:
                    trait_dialect = self._schema_dialect(ts)
                    host_dialect = self._schema_dialect(content)
                    if trait_dialect != host_dialect:
                        raise ValueError(
                            f"trait schema dialect {trait_dialect} differs from host dialect {host_dialect}"
                        )
                # Inline local JSON Pointer refs against the host document, then
                # resolve any gts:// refs so the composed schema is self-contained.
                inlined = traits.inline_local_pointers(ts, content)
                trait_schemas.append(self._resolve_schema_refs(inlined))

            level_traits: dict[str, Any] = {}
            traits.collect_traits_from_value(content, level_traits)
            traits.merge_rfc7396_into(merged_traits, level_traits)

        leaf = self.get(chain_ids[-1]) if chain_ids else None
        leaf_content = (
            transient_schema
            if transient_schema is not None and chain_ids[-1] == gts_id
            else leaf.content
            if leaf
            else None
        )
        dialect = (
            self._schema_dialect_uri(leaf_content)
            if isinstance(leaf_content, dict)
            else None
        )

        return traits.build_effective_traits(trait_schemas, merged_traits, dialect)

    def _validate_traits(
        self,
        gts_id: str,
        is_abstract: bool,
        transient_schema: dict[str, Any] | None = None,
        gts_ref_validation: GtsRefValidationMode = GtsRefValidationMode.ANY_VALID,
    ) -> None:
        """Validate OP#13: schema traits for a type."""
        effective = self._build_effective_traits(gts_id, transient_schema)
        errors = effective.validate(
            check_unresolved=not is_abstract,
            reference_store=self,
            gts_ref_validation=gts_ref_validation,
            selected_type_id=gts_id,
        )
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
        schema_id = _require_schema_id(gts_id)
        schema_entity = self.get(schema_id.id)
        if not schema_entity:
            raise StoreGtsSchemaNotFound(schema_id.id)

        if not schema_entity.is_schema:
            raise ValueError(f"Entity '{schema_id.id}' is not a schema")

        schema_content = schema_entity.content
        if not isinstance(schema_content, dict):
            raise ValueError(  # noqa: TRY004 - keep ValueError for API compatibility
                f"Schema '{gts_id}' content must be a dictionary"
            )

        meta_schema_url = schema_content.get("$schema")
        if (
            meta_schema_url
            and isinstance(meta_schema_url, str)
            and looks_like_gts(meta_schema_url)
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

    def validate_schema_content(
        self,
        gts_id: str,
        schema_content: dict[str, Any],
        gts_ref_validation: GtsRefValidationMode = GtsRefValidationMode.ANY_VALID,
    ) -> None:
        """Validate a schema using the registry only for its dependencies."""
        schema_id = _require_schema_id(gts_id)

        meta_schema_url = schema_content.get("$schema")
        if (
            meta_schema_url
            and isinstance(meta_schema_url, str)
            and looks_like_gts(meta_schema_url)
        ):
            raise ValueError(
                f"Invalid $schema URL '{meta_schema_url}': must be a standard JSON Schema URL, not a GTS ID"
            )

        self._schema_dialect(schema_content)
        # A subschema may not switch JSON Schema dialect (spec sec 11); the whole
        # type is read under the dialect its top-level $schema selects.
        schema_dialect.check_subschemas(schema_content)
        logger.info(f"Validating schema {schema_id.id}")
        self._validate_schema_refs(schema_content, "")
        self._validate_schema_ref_targets(schema_content)
        self._validate_schema_x_gts_refs_content(schema_id.id, schema_content)
        self._validate_gts_keywords(schema_content)
        self._validate_schema_chain(schema_id.id, schema_content)

        try:
            validator_class = validator_for(
                {"$schema": self._schema_dialect_uri(schema_content)}
            )
            validator_class.check_schema(schema_content)

            logger.info(
                f"Schema {schema_id.id} passed JSON Schema meta-schema validation"
            )
        except Exception as error:
            raise ValueError(
                f"JSON Schema validation failed for '{schema_id.id}': {error!s}"
            ) from error

        self._validate_traits(
            schema_id.id,
            self._content_is_abstract(schema_content),
            schema_content,
            gts_ref_validation,
        )

    def validate_schema(
        self,
        gts_id: str,
        gts_ref_validation: GtsRefValidationMode = GtsRefValidationMode.ANY_VALID,
    ) -> None:
        """Validate a registered schema and all of its dependencies."""
        self._validate_schema_transitive(gts_id, set(), set(), gts_ref_validation)

    def _validate_schema_transitive(
        self,
        gts_id: str,
        visiting: set[str],
        validated: set[str],
        gts_ref_validation: GtsRefValidationMode,
    ) -> None:
        schema_id = _require_schema_id(gts_id)
        key = f"schema:{schema_id.id}"
        if key in validated or key in visiting:
            return

        schema_entity = self.get(schema_id.id)
        if not schema_entity:
            raise StoreGtsSchemaNotFound(schema_id.id)
        if not schema_entity.is_schema:
            raise ValueError(f"Entity '{schema_id.id}' is not a schema")
        if not isinstance(schema_entity.content, dict):
            raise ValueError(  # noqa: TRY004 - keep ValueError for API compatibility
                f"Schema '{schema_id.id}' content must be a dictionary"
            )

        visiting.add(key)
        try:
            self.validate_schema_content(
                schema_id.id, schema_entity.content, gts_ref_validation
            )

            schema_ref_validator = XGtsRefValidator(store=self, mode=gts_ref_validation)
            schema_ref_errors = schema_ref_validator.validate_schema_ref_existence(
                schema_entity.content, selected_type_id=schema_id.id
            )
            if schema_ref_errors:
                raise ValueError(
                    "x-gts-ref validation failed: "
                    + "; ".join(error.reason for error in schema_ref_errors)
                )

            effective_traits = self._build_effective_traits(schema_id.id)
            trait_ref_validator = XGtsRefValidator(store=self, mode=gts_ref_validation)
            trait_ref_validator.validate_schema_ref_existence(
                effective_traits.schema, selected_type_id=schema_id.id
            )
            trait_ref_validator.validate_instance(
                effective_traits.values,
                effective_traits.schema,
                selected_type_id=schema_id.id,
            )
            xref_ids = (
                schema_ref_validator.referenced_ids | trait_ref_validator.referenced_ids
            )
            wildcard_patterns = (
                schema_ref_validator.referenced_wildcard_patterns
                | trait_ref_validator.referenced_wildcard_patterns
            )
            if gts_ref_validation is GtsRefValidationMode.ANY_VALID:
                for dependency_id in xref_ids:
                    try:
                        self._validate_entity_transitive(
                            dependency_id, visiting, validated, gts_ref_validation
                        )
                    except Exception as error:
                        raise ValueError(
                            f"Referenced x-gts-ref entity '{dependency_id}' is invalid: {error}"
                        ) from error
                for pattern in wildcard_patterns:
                    if not self._has_valid_wildcard_match(
                        pattern, visiting, validated, gts_ref_validation
                    ):
                        raise ValueError(
                            f"x-gts-ref wildcard constraint '{pattern}' has no valid registered match"
                        )

            chain_ids: list[str] = []
            prefix = GTS_PREFIX
            for segment in schema_id.gts_id_segments:
                chain_ids.append(prefix + segment.segment)
                prefix += segment.segment
            for ancestor_id in chain_ids[:-1]:
                try:
                    self._validate_schema_transitive(
                        ancestor_id, visiting, validated, gts_ref_validation
                    )
                except Exception as error:
                    raise ValueError(
                        f"Ancestor type '{ancestor_id}' is invalid: {error}"
                    ) from error

            for dependency_id, dependency_is_type in self._schema_dependencies(
                schema_entity.content, include_gts_refs=False
            ):
                try:
                    if dependency_is_type:
                        self._validate_schema_transitive(
                            dependency_id, visiting, validated, gts_ref_validation
                        )
                    else:
                        self._validate_entity_transitive(
                            dependency_id, visiting, validated, gts_ref_validation
                        )
                except Exception as error:
                    raise ValueError(
                        f"Referenced entity '{dependency_id}' is invalid: {error}"
                    ) from error
        finally:
            visiting.remove(key)
        validated.add(key)

    def _schema_dependencies(
        self, schema: Any, include_gts_refs: bool = True
    ) -> Iterator[tuple[str, bool]]:
        x_gts_ref_validator = XGtsRefValidator(mode=GtsRefValidationMode.NONE)
        selected_type_id = (
            x_gts_ref_validator.selected_type_id(schema, None)
            if isinstance(schema, dict)
            else None
        )
        for subschema, _path in iter_schema_nodes(schema):
            ref_uri = subschema.get("$ref")
            if isinstance(ref_uri, str):
                ref = GtsRef.parse(ref_uri)
                if not ref.is_local and ref.is_gts and ref.has_scheme:
                    yield ref.target_id, True

            if not include_gts_refs:
                continue
            x_gts_ref = x_gts_ref_validator.resolve_ref_pattern(
                subschema.get("x-gts-ref"), selected_type_id
            )
            if (
                isinstance(x_gts_ref, str)
                and x_gts_ref.startswith(GTS_PREFIX)
                and "*" not in x_gts_ref
            ):
                yield x_gts_ref, True

    def _validate_entity_transitive(
        self,
        gts_id: str,
        visiting: set[str],
        validated: set[str],
        gts_ref_validation: GtsRefValidationMode,
    ) -> None:
        entity = self.get(gts_id)
        if not entity:
            raise StoreGtsEntityNotFound(gts_id)
        if entity.is_schema:
            self._validate_schema_transitive(
                gts_id, visiting, validated, gts_ref_validation
            )
        else:
            self._validate_instance_transitive(
                gts_id, visiting, validated, gts_ref_validation
            )

    def _has_valid_wildcard_match(
        self,
        pattern: str,
        visiting: set[str],
        validated: set[str],
        gts_ref_validation: GtsRefValidationMode,
    ) -> bool:
        wildcard = GtsWildcard(pattern)
        for entity_id in self.keys():
            try:
                if not GtsID(entity_id).wildcard_match(wildcard):
                    continue
                self._validate_entity_transitive(
                    entity_id, visiting, validated, gts_ref_validation
                )
                return True
            except Exception as error:  # noqa: BLE001 - try another wildcard match
                logger.debug("Invalid wildcard candidate %s: %s", entity_id, error)
                continue
        return False

    def validate_instance_content(
        self,
        content: dict[str, Any],
        type_id: str,
        gts_ref_validation: GtsRefValidationMode = GtsRefValidationMode.ANY_VALID,
    ) -> set[str]:
        """Validate unregistered instance content against a registered type schema."""
        schema_type = _require_schema_id(type_id)
        try:
            schema = self.get_schema_content(schema_type.id)
        except KeyError as error:
            raise StoreGtsSchemaNotFound(schema_type.id) from error

        self._validate_schema_chain(schema_type.id)
        self._schema_dialect(schema)
        if isinstance(schema, dict) and self._content_is_abstract(schema):
            raise ValueError(
                f"type '{schema_type.id}' is abstract and cannot have direct instances"
            )

        schema_for_validation = {
            **_without_x_gts_ref(schema),
            "$schema": self._schema_dialect_uri(schema),
        }
        validator_class = validator_for(schema_for_validation)
        validator = validator_class(
            schema_for_validation,
            registry=self._create_reference_registry(),
            format_checker=FORMAT_CHECKER,
        )
        validator.validate(content)

        x_gts_ref_validator = XGtsRefValidator(store=self, mode=gts_ref_validation)
        x_gts_ref_errors = x_gts_ref_validator.validate_instance(
            content, self._resolve_schema_refs(schema), selected_type_id=schema_type.id
        )
        if x_gts_ref_errors:
            error_messages = [
                f"{err.field_path}: {err.reason}" for err in x_gts_ref_errors
            ]
            raise ValueError(
                f"x-gts-ref validation failed: {'; '.join(error_messages)}"
            )
        return x_gts_ref_validator.referenced_ids

    def validate_instance(
        self,
        gts_id: str,
        gts_ref_validation: GtsRefValidationMode = GtsRefValidationMode.ANY_VALID,
    ) -> None:
        """Validate an object instance and its complete dependency closure."""
        self._validate_instance_transitive(gts_id, set(), set(), gts_ref_validation)

    def _validate_instance_transitive(
        self,
        gts_id: str,
        visiting: set[str],
        validated: set[str],
        gts_ref_validation: GtsRefValidationMode,
    ) -> None:
        key = f"instance:{gts_id}"
        if key in validated or key in visiting:
            return
        visiting.add(key)
        try:
            referenced_ids = self._validate_instance_local(gts_id, gts_ref_validation)

            obj = (
                self.get(GtsID(gts_id).id)
                if GtsID.is_valid(gts_id)
                else self.get(gts_id)
            )
            if not obj or not obj.type_id:
                return
            try:
                self._validate_schema_transitive(
                    obj.type_id, visiting, validated, gts_ref_validation
                )
            except Exception as error:
                raise ValueError(
                    f"Instance type '{obj.type_id}' is invalid: {error}"
                ) from error

            if gts_ref_validation is GtsRefValidationMode.ANY_VALID:
                for dependency_id in referenced_ids:
                    try:
                        self._validate_entity_transitive(
                            dependency_id, visiting, validated, gts_ref_validation
                        )
                    except Exception as error:
                        raise ValueError(
                            f"Referenced entity '{dependency_id}' is invalid: {error}"
                        ) from error
        finally:
            visiting.remove(key)
        validated.add(key)

    def _validate_instance_local(
        self,
        gts_id: str,
        gts_ref_validation: GtsRefValidationMode,
    ) -> set[str]:
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
            except Exception as e:
                raise StoreGtsObjectNotFound(gts_id) from e
            obj = self.get(gts_id)
            lookup_id = gts_id

        if not obj:
            raise StoreGtsObjectNotFound(gts_id)
        if not obj.type_id:
            raise StoreGtsSchemaForInstanceNotFound(lookup_id)
        if not isinstance(obj.content, dict):
            raise TypeError(f"Instance '{lookup_id}' content must be a dictionary")

        logger.info(f"Validating instance {gts_id} against schema {obj.type_id}")
        return self.validate_instance_content(
            obj.content, obj.type_id, gts_ref_validation
        )

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
        registry = self._create_reference_registry()

        return from_entity.cast(to_schema, from_schema, resolver=registry)

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

        # Surface *why* a direction failed and classify the candidate's object
        # levels so a caller can see whether a level can still gain optional
        # properties later (spec sec 4.4). Reasons are derived from the verdicts
        # already computed above (no second inclusion pass), so they never
        # contradict the verdict.
        differing_dialects = compatibility.dialects_differ(old_resolved, new_resolved)
        backward_errors = compatibility.explain_verdict(
            backward, backward=True, differing_dialects=differing_dialects
        )
        forward_errors = compatibility.explain_verdict(
            forward, backward=False, differing_dialects=differing_dialects
        )
        # Union the directional reasons for the summary, dropping the duplicate
        # a single shared cause (e.g. differing dialects) would produce.
        incompatibility_reasons = list(dict.fromkeys(backward_errors + forward_errors))

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
            incompatibility_reasons=incompatibility_reasons,
            backward_errors=backward_errors,
            forward_errors=forward_errors,
            casted_entity=None,
            backward_verdict=backward,
            forward_verdict=forward,
            full_verdict=full,
            candidate_object_levels=compatibility.classify_object_levels(new_resolved),
        )

    def build_schema_graph(self, gts_id: str) -> dict[str, Any]:
        seen_gts_ids: set[str] = set()

        def gts2node(gts_id: str, seen_gts_ids: set[str]) -> dict[str, Any]:
            ret: dict[str, Any] = {"id": gts_id}

            if gts_id in seen_gts_ids:
                return ret

            seen_gts_ids.add(gts_id)

            entity = self.get(gts_id)
            if entity:
                refs: dict[str, Any] = {}
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
                        ret["type_id"] = gts2node(entity.type_id, seen_gts_ids)
                else:
                    ret["errors"] = ret.get("errors", []) + ["Schema not recognized"]
            else:
                ret["errors"] = ret.get("errors", []) + ["Entity not found"]

            return ret

        return gts2node(gts_id, seen_gts_ids)

    def _parse_query_filters(self, filter_str: str) -> dict[str, str]:
        """Parse filter expressions from query string.

        Args:
            filter_str: Filter string like 'status=active, category=order'

        Returns:
            Dictionary of filter key-value pairs
        """
        filters: dict[str, str] = {}
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
    ) -> tuple[GtsWildcard | None, GtsID | None, str]:
        """Validate and parse the query pattern.

        Args:
            base_pattern: The base GTS ID pattern
            is_wildcard: Whether the pattern contains wildcards

        Returns:
            Tuple of (wildcard_pattern, exact_gts_id, error_message)
        """
        if is_wildcard:
            # Wildcard pattern must end with .* or ~*
            if not base_pattern.endswith((".*", "~*")):
                return (
                    None,
                    None,
                    "Invalid query: wildcard patterns must end with .* or ~*",
                )
            try:
                wildcard_pattern = GtsWildcard(base_pattern)
                return wildcard_pattern, None, ""
            except Exception as e:  # noqa: BLE001 - error surfaced in return value
                return None, None, f"Invalid query: {e!s}"
        else:
            # Non-wildcard pattern must be a complete valid GTS ID
            try:
                exact_gts_id = GtsID(base_pattern)
                if not exact_gts_id.gts_id_segments:
                    return None, None, "Invalid query: GTS ID has no valid segments"
                return None, exact_gts_id, ""
            except Exception as e:  # noqa: BLE001 - error surfaced in return value
                return None, None, f"Invalid query: {e!s}"

    def _matches_id_pattern(
        self,
        entity_id: GtsID,
        base_pattern: str,
        is_wildcard: bool,
        wildcard_pattern: GtsWildcard | None,
        exact_gts_id: GtsID | None,
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
            except Exception:  # noqa: BLE001 - fall back to exact match
                return entity_id.id == base_pattern

        return entity_id.id == base_pattern

    def _matches_filters(
        self, entity_content: dict[str, Any], filters: dict[str, str]
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
        for entity in self.values():
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
