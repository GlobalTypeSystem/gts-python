from .entities import (
    DEFAULT_GTS_CONFIG,
    GtsConfig,
    GtsEntity,
    GtsFile,
    ValidationError,
    ValidationResult,
)
from .files_reader import (
    GtsFileReader,
)
from .gts import (
    GtsID,
    GtsIdSegment,
    GtsWildcard,
)
from .gts_ref_validation import GtsRefValidationMode
from .ops import (
    GtsAddEntitiesResult,
    GtsAddEntityResult,
    GtsAddSchemaResult,
    GtsAddSchemasResult,
    GtsEntitiesListResult,
    GtsEntityInfo,
    GtsEntityValidationResult,
    GtsExtractIdResult,
    GtsGetEntityResult,
    GtsIdMatchResult,
    GtsIdParseResult,
    GtsIdValidationResult,
    GtsJsonValidationResult,
    GtsOps,
    GtsSchemaGraphResult,
    GtsUuidResult,
    GtsValidationResult,
)
from .path_resolver import GtsPathResolver
from .store import (
    GtsReader,
    GtsStore,
)

__all__ = [
    "DEFAULT_GTS_CONFIG",
    "GtsAddEntitiesResult",
    "GtsAddEntityResult",
    "GtsAddSchemaResult",
    "GtsAddSchemasResult",
    "GtsConfig",
    "GtsEntitiesListResult",
    "GtsEntity",
    "GtsEntityInfo",
    "GtsEntityValidationResult",
    "GtsExtractIdResult",
    "GtsFile",
    "GtsFileReader",
    "GtsGetEntityResult",
    "GtsID",
    "GtsIdMatchResult",
    "GtsIdParseResult",
    "GtsIdSegment",
    "GtsIdValidationResult",
    "GtsJsonValidationResult",
    "GtsOps",
    "GtsPathResolver",
    "GtsReader",
    "GtsRefValidationMode",
    "GtsSchemaGraphResult",
    "GtsStore",
    "GtsUuidResult",
    "GtsValidationResult",
    "GtsWildcard",
    "JsonEntity",
    # Backward compatibility aliases
    "JsonFile",
    "JsonPathResolver",
    "ValidationError",
    "ValidationResult",
]

# Backward compatibility aliases
JsonFile = GtsFile
JsonEntity = GtsEntity
JsonPathResolver = GtsPathResolver
