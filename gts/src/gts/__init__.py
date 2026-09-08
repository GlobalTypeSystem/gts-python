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
from .path_resolver import GtsPathResolver
from .store import (
    GtsReader,
    GtsStore,
)

__all__ = [
    "DEFAULT_GTS_CONFIG",
    "GtsConfig",
    "GtsEntity",
    "GtsFile",
    "GtsFileReader",
    "GtsID",
    "GtsIdSegment",
    "GtsPathResolver",
    "GtsReader",
    "GtsStore",
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
