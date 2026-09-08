from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .gts import GtsID
from .schema_cast import GtsEntityCastResult, SchemaCastError

if TYPE_CHECKING:
    from .path_resolver import GtsPathResolver


@dataclass
class ValidationError:
    instancePath: str
    schemaPath: str
    keyword: str
    message: str
    params: dict[str, Any]
    data: Any | None = None


@dataclass
class ValidationResult:
    errors: list[ValidationError] = field(default_factory=list)


@dataclass
class GtsFile:
    path: str
    name: str
    content: Any
    sequencesCount: int = 0
    sequenceContent: dict[int, Any] = field(default_factory=dict)
    validation: ValidationResult = field(default_factory=ValidationResult)

    def __post_init__(self) -> None:
        items = self.content if isinstance(self.content, list) else [self.content]
        for i, it in enumerate(items):
            self.sequencesCount += 1
            self.sequenceContent[i] = it


@dataclass
class GtsConfig:
    entity_id_fields: list[str]
    schema_id_fields: list[str]


DEFAULT_GTS_CONFIG = GtsConfig(
    entity_id_fields=[
        "$id",
        "gtsId",
        "gtsIid",
        "gtsOid",
        "gtsI",
        "gts_id",
        "gts_oid",
        "gts_iid",
        "id",
    ],
    schema_id_fields=[
        "gtsTid",
        "gtsType",
        "gtsT",
        "gts_t",
        "gts_tid",
        "gts_type",
        "type",
        "schema",
    ],
)


@dataclass
class GtsEntity:
    gts_id: GtsID | None = None
    is_schema: bool = False
    file: GtsFile | None = None
    list_sequence: int | None = None
    label: str = ""
    content: Any = None
    gts_refs: list[dict[str, str]] = field(default_factory=list)
    validation: ValidationResult = field(default_factory=ValidationResult)
    type_id: str | None = None
    selected_entity_field: str | None = None
    selected_type_id_field: str | None = None
    description: str = ""
    raw_id: str | None = None  # Stores raw ID value (may be non-GTS)
    schemaRefs: list[dict[str, str]] = field(default_factory=list)

    def __init__(
        self,
        *,
        file: GtsFile | None = None,
        list_sequence: int | None = None,
        content: Any = None,
        cfg: GtsConfig | None = None,
        gts_id: GtsID | None = None,
        is_schema: bool = False,
        label: str = "",
        validation: ValidationResult | None = None,
        type_id: str | None = None,
    ) -> None:
        self.file = file
        self.list_sequence = list_sequence
        self.content = content
        self.gts_id = gts_id
        self.is_schema = is_schema
        self.label = label
        self.validation = validation or ValidationResult()
        self.type_id = type_id
        self.selected_entity_field = None
        self.selected_type_id_field = None
        self.gts_refs = []
        self.schemaRefs = []
        self.description = ""

        # Auto-detect if this is a schema
        if content is not None and self._is_json_schema_entity():
            self.is_schema = True

        # Calculate IDs if config provided
        if cfg is not None:
            idv = self._calc_json_entity_id(cfg)
            self.raw_id = idv  # Store raw ID even if non-GTS
            self.type_id = self._calc_json_schema_id(cfg)
            # If no valid GTS ID found in entity fields, use schema ID as fallback
            if not (idv and GtsID.is_valid(idv)) and (
                self.type_id and GtsID.is_valid(self.type_id)
            ):
                idv = self.type_id
            self.gts_id = GtsID(idv) if idv and GtsID.is_valid(idv) else None

        # Set label
        if self.file and self.list_sequence is not None:
            self.label = f"{self.file.name}#{self.list_sequence}"
        elif self.file:
            self.label = self.file.name
        elif self.gts_id:
            self.label = self.gts_id.id
        elif not self.label:
            self.label = ""

        # Extract description
        self.description = (
            (self.content or {}).get("description", "")
            if isinstance(self.content, dict)
            else ""
        )

        # Extract references
        self.gts_refs = self._extract_gts_ids_with_paths()
        if self.is_schema:
            self.schemaRefs = self._extract_ref_strings_with_paths()

    def _is_json_schema_entity(self) -> bool:
        if not isinstance(self.content, dict):
            return False
        url = self.content.get("$schema")
        if not isinstance(url, str):
            return False
        # Issue #25: strict check, no GTS IDs in $schema
        return url.startswith(("http://json-schema.org/", "https://json-schema.org/"))

    def resolve_path(self, path: str) -> GtsPathResolver:
        from .path_resolver import GtsPathResolver

        resolver = GtsPathResolver(self.gts_id.id if self.gts_id else "", self.content)
        return resolver.resolve(path)

    def cast(
        self,
        to_schema: GtsEntity,
        from_schema: GtsEntity,
        resolver: Any | None = None,
    ) -> GtsEntityCastResult:
        if (
            self.is_schema
            and from_schema.gts_id
            and self.gts_id.id != from_schema.gts_id.id
        ):
            # When casting a schema, from_schema might be a standard JSON Schema (no gts_id)
            # In that case, skip the sanity check
            raise SchemaCastError(
                f"Internal error: {self.gts_id.id} != {from_schema.gts_id.id}"
            )
        if not to_schema.is_schema:
            raise SchemaCastError("Target must be a schema")
        if not from_schema.is_schema:
            raise SchemaCastError("Source schema must be a schema")
        return GtsEntityCastResult.cast(
            self.gts_id.id,
            to_schema.gts_id.id,
            self.content,
            from_schema.content,
            to_schema.content,
            resolver=resolver,
        )

    def _walk_and_collect(
        self,
        content: Any,
        collector: list[dict[str, str]],
        matcher: Any,  # Callable but avoiding import
    ) -> None:
        """Generic tree walker that collects matching nodes.

        Args:
            content: Content to walk through
            collector: List to append matches to
            matcher: Function that takes (node, path) and returns Optional[Dict[str, str]]
        """

        def walk(node: Any, current_path: str = "") -> None:
            if node is None:
                return

            # Try to match current node
            match_result = matcher(node, current_path)
            if match_result:
                collector.append(match_result)

            # Recurse into structures
            if isinstance(node, dict):
                for k, v in node.items():
                    next_path = f"{current_path}.{k}" if current_path else k
                    walk(v, next_path)
            elif isinstance(node, list):
                for idx, item in enumerate(node):
                    next_path = f"{current_path}[{idx}]"
                    walk(item, next_path)

        walk(content)

    def _deduplicate_by_id_and_path(
        self, items: list[dict[str, str]]
    ) -> list[dict[str, str]]:
        """Deduplicate items by their id and sourcePath."""
        uniq: dict[str, dict[str, str]] = {}
        for item in items:
            key = f"{item['id']}|{item['sourcePath']}"
            uniq[key] = item
        return list(uniq.values())

    def _extract_gts_ids_with_paths(self) -> list[dict[str, str]]:
        """Extract all GTS IDs from content with their paths."""
        found: list[dict[str, str]] = []

        def gts_id_matcher(node: Any, path: str) -> dict[str, str] | None:
            """Match GTS ID strings."""
            if isinstance(node, str):
                val = node
                val = val.removeprefix("gts://")
                if GtsID.is_valid(val):
                    return {"id": val, "sourcePath": path or "root"}
            return None

        self._walk_and_collect(self.content, found, gts_id_matcher)
        return self._deduplicate_by_id_and_path(found)

    def _extract_ref_strings_with_paths(self) -> list[dict[str, str]]:
        """Extract $ref strings with their paths (for schemas)."""
        refs: list[dict[str, str]] = []

        def ref_matcher(node: Any, path: str) -> dict[str, str] | None:
            """Match $ref properties in dict nodes."""
            if isinstance(node, dict) and isinstance(node.get("$ref"), str):
                val = node["$ref"]
                # Issue #32: handle gts:// prefix
                val = val.removeprefix("gts://")
                ref_path = f"{path}.$ref" if path else "$ref"
                return {"id": val, "sourcePath": ref_path}
            return None

        self._walk_and_collect(self.content, refs, ref_matcher)
        return self._deduplicate_by_id_and_path(refs)

    def _get_field_value(self, field: str) -> str | None:
        """Get string value from content field."""
        if not isinstance(self.content, dict):
            return None
        v = self.content.get(field)
        if isinstance(v, str) and v.strip():
            # Issue #31, #32: Handle gts:// prefix in fields (e.g. $id)
            v = v.removeprefix("gts://")
            return v
        return None

    def _first_non_empty_field(self, fields: list[str]) -> tuple[str, str] | None:
        """Find first non-empty field value in order.

        Returns the first non-empty string value without preferring GTS IDs.
        This ensures UUID and non-GTS values are returned when they appear first.
        """
        for f in fields:
            v = self._get_field_value(f)
            if v:
                return f, v
        return None

    def _calc_json_entity_id(self, cfg: GtsConfig) -> str:
        cand = self._first_non_empty_field(cfg.entity_id_fields)
        if cand:
            self.selected_entity_field = cand[0]
            return cand[1]
        if self.file and self.list_sequence is not None:
            return f"{self.file.path}#{self.list_sequence}"
        return self.file.path if self.file else ""

    def _calc_json_schema_id(self, cfg: GtsConfig) -> str | None:
        """Calculate schema_id based on entity type and content.

        Rules:
        - For schemas: extract parent from $id chain, or fallback to $schema
        - For instances: look for type/schema fields in schema_id_fields
        - Return None if no schema reference found for instances
        """
        # For schemas, derive from the entity ID (parent of chain)
        if self.is_schema:
            # Get entity ID (the $id field for schemas)
            idv = self._get_field_value("$id")
            if idv and GtsID.is_valid(idv):
                # For schemas, a chained $id means derivation.
                # type_id is the parent (everything up to the second-to-last '~').
                # idv ends with '~' for schemas.
                # Strip trailing '~' to find internal chain boundaries.
                inner = idv.removesuffix("~")
                last_tilde = inner.rfind("~")
                if last_tilde > 0:
                    # Has at least 2 segments - return parent chain
                    self.selected_type_id_field = "$id"
                    return inner[: last_tilde + 1]
            # Base schema (single segment) - no GTS parent type.
            # The $schema URL is NOT a GTS Type Identifier.
            return None

        # PRIORITY 1: Check entity_id_fields for a GTS ID (gtsId, id, etc.)
        # If found and it's a chained ID, extract schema from the chain
        # NOTE: Skip $id field for instances - $id should only influence schema_id for schemas
        entity_id_cand = self._first_non_empty_field(cfg.entity_id_fields)
        if entity_id_cand and GtsID.is_valid(entity_id_cand[1]):
            # Skip $id for non-schemas: $id without $schema means the doc is an instance
            # and $id should not be used to derive schema_id
            if entity_id_cand[0] == "$id" and not self.is_schema:
                pass  # Skip to PRIORITY 2
            else:
                idv = entity_id_cand[1]
                # If already a type id (ends with '~'), use it as-is
                if idv.endswith("~"):
                    self.selected_type_id_field = entity_id_cand[0]
                    return idv
                # For chained IDs (well-known instances), extract schema:
                # everything up to and including last '~'
                last_tilde = idv.rfind("~")
                if last_tilde > 0:
                    self.selected_type_id_field = entity_id_cand[0]
                    return idv[: last_tilde + 1]

        # PRIORITY 2: Fall back to explicit schema_id_fields (type, gtsTid, etc.)
        # Only check these if no chained GTS ID was found in entity_id_fields
        # NOTE: Only use these for instances (non-schemas) - schemas use $id chain
        cand = self._first_non_empty_field(cfg.schema_id_fields)
        if cand:
            self.selected_type_id_field = cand[0]
            type_id_val = cand[1]
            # If type_id is a chained GTS ID, extract parent (base type)
            if GtsID.is_valid(type_id_val):
                last_tilde = type_id_val.rfind("~")
                if last_tilde > 0 and not type_id_val.endswith("~"):
                    # It's an instance ID in type field - extract schema part
                    return type_id_val[: last_tilde + 1]
            return type_id_val

        # No schema reference found for instance
        return None

    def get_graph(self) -> dict[str, set[str]]:
        refs = {}
        for r in self.gts_refs:
            refs[r["sourcePath"]] = r["id"]
        return {"id": self.gts_id.id, "schema_id": self.type_id, "refs": refs}
