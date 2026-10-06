"""Type Schema evolution / derivation compatibility (spec sec 4, OP#8 & OP#12).

The compatibility relations are defined by accepted-instance-set inclusion,
NOT by structural diffing:

- backward compatibility: ``Valid(old) subset-of Valid(new)`` (new reads old data)
- forward compatibility:  ``Valid(new) subset-of Valid(old)`` (old reads new data)

Rather than re-implement the inclusion engine, this module delegates the
inclusion primitive to the ``jsonsubschema`` library and only adds the GTS
verdict vocabulary on top. Schema derivation (OP#12) reuses the same primitive
via :func:`check_accepted_set_inclusion`.

Regex constraints are factored out or reported as unprovable: ``jsonsubschema``
uses Python/greenery semantics rather than the GTS profile (spec sec 11.0.1).
"""

from __future__ import annotations

import copy
import json
from typing import Any

from jsonsubschema import isSubschema

from .schema_validation import FORMAT_CHECKER, RegexEvaluationError, validator_for

COMPATIBLE = "compatible"
INCOMPATIBLE = "incompatible"
UNKNOWN = "unknown"

# JSON Schema meta keywords and GTS extensions that carry no assertion the
# inclusion engine understands. Stripping them keeps the comparison stable and
# avoids the library reasoning about identifiers or GTS-only annotations.
_META_KEYWORDS = {"$id", "$schema", "$comment", "$anchor", "$dynamicAnchor"}

_NON_ASSERTION_KEYWORDS = {
    "$anchor",
    "$comment",
    "$defs",
    "$dynamicAnchor",
    "$id",
    "$schema",
    "default",
    "definitions",
    "deprecated",
    "description",
    "examples",
    "readOnly",
    "title",
    "writeOnly",
}


def boolean_schema_value(schema: Any) -> bool | None:
    """Return True/False when a schema is boolean-equivalent, else None.

    ``{}`` == True and ``{"not": {}}`` == False; annotation keywords are ignored.
    """
    if isinstance(schema, bool):
        return schema
    if isinstance(schema, dict):
        assertions = [
            (k, v) for k, v in schema.items() if k not in _NON_ASSERTION_KEYWORDS
        ]
        if len(assertions) > 1:
            return None
        if not assertions:
            return True
        key, inner = assertions[0]
        if key == "not":
            iv = boolean_schema_value(inner)
            return None if iv is None else (not iv)
        return None
    return None


def _finite_values(schema: Any) -> list[Any] | None:
    if not isinstance(schema, dict):
        return None
    if "const" in schema:
        return [schema["const"]]
    enum = schema.get("enum")
    return list(enum) if isinstance(enum, list) else None


def _value_constraint_makes_type_redundant(schema: dict[Any, Any]) -> bool:
    if "type" not in schema:
        return False
    values = _finite_values(schema)
    if values is None:
        return False
    try:
        validator = validator_for(
            {"type": schema["type"]}, inherited_dialect="draft-07"
        )({"type": schema["type"]})
        return all(validator.is_valid(value) for value in values)
    except RegexEvaluationError:
        raise
    except Exception:  # noqa: BLE001 - intentional broad fallback
        return False


def _finite_subset(subset: Any, superset: Any) -> bool | None:
    values = _finite_values(subset)
    if values is None:
        return None
    try:
        # Formats are asserted as at runtime, so `format: "regex"` checks the profile.
        subset_validator = validator_for(subset, inherited_dialect="draft-07")(
            subset, format_checker=FORMAT_CHECKER
        )
        superset_validator = validator_for(superset, inherited_dialect="draft-07")(
            superset, format_checker=FORMAT_CHECKER
        )
        return all(
            not subset_validator.is_valid(value) or superset_validator.is_valid(value)
            for value in values
        )
    except RegexEvaluationError:
        raise
    except Exception:  # noqa: BLE001 - intentional broad fallback
        return None


def sanitize(schema: Any) -> Any:
    """Return a copy of ``schema`` with meta/GTS-only keywords removed.

    ``$ref`` is deliberately preserved: callers resolve references before
    comparing, and a surviving ``$ref`` means the target was unresolvable.
    """
    if isinstance(schema, dict):
        # ``jsonsubschema`` rejects some otherwise-valid const/enum schemas with
        # a sibling type. The type can only be removed when it accepts every
        # enumerated value, which leaves the accepted-instance set unchanged.
        drop_type = _value_constraint_makes_type_redundant(schema)
        result = {}
        for key, value in schema.items():
            if key in _META_KEYWORDS:
                continue
            if isinstance(key, str) and key.startswith("x-gts-"):
                continue
            if key == "type" and drop_type:
                continue
            result[key] = sanitize(value)
        return result
    if isinstance(schema, list):
        return [sanitize(item) for item in schema]
    return schema


def _lower_root_unevaluated_properties(schema: Any) -> Any | None:
    if not isinstance(schema, dict) or "unevaluatedProperties" not in schema:
        return schema
    if any(
        key in schema
        for key in (
            "$ref",
            "$dynamicRef",
            "allOf",
            "anyOf",
            "oneOf",
            "if",
            "then",
            "else",
            "not",
            "dependentSchemas",
        )
    ):
        return None
    unevaluated = schema["unevaluatedProperties"]
    if (
        "additionalProperties" in schema
        and schema["additionalProperties"] != unevaluated
    ):
        return None
    result = dict(schema)
    result.pop("unevaluatedProperties")
    result.setdefault("additionalProperties", unevaluated)
    return result


def _coerce_bool_schema(schema: Any) -> Any:
    """Turn a top-level boolean schema into its object-equivalent.

    ``jsonsubschema`` only accepts object schemas as operands, so ``true`` and
    ``false`` are expressed as ``{}`` and ``{"not": {}}`` respectively.
    """
    if schema is True:
        return {}
    if schema is False:
        return {"not": {}}
    return schema


def _is_subschema(subset: Any, superset: Any) -> bool | None:
    """``Valid(subset) subset-of Valid(superset)`` or ``None`` when unprovable."""
    lowered_subset = _lower_root_unevaluated_properties(subset)
    lowered_superset = _lower_root_unevaluated_properties(superset)
    if lowered_subset is None or lowered_superset is None:
        return None
    finite_result = _finite_subset(lowered_subset, lowered_superset)
    if finite_result is not None:
        return finite_result
    factored = _factor_regex_constraints(lowered_subset, lowered_superset)
    if factored is None:
        return None
    regex_free_subset, regex_free_superset, was_factored = factored
    try:
        included = bool(
            isSubschema(
                _coerce_bool_schema(sanitize(regex_free_subset)),
                _coerce_bool_schema(sanitize(regex_free_superset)),
            )
        )
        # After factoring, only a positive answer carries over.
        return True if included else (None if was_factored else False)
    except Exception:  # noqa: BLE001 - intentional broad fallback
        return None


# --- regex-bearing operands ----------------------------------------------
#
# Keywords whose values are literal data, never schemas or expressions.
_LITERAL_KEYWORDS = {"const", "default", "enum", "examples"}


def _is_ignored(key: Any) -> bool:
    # Literal data, and GTS extensions: sanitize() strips ``x-gts-*`` before the
    # inclusion engine runs (trait schemas are compared separately, OP#13).
    return key in _LITERAL_KEYWORDS or (
        isinstance(key, str) and key.startswith("x-gts-")
    )


# Keywords whose values map names (not expressions) to subschemas.
_NAMED_SCHEMA_MAPS = {
    "$defs",
    "definitions",
    "dependencies",
    "dependentSchemas",
    "properties",
}


# Keywords that may carry a safe-profile assertion; jsonsubschema would read
# patterns with Python semantics and ignores `format: "regex"` entirely.
_REGEX_KEYWORDS = ("format", "pattern", "patternProperties", "propertyNames")
# Factor these whole; anyOf/oneOf also annotate evaluated properties and items.
_OPAQUE_CONJUNCTS = ("not", "anyOf", "oneOf")
# Keywords that change which array elements a sibling ``items`` applies to.
_ITEM_CONTEXT_KEYWORDS = ("additionalItems", "prefixItems", "unevaluatedItems")


def _has_regex(node: Any) -> bool:
    """Whether any regular expression occurs in ``node`` (conservatively)."""
    if isinstance(node, list):
        return any(_has_regex(item) for item in node)
    if not isinstance(node, dict):
        return False
    for key, value in node.items():
        if _is_ignored(key):
            continue
        if key == "pattern" or (key == "patternProperties" and value):
            return True
        if key == "format" and value == "regex":
            return True
        if key in _NAMED_SCHEMA_MAPS and isinstance(value, dict):
            if any(_has_regex(child) for child in value.values()):
                return True
        elif _has_regex(value):
            return True
    return False


def _mentions(node: Any, matches: Any) -> bool:
    """Whether ``matches(dict)`` holds for any object in ``node``, literal data
    included (``x-gts-*`` values are stripped before inclusion, so skipped)."""
    if isinstance(node, list):
        return any(_mentions(item, matches) for item in node)
    if not isinstance(node, dict):
        return False
    return matches(node) or any(
        _mentions(value, matches)
        for key, value in node.items()
        if not (isinstance(key, str) and key.startswith("x-gts-"))
    )


def _ref_may_reach_regex(schema: Any) -> bool:
    # References may reach regexes in literal data; treat inclusion as unprovable.
    def is_ref(node: dict[str, Any]) -> bool:
        return any(key in node for key in ("$ref", "$dynamicRef", "$recursiveRef"))

    def is_regex(node: dict[str, Any]) -> bool:
        return (
            "pattern" in node
            or bool(node.get("patternProperties"))
            or node.get("format") == "regex"
        )

    return _mentions(schema, is_ref) and _mentions(schema, is_regex)


def _has_keyword(node: Any, keyword: str) -> bool:
    if isinstance(node, list):
        return any(_has_keyword(item, keyword) for item in node)
    if not isinstance(node, dict):
        return False
    return any(
        key == keyword or (not _is_ignored(key) and _has_keyword(value, keyword))
        for key, value in node.items()
    )


def _collect_regex_constraints(
    schema: Any, *, is_subset: bool
) -> tuple[Any, list[tuple[tuple[Any, ...], str, Any]]] | None:
    """Split out regex constraints at positive conjunctive locations.

    Traverse root, ``properties``, ``allOf`` and single-schema ``items`` without
    sibling ``prefixItems``/``additionalItems``/``unevaluatedItems``. Collect
    regex-bearing ``not``/``anyOf``/``oneOf`` whole; regexes elsewhere return ``None``.

    Removal must widen the subset: ``patternProperties`` requires unconstrained
    ``additionalProperties`` and no ``unevaluatedProperties``. Removing
    ``anyOf``/``oneOf`` requires no unevaluated property/item constraints because
    they annotate evaluated locations.
    """
    constraints: list[tuple[tuple[Any, ...], str, Any]] = []
    stripped = copy.deepcopy(schema)
    has_unevaluated = _has_keyword(schema, "unevaluatedProperties")
    has_unevaluated_items = _has_keyword(schema, "unevaluatedItems")

    def removable(node: dict[str, Any], key: str) -> bool:
        if key in ("anyOf", "oneOf"):
            return not (has_unevaluated or has_unevaluated_items)
        if key != "patternProperties" or not is_subset:
            return True
        additional = node.get("additionalProperties", True)
        return not has_unevaluated and boolean_schema_value(additional) is True

    def walk(node: Any, location: tuple[Any, ...]) -> bool:
        if not isinstance(node, dict):
            return True
        for key in list(node):
            value = node[key]
            if _is_ignored(key):
                continue
            if (key in _REGEX_KEYWORDS or key in _OPAQUE_CONJUNCTS) and _has_regex(
                {key: value}
            ):
                if not removable(node, key):
                    return False
                constraints.append((location, key, copy.deepcopy(value)))
                del node[key]
            elif key == "properties" and isinstance(value, dict):
                for name, child in value.items():
                    if not walk(child, (*location, ("properties", name))):
                        return False
            elif key == "items" and isinstance(value, dict):
                # prefixItems shifts which elements this items location constrains.
                if any(k in node for k in _ITEM_CONTEXT_KEYWORDS) and _has_regex(value):
                    return False
                if not walk(value, (*location, ("items",))):
                    return False
            elif key == "allOf" and isinstance(value, list):
                if not all(walk(child, location) for child in value):
                    return False
            elif _has_regex({key: value}):
                return False
        return True

    if not walk(stripped, ()):
        return None
    return stripped, constraints


def _factor_regex_constraints(
    subset: Any, superset: Any
) -> tuple[Any, Any, bool] | None:
    """Return regex-free ``(subset, superset, factored)``, or ``None`` if unsound.

    Every removed superset constraint must occur identically at the same subset
    location. Removal only widens the subset, so inclusion of the stripped
    operands proves inclusion of the originals. After factoring, a negative is inconclusive.
    Operands without regexes are returned unchanged with ``factored=False``.
    """
    if _ref_may_reach_regex(subset) or _ref_may_reach_regex(superset):
        return None
    if not _has_regex(subset) and not _has_regex(superset):
        return subset, superset, False
    split_subset = _collect_regex_constraints(subset, is_subset=True)
    split_superset = _collect_regex_constraints(superset, is_subset=False)
    if split_subset is None or split_superset is None:
        return None
    stripped_subset, subset_constraints = split_subset
    stripped_superset, superset_constraints = split_superset
    # JSON spelling distinguishes bools from numbers; 1 vs 1.0 is conservative.
    subset_keys = {_json_key(item) for item in subset_constraints}
    if any(_json_key(item) not in subset_keys for item in superset_constraints):
        return None
    return stripped_subset, stripped_superset, True


def _json_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=repr)


def _verdict(result: bool | None) -> str:
    if result is None:
        return UNKNOWN
    return COMPATIBLE if result else INCOMPATIBLE


def _canonical_dialect(declared: str) -> str:
    body = declared.removesuffix("#")
    return body.removeprefix("https://").removeprefix("http://")


def dialects_differ(old_schema: Any, new_schema: Any) -> bool:
    if not isinstance(old_schema, dict) or not isinstance(new_schema, dict):
        return False
    old_dialect = old_schema.get("$schema")
    new_dialect = new_schema.get("$schema")
    return (
        isinstance(old_dialect, str)
        and isinstance(new_dialect, str)
        and _canonical_dialect(old_dialect) != _canonical_dialect(new_dialect)
    )


def check_backward_compatibility(old_schema: Any, new_schema: Any) -> str:
    """new consumers read old data: ``Valid(old) subset-of Valid(new)``."""
    if dialects_differ(old_schema, new_schema):
        return UNKNOWN
    return _verdict(_is_subschema(old_schema, new_schema))


def check_forward_compatibility(old_schema: Any, new_schema: Any) -> str:
    """old consumers read new data: ``Valid(new) subset-of Valid(old)``."""
    if dialects_differ(old_schema, new_schema):
        return UNKNOWN
    return _verdict(_is_subschema(new_schema, old_schema))


def full_verdict(backward: str, forward: str) -> str:
    if backward == INCOMPATIBLE or forward == INCOMPATIBLE:
        return INCOMPATIBLE
    if backward == COMPATIBLE and forward == COMPATIBLE:
        return COMPATIBLE
    return UNKNOWN


def check_accepted_set_inclusion(subset: Any, superset: Any) -> bool | None:
    """Shared inclusion primitive used by OP#12 derivation admission."""
    return _is_subschema(subset, superset)


# --- diagnostics (spec sec 4.4) -------------------------------------------
#
# The inclusion verdict tells you *whether* two definitions are compatible; a
# caller admitting a new version also wants to know *why* a direction failed and
# whether a level can still gain optional properties later. The Rust reference
# surfaces both (``SchemaComparison`` carries diagnostics plus the content model
# of every object level). We keep ``jsonsubschema`` as the inclusion primitive
# and add the same reporting on top of it.

OPEN = "open"
CLOSED = "closed"
PARTIAL = "partially_open"

_APPLICATOR_OBJECT_KEYWORDS = (
    "properties",
    "patternProperties",
    "$defs",
    "definitions",
)


def _level_content_model(node: dict[str, Any]) -> str:
    """Classify how one object level treats undeclared properties.

    - ``open``: accepts an undeclared property with any value;
    - ``closed``: rejects every undeclared property;
    - ``partially_open``: accepts some undeclared names or constrains their
      values (schema-valued ``additionalProperties``, ``patternProperties`` or
      ``propertyNames``).
    """
    has_pattern = bool(node.get("patternProperties"))
    has_property_names = "propertyNames" in node
    additional = node.get("additionalProperties")
    if additional is None:
        additional_kind = "open"
    else:
        boolean = boolean_schema_value(additional)
        if boolean is True:
            additional_kind = "open"
        elif boolean is False:
            additional_kind = "closed"
        else:
            additional_kind = "schema"

    if additional_kind == "closed" and not has_pattern and not has_property_names:
        return CLOSED
    if additional_kind == "open" and not has_pattern and not has_property_names:
        return OPEN
    return PARTIAL


def _is_object_level(node: Any) -> bool:
    if not isinstance(node, dict):
        return False
    if node.get("type") == "object" or "properties" in node:
        return True
    return any(
        key in node
        for key in ("additionalProperties", "patternProperties", "propertyNames")
    )


def classify_object_levels(schema: Any) -> list[dict[str, str]]:
    """Content model of every object level of a (resolved) schema.

    Returns one entry per object level, e.g.
    ``[{"path": "$", "content_model": "closed"}, ...]``. Callers use it to
    report, per level, whether a later definition can add an optional property
    there (only a ``closed`` level can, per spec sec 4.4).
    """
    levels: list[dict[str, str]] = []
    seen: set[int] = set()

    def walk(node: Any, path: str, depth: int) -> None:
        if depth > 64 or not isinstance(node, dict):
            return
        marker = id(node)
        if marker in seen:
            return
        seen.add(marker)

        if _is_object_level(node):
            levels.append({"path": path, "content_model": _level_content_model(node)})

        properties = node.get("properties")
        if isinstance(properties, dict):
            for name, child in properties.items():
                walk(child, f"{path}.{name}", depth + 1)

        # Undeclared-property and pattern-property values are object levels of
        # the instance too, so classify their schemas (a bare boolean/absent
        # additionalProperties carries no nested level).
        pattern_properties = node.get("patternProperties")
        if isinstance(pattern_properties, dict):
            for pattern, child in pattern_properties.items():
                walk(child, f"{path}.patternProperties[{pattern}]", depth + 1)
        additional = node.get("additionalProperties")
        if isinstance(additional, dict):
            walk(additional, f"{path}.additionalProperties", depth + 1)

        # Array element schemas: `items` as a single schema, and the tuple forms
        # (`prefixItems`, or `items`/`additionalItems` as a list).
        items = node.get("items")
        if isinstance(items, dict):
            walk(items, f"{path}[]", depth + 1)
        elif isinstance(items, list):
            for index, child in enumerate(items):
                walk(child, f"{path}[{index}]", depth + 1)
        prefix_items = node.get("prefixItems")
        if isinstance(prefix_items, list):
            for index, child in enumerate(prefix_items):
                walk(child, f"{path}[{index}]", depth + 1)
        additional_items = node.get("additionalItems")
        if isinstance(additional_items, dict):
            walk(additional_items, f"{path}[].additionalItems", depth + 1)

        for combinator in ("allOf", "anyOf", "oneOf"):
            branches = node.get(combinator)
            if isinstance(branches, list):
                for branch in branches:
                    walk(branch, path, depth + 1)

    walk(schema, "$", 0)
    return levels


def explain_verdict(
    verdict: str, *, backward: bool, differing_dialects: bool = False
) -> list[str]:
    """Human-readable reasons for a non-``compatible`` directional verdict.

    Pure function of an already-computed ``verdict`` (``compatible`` /
    ``incompatible`` / ``unknown``); it does not re-run the inclusion check, so
    the caller pays for the accepted-instance-set comparison exactly once.
    ``backward`` selects the direction (backward is ``Valid(old) subset-of
    Valid(new)``, forward the reverse) for message wording; ``differing_dialects``
    distinguishes an ``unknown`` caused by incomparable dialects from one the
    checker could not decide. Returns ``[]`` for a compatible verdict.
    """
    if verdict == COMPATIBLE:
        return []
    if verdict == UNKNOWN:
        if differing_dialects:
            return [
                (
                    "compatibility is unknown: the two definitions declare "
                    "different JSON Schema dialects, so their accepted-instance "
                    "sets are not comparable"
                )
            ]
        return [
            (
                "compatibility is unknown: the accepted-instance-set inclusion "
                "could not be proved or disproved for this direction"
            )
        ]
    if backward:
        return [
            (
                "backward incompatible: Valid(old) is not a subset of Valid(new); "
                "the new definition rejects instances the old definition accepts"
            )
        ]
    return [
        (
            "forward incompatible: Valid(new) is not a subset of Valid(old); the "
            "old definition rejects instances the new definition accepts"
        )
    ]
