from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from typing import Any

import regex
from jsonschema import Draft7Validator, FormatChecker, ValidationError, validators
from jsonschema.validators import validator_for as jsonschema_validator_for

PATTERN_TIMEOUT_SECONDS = 1.0

_SCHEMA_MAP_KEYWORDS = {
    "$defs",
    "definitions",
    "dependentSchemas",
    "patternProperties",
    "properties",
}
_SCHEMA_ARRAY_KEYWORDS = {"allOf", "anyOf", "oneOf", "prefixItems"}
_DRAFT3_SCHEMA_KEYWORDS = {"disallow", "extends", "type"}
_SCHEMA_SINGLE_KEYWORDS = {
    "additionalItems",
    "additionalProperties",
    "contains",
    "contentSchema",
    "else",
    "if",
    "not",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
    "x-gts-traits-schema",
}


def iter_schema_nodes(
    schema: Any, path: str = ""
) -> Iterator[tuple[dict[str, Any], str]]:
    if not isinstance(schema, dict):
        return
    yield schema, path
    for keyword, value in schema.items():
        keyword_path = f"{path}/{keyword}" if path else keyword
        if keyword in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
            for name, child in value.items():
                yield from iter_schema_nodes(child, f"{keyword_path}/{name}")
        elif keyword in _SCHEMA_ARRAY_KEYWORDS and isinstance(value, list):
            for index, child in enumerate(value):
                yield from iter_schema_nodes(child, f"{keyword_path}[{index}]")
        elif keyword in _SCHEMA_SINGLE_KEYWORDS:
            yield from iter_schema_nodes(value, keyword_path)
        elif keyword in _DRAFT3_SCHEMA_KEYWORDS:
            if isinstance(value, dict):
                yield from iter_schema_nodes(value, keyword_path)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    if isinstance(child, dict):
                        yield from iter_schema_nodes(child, f"{keyword_path}[{index}]")
        elif keyword == "items":
            if isinstance(value, list):
                for index, child in enumerate(value):
                    yield from iter_schema_nodes(child, f"{keyword_path}[{index}]")
            else:
                yield from iter_schema_nodes(value, keyword_path)
        elif keyword == "dependencies" and isinstance(value, dict):
            for name, child in value.items():
                if isinstance(child, (dict, bool)):
                    yield from iter_schema_nodes(child, f"{keyword_path}/{name}")


def map_schema_nodes(schema: Any, transform: Callable[[Any], Any]) -> Any:
    if not isinstance(schema, dict):
        return copy.deepcopy(schema)
    mapped = copy.deepcopy(schema)
    for keyword, value in schema.items():
        if keyword in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
            mapped[keyword] = {
                name: map_schema_nodes(child, transform)
                for name, child in value.items()
            }
        elif keyword in _SCHEMA_ARRAY_KEYWORDS and isinstance(value, list):
            mapped[keyword] = [map_schema_nodes(child, transform) for child in value]
        elif keyword in _SCHEMA_SINGLE_KEYWORDS:
            mapped[keyword] = map_schema_nodes(value, transform)
        elif keyword in _DRAFT3_SCHEMA_KEYWORDS:
            if isinstance(value, dict):
                mapped[keyword] = map_schema_nodes(value, transform)
            elif isinstance(value, list):
                mapped[keyword] = [
                    map_schema_nodes(child, transform)
                    if isinstance(child, dict)
                    else copy.deepcopy(child)
                    for child in value
                ]
        elif keyword == "items":
            if isinstance(value, list):
                mapped[keyword] = [
                    map_schema_nodes(child, transform) for child in value
                ]
            else:
                mapped[keyword] = map_schema_nodes(value, transform)
        elif keyword == "dependencies" and isinstance(value, dict):
            mapped[keyword] = {
                name: map_schema_nodes(child, transform)
                if isinstance(child, (dict, bool))
                else copy.deepcopy(child)
                for name, child in value.items()
            }
    return transform(mapped)


# Shared format checker for instance/trait validation.
#
# A bare ``FormatChecker()`` draws from jsonschema's shared, class-level checker
# registry, in which draft3-only checkers overwrite their draft6/7 successors
# (e.g. "time" ends up as bare ``HH:MM:SS`` and rejects a valid RFC 3339 value
# like "10:30:00Z"). Conversely, ``Draft7Validator.FORMAT_CHECKER`` fixes those
# but lacks "uuid" (added to JSON Schema only in draft 2019-09).
#
# Starting from the bare checker (which provides "uuid") and overlaying the
# draft-07 checkers (which restore correct RFC 3339 "time"/"date-time") yields
# the complete, correct standard-format set. This uses only built-in jsonschema
# checkers -- no custom format functions.
FORMAT_CHECKER = FormatChecker()
FORMAT_CHECKER.checkers.update(Draft7Validator.FORMAT_CHECKER.checkers)


def _validate_pattern(
    validator: Any, pattern: str, instance: Any, schema: Any
) -> Iterator[ValidationError]:
    if not isinstance(instance, str):
        return
    try:
        if regex.search(pattern, instance, timeout=PATTERN_TIMEOUT_SECONDS) is None:
            yield ValidationError(f"{instance!r} does not match {pattern!r}")
    except TimeoutError:
        yield ValidationError("regular expression match timed out")
    except regex.error as error:
        yield ValidationError(f"invalid regular expression: {error}")


def validator_for(schema: Any) -> Any:
    return validators.extend(
        jsonschema_validator_for(schema), {"pattern": _validate_pattern}
    )
