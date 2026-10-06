from __future__ import annotations

import copy
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import attrs
from jsonschema import (
    Draft7Validator,
    Draft201909Validator,
    Draft202012Validator,
    FormatChecker,
    ValidationError,
    validators,
)
from jsonschema.exceptions import SchemaError
from referencing import Registry
from referencing.exceptions import Unresolvable
from referencing.jsonschema import (
    DRAFT7,
    DRAFT201909,
    DRAFT202012,
    lookup_recursive_ref,
)

from . import safe_regex, schema_dialect
from .safe_regex import RegexEvaluationError, UnsupportedRegexError

__all__ = [
    "FORMAT_CHECKER",
    "RegexEvaluationError",
    "UnsupportedRegexError",
    "check_schema",
    "check_schema_regexes",
    "iter_schema_nodes",
    "map_schema_nodes",
    "validator_family",
    "validator_for",
]

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
# the complete standard-format set. The regex checker is replaced below with
# the safe-profile check used for schema patterns (spec sec 11.0.1).
FORMAT_CHECKER = FormatChecker()
FORMAT_CHECKER.checkers.update(Draft7Validator.FORMAT_CHECKER.checkers)


def _is_supported_regex(value: object) -> bool:
    # An unsupported string is an ordinary format violation (so ``not`` may
    # invert it); a compilation failure (RegexEvaluationError) propagates.
    if isinstance(value, str):
        safe_regex.check_supported(value)
    return True


FORMAT_CHECKER.checks("regex", raises=UnsupportedRegexError)(_is_supported_regex)


# --- safe-profile keywords --------------------------------------------------
#
# Replace Python re in keywords and property helpers with safe_regex.
# Raise regex failures so applicators cannot invert them.


def _search(pattern: Any, text: str) -> bool:
    return safe_regex.search(pattern, text)


def _validate_pattern(
    validator: Any, pattern: Any, instance: Any, schema: Any
) -> Iterator[ValidationError]:
    if not validator.is_type(instance, "string"):
        return
    if not _search(pattern, instance):
        yield ValidationError(f"{instance!r} does not match {pattern!r}")


def _validate_pattern_properties(
    validator: Any, pattern_properties: Any, instance: Any, schema: Any
) -> Iterator[ValidationError]:
    if not validator.is_type(instance, "object"):
        return
    for pattern, subschema in pattern_properties.items():
        for name, value in instance.items():
            if _search(pattern, name):
                yield from validator.descend(
                    value, subschema, path=name, schema_path=pattern
                )


def _find_additional_properties(instance: Any, schema: Any) -> Iterator[Any]:
    # Match separately: jsonschema's "|" join drops empty patterns and leaks flags.
    properties = schema.get("properties", {})
    patterns = schema.get("patternProperties", {})
    for name in instance:
        if name in properties:
            continue
        if any(_search(pattern, name) for pattern in patterns):
            continue
        yield name


def _extras_msg(extras: list[Any]) -> tuple[str, str]:
    verb = "was" if len(extras) == 1 else "were"
    return ", ".join(repr(extra) for extra in extras), verb


def _validate_additional_properties(
    validator: Any, additional: Any, instance: Any, schema: Any
) -> Iterator[ValidationError]:
    if not validator.is_type(instance, "object"):
        return
    extras = set(_find_additional_properties(instance, schema))
    if validator.is_type(additional, "object"):
        for extra in extras:
            yield from validator.descend(instance[extra], additional, path=extra)
    elif not additional and extras:
        if "patternProperties" in schema:
            verb = "does" if len(extras) == 1 else "do"
            joined = ", ".join(repr(each) for each in sorted(extras))
            patterns = ", ".join(
                repr(each) for each in sorted(schema["patternProperties"])
            )
            yield ValidationError(
                f"{joined} {verb} not match any of the regexes: {patterns}"
            )
        else:
            error = "Additional properties are not allowed (%s %s unexpected)"
            yield ValidationError(error % _extras_msg(sorted(extras, key=str)))


def _no_errors(errors: Iterator[ValidationError]) -> bool:
    return next(errors, None) is None


def _keys_annotated_here(validator: Any, instance: Any, schema: Any) -> list[Any]:
    """Keys evaluated by this schema object's property applicators.

    Unlike jsonschema's 2019-09 helper, schema-valued ``additionalProperties``
    annotates valid additional keys in both modern dialects.
    """
    evaluated: list[Any] = []
    properties = schema.get("properties")
    if validator.is_type(properties, "object"):
        evaluated += [name for name in instance if name in properties]
    pattern_properties = schema.get("patternProperties")
    if validator.is_type(pattern_properties, "object"):
        evaluated += [
            name
            for name in instance
            if any(_search(pattern, name) for pattern in pattern_properties)
        ]
    candidates = {
        "additionalProperties": lambda: _find_additional_properties(instance, schema),
        "unevaluatedProperties": lambda: iter(instance),
    }
    for keyword, names in candidates.items():
        if (subschema := schema.get(keyword)) is None:
            continue
        evaluated += [
            name
            for name in names()
            if _no_errors(validator.descend(instance[name], subschema))
        ]
    return evaluated


def _evaluated_keys(
    validator: Any, instance: Any, schema: Any, *, draft2019: bool
) -> list[Any]:
    """Collect evaluated keys using safe regex matching.

    Port of jsonschema's 2020-12 helper; 2019-09 uses ``$recursiveRef`` instead
    of ``$dynamicRef``.
    """
    if validator.is_type(schema, "boolean"):
        return []
    evaluated: list[Any] = []

    def recurse(subschema: Any, current: Any = validator) -> list[Any]:
        return _evaluated_keys(current, instance, subschema, draft2019=draft2019)

    targets = []
    if schema.get("$ref") is not None:
        targets.append(validator._resolver.lookup(schema["$ref"]))
    if draft2019 and "$recursiveRef" in schema:
        targets.append(lookup_recursive_ref(validator._resolver))
    if not draft2019 and schema.get("$dynamicRef") is not None:
        targets.append(validator._resolver.lookup(schema["$dynamicRef"]))
    for resolved in targets:
        evolved = validator.evolve(
            schema=resolved.contents, _resolver=resolved.resolver
        )
        evaluated += recurse(resolved.contents, evolved)

    evaluated += _keys_annotated_here(validator, instance, schema)

    for name, subschema in schema.get("dependentSchemas", {}).items():
        if name in instance:
            evaluated += recurse(subschema)

    for keyword in ("allOf", "oneOf", "anyOf"):
        for subschema in schema.get(keyword, []):
            if _no_errors(validator.descend(instance, subschema)):
                evaluated += recurse(subschema)

    if "if" in schema:
        if validator.evolve(schema=schema["if"]).is_valid(instance):
            evaluated += recurse(schema["if"])
            if "then" in schema:
                evaluated += recurse(schema["then"])
        elif "else" in schema:
            evaluated += recurse(schema["else"])

    return evaluated


def _unevaluated_properties(
    *, draft2019: bool
) -> Callable[..., Iterator[ValidationError]]:
    def keyword(
        validator: Any, unevaluated: Any, instance: Any, schema: Any
    ) -> Iterator[ValidationError]:
        if not validator.is_type(instance, "object"):
            return
        evaluated = set(
            _evaluated_keys(validator, instance, schema, draft2019=draft2019)
        )
        invalid = [
            name
            for name in instance
            if name not in evaluated
            and not _no_errors(
                validator.descend(
                    instance[name], unevaluated, path=name, schema_path=name
                )
            )
        ]
        if not invalid:
            return
        if unevaluated is False:
            error = "Unevaluated properties are not allowed (%s %s unexpected)"
            yield ValidationError(error % _extras_msg(sorted(invalid, key=str)))
        else:
            error = (
                "Unevaluated properties are not valid under "
                "the given schema (%s %s unevaluated and invalid)"
            )
            yield ValidationError(error % _extras_msg(invalid))

    return keyword


_SAFE_KEYWORDS: dict[str, dict[str, Callable[..., Any]]] = {
    "draft-07": {},
    "2019-09": {"unevaluatedProperties": _unevaluated_properties(draft2019=True)},
    "2020-12": {"unevaluatedProperties": _unevaluated_properties(draft2019=False)},
}
_COMMON_SAFE_KEYWORDS = {
    "additionalProperties": _validate_additional_properties,
    "pattern": _validate_pattern,
    "patternProperties": _validate_pattern_properties,
}
_BASE_VALIDATORS = {
    "draft-07": Draft7Validator,
    "2019-09": Draft201909Validator,
    "2020-12": Draft202012Validator,
}


def _family_evolve(family: Mapping[str, type]) -> Callable[..., Any]:
    """Keep ``evolve`` within the safe validator family.

    Stock ``evolve`` selects global classes when a target declares ``$schema``,
    losing safe regex and GTS keywords. Use the family's dialect class instead.
    """

    def evolve(self: Any, **changes: Any) -> Any:
        cls = type(self)
        schema = changes.setdefault("schema", self.schema)
        new_cls = cls
        if isinstance(schema, dict) and "$schema" in schema:
            try:
                new_cls = family[schema_dialect.require_dialect(schema["$schema"])]
            except ValueError:
                new_cls = cls
        for field in attrs.fields(cls):
            if field.init and field.alias not in changes:
                changes[field.alias] = getattr(self, field.name)
        return new_cls(**changes)

    return evolve


def validator_family(
    extra_keywords: Mapping[str, Callable[..., Any]] | None = None,
) -> Mapping[str, type]:
    """Build safe validator classes keyed by dialect, without global registration.

    Each uses safe regex keywords, the shared format checker and ``extra_keywords``;
    descent and reference resolution keep validators within the family.
    """
    family: dict[str, type] = {}
    for dialect, base in _BASE_VALIDATORS.items():
        keywords = {
            **_COMMON_SAFE_KEYWORDS,
            **_SAFE_KEYWORDS[dialect],
            **(extra_keywords or {}),
        }
        cls = validators.extend(base, keywords, format_checker=FORMAT_CHECKER)
        cls.evolve = _family_evolve(family)  # type: ignore[attr-defined]
        family[dialect] = cls
    return family


_DEFAULT_FAMILY = validator_family()


def validator_for(
    schema: Any,
    *,
    inherited_dialect: str | None = None,
    family: Mapping[str, type] | None = None,
) -> Any:
    dialect = schema_dialect.effective_dialect(schema, inherited_dialect)
    return (family or _DEFAULT_FAMILY)[dialect]


def check_schema(schema: Any, dialect: Any = None) -> None:
    """Validate against the dialect's meta-schema using the safe validator.

    Stock ``check_schema`` reselects a global class; using the safe class keeps
    meta-schema patterns and regex formats within the GTS profile.
    """
    cls = validator_for(dialect or schema)
    meta_validator = cls(cls.META_SCHEMA, format_checker=FORMAT_CHECKER)
    for error in meta_validator.iter_errors(schema):
        raise SchemaError.create_from(error)


# --- schema-position preflight (spec sec 11.0.1 "Schema validation") --------

_SPECIFICATIONS = {
    "draft-07": DRAFT7,
    "2019-09": DRAFT201909,
    "2020-12": DRAFT202012,
}

# Schema positions by dialect, including legacy definitions/dependencies in
# modern meta-schemas. Literal data and unknown keywords are visited only via refs.
_SUBSCHEMA_MAPS = {
    "draft-07": {"definitions", "dependencies", "patternProperties", "properties"},
    "2019-09": {
        "$defs",
        "definitions",
        "dependencies",
        "dependentSchemas",
        "patternProperties",
        "properties",
    },
    "2020-12": {
        "$defs",
        "definitions",
        "dependencies",
        "dependentSchemas",
        "patternProperties",
        "properties",
    },
}
_SUBSCHEMA_ARRAYS = {
    "draft-07": {"allOf", "anyOf", "items", "oneOf"},
    "2019-09": {"allOf", "anyOf", "items", "oneOf"},
    "2020-12": {"allOf", "anyOf", "oneOf", "prefixItems"},
}
_SUBSCHEMA_SINGLE = {
    "draft-07": {
        "additionalItems",
        "additionalProperties",
        "contains",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "x-gts-traits-schema",
    },
    "2019-09": {
        "additionalItems",
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
        "x-gts-traits-schema",
    },
    "2020-12": {
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
        "x-gts-traits-schema",
    },
}
_REF_KEYWORDS = {
    "draft-07": ("$ref",),
    "2019-09": ("$ref", "$recursiveRef"),
    "2020-12": ("$ref", "$dynamicRef"),
}


def _join(path: str, segment: Any) -> str:
    return f"{path}/{segment}" if path else str(segment)


def check_schema_regexes(
    schema: Any,
    *,
    dialect: str | None = None,
    registry: Registry | None = None,
    compile_patterns: bool = False,
    on_unresolved: Callable[[str], None] | None = None,
) -> None:
    """Reject unsupported regexes in all schema positions, including inactive ones.

    Follow dialect keywords, ``x-gts-traits-schema`` and local/external references,
    including recursive/dynamic targets and literal data. Unresolved refs are left
    to validation. Raise ``UnsupportedRegexError`` with the first unsupported location.
    With ``compile_patterns``, also compile each expression so that an engine
    failure surfaces with the schema; it raises ``RegexEvaluationError``.
    ``on_unresolved`` may report deferred errors for reached reference targets.
    """
    check = (
        safe_regex.check_compiles if compile_patterns else safe_regex.check_supported
    )

    def check_at(pattern: object, location: str) -> None:
        try:
            check(pattern)
        except UnsupportedRegexError as error:
            raise error.at(location) from None
        except RegexEvaluationError as error:
            raise RegexEvaluationError(f"{error} at '{location}'") from error

    root_dialect = schema_dialect.effective_dialect(schema, dialect)
    if not isinstance(schema, dict):
        return
    resolver = (registry or Registry()).resolver_with_root(
        _SPECIFICATIONS[root_dialect].create_resource(schema)
    )
    stack: list[tuple[Any, str, Any, str, bool]] = [
        (schema, root_dialect, resolver, "", True)
    ]
    seen: set[tuple[int, str]] = set()
    # Resource roots and dynamic anchors, for $recursiveRef/$dynamicRef targets
    # that depend on the dynamic scope rather than the static reference.
    resources: dict[tuple[int, str], tuple[Any, str, Any, str]] = {}
    dynamic_refs: set[tuple[str, str]] = set()

    def scope_key(node: Any, node_resolver: Any) -> tuple[int, str]:
        # referencing exposes no public base-URI accessor. Identity alone loses
        # visits reached with different scopes through pointers and descent.
        return id(node), node_resolver._base_uri

    def lookup(target_resolver: Any, ref: str, node_dialect: str, path: str) -> None:
        try:
            resolved = target_resolver.lookup(ref)
        except (Unresolvable, LookupError):
            if on_unresolved is not None:
                on_unresolved(ref)
            return
        stack.append((resolved.contents, node_dialect, resolved.resolver, path, True))

    def dynamic_targets() -> None:
        for ref, ref_path in sorted(dynamic_refs):
            for root, root_dialect_, root_resolver, _ in list(resources.values()):
                if ref == "#":
                    if isinstance(root, dict) and root.get("$recursiveAnchor") is True:
                        stack.append(
                            (root, root_dialect_, root_resolver, ref_path, True)
                        )
                else:
                    lookup(root_resolver, ref, root_dialect_, ref_path)

    while True:
        while stack:
            node, node_dialect, node_resolver, path, already_scoped = stack.pop()
            if not isinstance(node, dict):
                continue
            declared = node.get("$schema")
            if isinstance(declared, str):
                try:
                    node_dialect = schema_dialect.require_dialect(declared)
                except ValueError:
                    pass
            specification = _SPECIFICATIONS[node_dialect]
            resource = specification.create_resource(node)
            # Root and reference resolvers already include the target's $id.
            if not already_scoped:
                node_resolver = node_resolver.in_subresource(resource)
            key = scope_key(node, node_resolver)
            if key in seen:
                continue
            seen.add(key)
            if path == "" or resource.id() is not None:
                resources[key] = (node, node_dialect, node_resolver, path)

            if "pattern" in node:
                check_at(node["pattern"], _join(path, "pattern"))
            pattern_properties = node.get("patternProperties")
            if isinstance(pattern_properties, dict):
                for pattern in pattern_properties:
                    check_at(pattern, _join(path, "patternProperties"))

            for keyword in _REF_KEYWORDS[node_dialect]:
                ref = node.get(keyword)
                if not isinstance(ref, str):
                    continue
                ref_path = _join(path, keyword)
                if keyword == "$recursiveRef":
                    dynamic_refs.add(("#", ref_path))
                    ref = "#"
                elif keyword == "$dynamicRef" and ref.startswith("#"):
                    dynamic_refs.add((ref, ref_path))
                lookup(node_resolver, ref, node_dialect, ref_path)

            for keyword, value in node.items():
                child_path = _join(path, keyword)
                if keyword in _SUBSCHEMA_SINGLE[node_dialect]:
                    stack.append(
                        (value, node_dialect, node_resolver, child_path, False)
                    )
                if keyword in _SUBSCHEMA_MAPS[node_dialect] and isinstance(value, dict):
                    for name, child in value.items():
                        stack.append(
                            (
                                child,
                                node_dialect,
                                node_resolver,
                                _join(child_path, name),
                                False,
                            )
                        )
                if keyword in _SUBSCHEMA_ARRAYS[node_dialect] and isinstance(
                    value, list
                ):
                    for index, child in enumerate(value):
                        stack.append(
                            (
                                child,
                                node_dialect,
                                node_resolver,
                                f"{child_path}[{index}]",
                                False,
                            )
                        )
        # Walk again from any newly reached dynamic target; stop once the
        # dynamic scope adds no unseen schema.
        dynamic_targets()
        stack[:] = [
            entry for entry in stack if scope_key(entry[0], entry[2]) not in seen
        ]
        if not stack:
            return
