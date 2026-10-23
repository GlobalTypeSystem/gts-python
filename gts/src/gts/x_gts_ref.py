"""GTS entity- and pattern-reference keyword validation (README §9.6; ADR-0007/0008).

This module validates the three GTS reference keywords:

- ``x-gts-type-ref``: the candidate MUST be a GTS Type Identifier selected by
  the operand.
- ``x-gts-instance-ref``: the candidate MUST be a GTS Instance Identifier
  selected by the operand.
- ``x-gts-pattern-ref``: the candidate MUST be a GTS identifier *pattern* whose
  whole selection set is covered by the operand.

The former kind-blind ``x-gts-ref`` keyword was removed in GTS 0.16; it is now
handled like any other unknown ``x-gts-*`` keyword (rejected on schema
validation).

Operand (schema) validity and candidate (value) validity are two concerns: an
invalid operand is a schema error (reported by :meth:`XGtsRefValidator.validate_schema`
and not invertible by ``not``); a value that does not satisfy the constraint is
an ordinary validation failure that composition keywords may invert. Registry
lookup applies only to the two entity-reference keywords; ``x-gts-pattern-ref``
never consults the registry.

Matching and coverage come from :mod:`gts.gts_id_pattern` (README §10), so they
agree with OP#4 point matching and pattern coverage.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jsonschema.exceptions import ValidationError

from . import gts_id_pattern as idp
from ._json_pointer import resolve as resolve_json_pointer
from ._naming import GTS_PREFIX, strip_scheme
from .gts_ref_validation import GtsRefValidationMode
from .schema_dialect import effective_dialect
from .schema_validation import (
    RegexEvaluationError,
    UnsupportedRegexError,
    iter_schema_nodes,
    map_schema_nodes,
    validator_family,
    validator_for,
)

X_GTS_TYPE_REF = "x-gts-type-ref"
X_GTS_INSTANCE_REF = "x-gts-instance-ref"
X_GTS_PATTERN_REF = "x-gts-pattern-ref"

#: The reserved ``/$id`` operand: the leaf GTS Type Schema selected for
#: validation, even where the constraint is inherited (README §9.6.1).
SELF_ID_REF = "/$id"
X_GTS_REF_SELF = SELF_ID_REF  # backward-compatible alias

#: Entity-reference keywords (kind-constrained, registry-aware).
ENTITY_REF_KEYWORDS = (X_GTS_TYPE_REF, X_GTS_INSTANCE_REF)
#: All three GTS reference keywords, in a stable order.
REF_KEYWORDS = (X_GTS_TYPE_REF, X_GTS_INSTANCE_REF, X_GTS_PATTERN_REF)


def _noun(keyword: str) -> str:
    return (
        "GTS Type Identifier"
        if keyword == X_GTS_TYPE_REF
        else "GTS Instance Identifier"
    )


def _other_noun(keyword: str) -> str:
    return (
        "GTS Instance Identifier"
        if keyword == X_GTS_TYPE_REF
        else "GTS Type Identifier"
    )


class _OperandError(Exception):
    """An operand (schema value) of a reference keyword is not usable."""


def resolve_ref_operand(
    keyword: str, operand: Any, selected_type_id: str | None
) -> tuple[idp.ParsedPattern, str]:
    """Resolve a keyword operand to its pattern and spelling (``/$id`` resolved).

    Raises :class:`_OperandError` when the operand is a schema error.
    """
    if not isinstance(operand, str):
        raise _OperandError(
            f"{keyword} operand must be a string, got {type(operand).__name__}"
        )

    if operand == SELF_ID_REF:
        if not selected_type_id:
            raise _OperandError(
                f"{keyword} operand '{SELF_ID_REF}' has no selected GTS Type "
                "Schema '$id' to resolve to"
            )
        try:
            parsed = idp.parse_concrete(selected_type_id)
        except idp.InvalidPattern:
            parsed = None  # type: ignore[assignment]
        if parsed is None or not parsed.is_type():
            raise _OperandError(
                f"{keyword} operand '{SELF_ID_REF}' resolves to '{selected_type_id}', "
                "which is not a concrete GTS Type Identifier"
            )
        return parsed, selected_type_id

    try:
        pattern = idp.parse_pattern(operand)
    except idp.InvalidPattern as error:
        raise _OperandError(
            f"{keyword} operand '{operand}': {error}; expected a GTS identifier, "
            f"a wildcard pattern or '{SELF_ID_REF}'"
        ) from error

    # x-gts-type-ref rejects a concrete Instance ID operand: it selects no types.
    if (
        keyword == X_GTS_TYPE_REF
        and not idp.has_wildcard(operand)
        and not idp.is_type_id(operand)
    ):
        raise _OperandError(
            f"{keyword} operand '{operand}' is a GTS Instance Identifier, "
            "which selects no GTS Type Identifiers"
        )
    return pattern, operand


def ref_candidate_violation(
    keyword: str, value: Any, operand: idp.ParsedPattern, spelling: str
) -> str | None:
    """Return why ``value`` violates the resolved reference constraint, or None.

    Pure structural check (kind + matching, or pattern coverage); no registry
    lookup.
    """
    if not isinstance(value, str):
        return f"Value must be a string, got {type(value).__name__}"

    if keyword == X_GTS_PATTERN_REF:
        try:
            value_pattern = idp.parse_pattern(value)
        except idp.InvalidPattern as error:
            return f"'{value}' is not a valid GTS identifier pattern: {error}"
        if not idp.covers(operand, value_pattern):
            return f"'{value}' selects GTS identifiers outside '{spelling}'"
        return None

    if idp.has_wildcard(value) or value.strip() != value:
        return f"Value '{value}' is not a valid GTS identifier"
    try:
        candidate = idp.parse_concrete(value)
    except idp.InvalidPattern:
        return f"Value '{value}' is not a valid GTS identifier"
    if (keyword == X_GTS_TYPE_REF) != candidate.is_type():
        return (
            f"Value '{value}' is a {_other_noun(keyword)}, but {keyword} "
            f"requires a {_noun(keyword)}"
        )
    if not idp.matches_pattern(candidate, operand):
        return f"Value '{value}' does not match pattern '{spelling}'"
    return None


def _make_ref_keyword(keyword: str, selected_type_id: str | None):
    """Build a ``jsonschema`` keyword handler so the reference keyword is a
    first-class assertion (``oneOf``/``anyOf``/``allOf``/``not`` resolve
    correctly). ``selected_type_id`` lets ``/$id`` resolve here; an unresolvable
    operand is deferred (no error) and caught by :meth:`XGtsRefValidator.validate_schema`.
    """

    def _keyword(validator, operand, instance, schema):
        if not isinstance(instance, str):
            return
        try:
            pattern, spelling = resolve_ref_operand(keyword, operand, selected_type_id)
        except _OperandError:
            return
        reason = ref_candidate_violation(keyword, instance, pattern, spelling)
        if reason is not None:
            yield ValidationError(reason)

    return _keyword


_EXTENDED_FAMILIES: dict[str | None, Mapping[str, type]] = {}


def extended_validator_for(schema: Any, selected_type_id: str | None = None) -> type:
    """Return the ``jsonschema`` validator class for ``schema``'s dialect,
    extended so the three GTS reference keywords are evaluated as real keywords
    during structural validation (including inside combinators).
    """
    family = _EXTENDED_FAMILIES.get(selected_type_id)
    if family is None:
        family = validator_family(
            {
                keyword: _make_ref_keyword(keyword, selected_type_id)
                for keyword in REF_KEYWORDS
            }
        )
        _EXTENDED_FAMILIES[selected_type_id] = family
    return validator_for(schema, family=family)


def _without_x_gts_ref(schema: Any) -> Any:
    """Remove the GTS reference keywords for the few places that need the bare
    structural shape (per-branch structural checks and the ``$ref`` registry)."""

    def strip(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        stripped = {
            key: value for key, value in node.items() if key not in REF_KEYWORDS
        }
        for keyword in ("oneOf", "anyOf", "allOf"):
            branches = stripped.get(keyword)
            if (
                isinstance(branches, list)
                and branches
                and all(isinstance(branch, dict) and not branch for branch in branches)
            ):
                stripped.pop(keyword, None)
        return stripped

    return map_schema_nodes(schema, strip)


def _is_x_gts_ref_only_combinator(branches: list[Any]) -> bool:
    if not branches:
        return False
    for branch in branches:
        stripped = _without_x_gts_ref(branch)
        if not isinstance(stripped, dict) or stripped:
            return False
    return True


def _is_structurally_valid(instance: Any, schema: Any, dialect: str) -> bool:
    try:
        validator = validator_for(schema, inherited_dialect=dialect)(
            _without_x_gts_ref(schema)
        )
        return validator.is_valid(instance)
    except (RegexEvaluationError, UnsupportedRegexError):
        # A regex failure fails the whole validation; it is not "not valid".
        raise
    except Exception:  # noqa: BLE001 - treat any validation error as "not valid"
        return False


class XGtsRefValidationError(Exception):
    """Exception raised when GTS reference validation fails."""

    def __init__(self, field_path: str, value: Any, ref_pattern: str, reason: str):
        super().__init__(
            f"GTS reference validation failed for field '{field_path}': {reason}"
        )
        self.field_path = field_path
        self.value = value
        self.ref_pattern = ref_pattern
        self.reason = reason


class XGtsRefValidator:
    """Validator for the GTS reference keywords in GTS schemas."""

    def __init__(
        self,
        store: Any | None = None,
        mode: GtsRefValidationMode | bool | str = GtsRefValidationMode.ANY_VALID,
        *,
        enforce_existence: bool | None = None,
        inherited_dialect: str | None = None,
    ):
        if enforce_existence is not None:
            mode = (
                GtsRefValidationMode.ANY_PRESENT
                if enforce_existence
                else GtsRefValidationMode.NONE
            )
        elif isinstance(mode, bool):
            mode = (
                GtsRefValidationMode.ANY_PRESENT if mode else GtsRefValidationMode.NONE
            )
        self.store = store
        self.mode = GtsRefValidationMode(mode)
        self.inherited_dialect = inherited_dialect
        self.referenced_ids: set[str] = set()
        self.referenced_wildcard_patterns: set[str] = set()

    @staticmethod
    def is_self_reference(value: Any) -> bool:
        return value == SELF_ID_REF

    @staticmethod
    def selected_type_id(
        schema: dict[str, Any], selected_type_id: str | None
    ) -> str | None:
        candidate = selected_type_id or schema.get("$id")
        return strip_scheme(candidate) if isinstance(candidate, str) else None

    def validate_instance(
        self,
        instance: dict[str, Any],
        schema: dict[str, Any],
        instance_path: str = "",
        selected_type_id: str | None = None,
    ) -> list[XGtsRefValidationError]:
        """Validate an instance against the reference constraints in ``schema``."""
        errors: list[XGtsRefValidationError] = []
        dialect = effective_dialect(schema, self.inherited_dialect)
        selected_type_id = self.selected_type_id(schema, selected_type_id)

        def resolve_local_ref(ref: str) -> Any | None:
            if ref != "#" and not ref.startswith("#/"):
                return None
            return resolve_json_pointer(schema, ref, default=None)

        def visit_instance(inst, sch, path, errs, refs=None):
            if not isinstance(sch, dict):
                return

            refs = refs or set()
            ref = sch.get("$ref")
            if isinstance(ref, str) and ref not in refs:
                target = resolve_local_ref(ref)
                if target is not None:
                    visit_instance(inst, target, path, errs, refs | {ref})

            if isinstance(inst, str):
                for keyword in REF_KEYWORDS:
                    if keyword in sch:
                        error = self._validate_ref_value(
                            keyword, inst, sch[keyword], path, selected_type_id
                        )
                        if error:
                            errs.append(error)

            one_of = sch.get("oneOf")
            if isinstance(one_of, list):
                if _is_x_gts_ref_only_combinator(one_of):
                    branch_errors = [
                        _validate_branch(inst, branch, path) for branch in one_of
                    ]
                    matching = sum(not branch for branch in branch_errors)
                    if matching == 0:
                        errs.append(
                            XGtsRefValidationError(
                                path, inst, "", "oneOf: no branch matched"
                            )
                        )
                    elif matching > 1:
                        errs.append(
                            XGtsRefValidationError(
                                path,
                                inst,
                                "",
                                f"oneOf: {matching} branches matched, expected exactly 1",
                            )
                        )
                else:
                    matching_branches = [
                        branch
                        for branch in one_of
                        if _is_structurally_valid(inst, branch, dialect)
                    ]
                    if len(matching_branches) == 1:
                        errs.extend(_validate_branch(inst, matching_branches[0], path))

            any_of = sch.get("anyOf")
            if isinstance(any_of, list):
                if _is_x_gts_ref_only_combinator(any_of):
                    branch_errors = [
                        _validate_branch(inst, branch, path) for branch in any_of
                    ]
                    if not any(not branch for branch in branch_errors):
                        errs.append(
                            XGtsRefValidationError(
                                path, inst, "", "anyOf: no branch matched"
                            )
                        )
                else:
                    matching_branches = [
                        branch
                        for branch in any_of
                        if _is_structurally_valid(inst, branch, dialect)
                    ]
                    branch_errors = [
                        _validate_branch(inst, branch, path)
                        for branch in matching_branches
                    ]
                    if matching_branches and not any(
                        not branch for branch in branch_errors
                    ):
                        errs.append(
                            XGtsRefValidationError(
                                path, inst, "", "anyOf: no branch matched"
                            )
                        )

            all_of = sch.get("allOf")
            if isinstance(all_of, list):
                for branch in all_of:
                    if _is_structurally_valid(inst, branch, dialect):
                        errs.extend(_validate_branch(inst, branch, path))

            properties = sch.get("properties")
            if isinstance(properties, dict) and isinstance(inst, dict):
                for prop_name, prop_schema in properties.items():
                    if prop_name in inst:
                        prop_path = f"{path}.{prop_name}" if path else prop_name
                        visit_instance(inst[prop_name], prop_schema, prop_path, errs)

            if isinstance(inst, list):
                tuple_items = sch.get("prefixItems")
                items = sch.get("items")
                if isinstance(tuple_items, list):
                    for idx, item_schema in enumerate(tuple_items[: len(inst)]):
                        item_path = f"{path}[{idx}]"
                        visit_instance(inst[idx], item_schema, item_path, errs)
                    if isinstance(items, dict):
                        for idx in range(len(tuple_items), len(inst)):
                            item_path = f"{path}[{idx}]"
                            visit_instance(inst[idx], items, item_path, errs)
                elif isinstance(items, list):
                    for idx, item_schema in enumerate(items[: len(inst)]):
                        item_path = f"{path}[{idx}]"
                        visit_instance(inst[idx], item_schema, item_path, errs)
                    additional_items = sch.get("additionalItems")
                    if isinstance(additional_items, dict):
                        for idx in range(len(items), len(inst)):
                            item_path = f"{path}[{idx}]"
                            visit_instance(inst[idx], additional_items, item_path, errs)
                elif isinstance(items, dict):
                    for idx, item in enumerate(inst):
                        item_path = f"{path}[{idx}]"
                        visit_instance(item, items, item_path, errs)

        def _validate_branch(inst, branch, path):
            branch_errors: list[XGtsRefValidationError] = []
            visit_instance(inst, branch, path, branch_errors)
            return branch_errors

        visit_instance(instance, schema, instance_path, errors)
        return errors

    def validate_schema(
        self, schema: dict[str, Any], schema_path: str = ""
    ) -> list[XGtsRefValidationError]:
        """Validate the reference-keyword operands in a schema definition."""
        errors = []
        selected_type_id = self.selected_type_id(schema, None)
        for subschema, path in iter_schema_nodes(schema):
            for keyword in REF_KEYWORDS:
                if keyword not in subschema:
                    continue
                ref_path = f"{path}/{keyword}" if path else keyword
                try:
                    resolve_ref_operand(keyword, subschema[keyword], selected_type_id)
                except _OperandError as error:
                    operand = subschema[keyword]
                    errors.append(
                        XGtsRefValidationError(
                            ref_path,
                            operand,
                            operand if isinstance(operand, str) else "",
                            str(error),
                        )
                    )
        return errors

    def validate_schema_ref_existence(
        self,
        schema: Any,
        schema_path: str = "",
        selected_type_id: str | None = None,
    ) -> list[XGtsRefValidationError]:
        """Check that each entity-reference operand resolves to a registered
        (and, under any-valid, valid) entity. Pattern-reference operands are
        never looked up (ADR-0007)."""
        if self.store is None or self.mode == GtsRefValidationMode.NONE:
            return []
        store = self.store
        selected_type_id = self.selected_type_id(schema, selected_type_id)

        def matches(pattern: str) -> bool:
            for entity_id, _ in store.items():  # noqa: PERF102 - generic store protocol
                matched, error = idp.match(entity_id, pattern)
                if error is None and matched:
                    return True
            return False

        errors: list[XGtsRefValidationError] = []
        for subschema, path in iter_schema_nodes(schema, schema_path):
            for keyword in ENTITY_REF_KEYWORDS:
                operand = subschema.get(keyword)
                if not isinstance(operand, str):
                    continue
                resolved = selected_type_id if operand == SELF_ID_REF else operand
                if not isinstance(resolved, str) or not resolved.startswith(GTS_PREFIX):
                    continue
                ref_path = f"{path}/{keyword}" if path else keyword
                if "*" in resolved:
                    if matches(resolved):
                        self.referenced_wildcard_patterns.add(resolved)
                    else:
                        errors.append(
                            XGtsRefValidationError(
                                ref_path,
                                operand,
                                resolved,
                                f"{keyword} wildcard constraint '{resolved}' has no registered match",
                            )
                        )
                elif store.get(resolved) is None:
                    errors.append(
                        XGtsRefValidationError(
                            ref_path,
                            operand,
                            resolved,
                            f"{keyword} constraint '{resolved}' is not registered",
                        )
                    )
                else:
                    self.referenced_ids.add(resolved)
        return errors

    def resolve_ref_pattern(
        self, ref_pattern: Any, selected_type_id: str | None
    ) -> str | None:
        if not isinstance(ref_pattern, str):
            return None
        if self.is_self_reference(ref_pattern):
            return selected_type_id
        return strip_scheme(ref_pattern)

    def _validate_ref_value(
        self,
        keyword: str,
        value: str,
        operand: Any,
        field_path: str,
        selected_type_id: str | None,
    ) -> XGtsRefValidationError | None:
        """Validate an instance value against a reference constraint, including
        registry existence for the entity-reference keywords."""
        try:
            pattern, spelling = resolve_ref_operand(keyword, operand, selected_type_id)
        except _OperandError as error:
            return XGtsRefValidationError(
                field_path,
                value,
                operand if isinstance(operand, str) else "",
                str(error),
            )

        reason = ref_candidate_violation(keyword, value, pattern, spelling)
        if reason is not None:
            return XGtsRefValidationError(field_path, value, spelling, reason)

        # Entity references may additionally require the candidate to be
        # registered. Pattern references never consult the registry.
        if (
            keyword in ENTITY_REF_KEYWORDS
            and self.store is not None
            and self.mode != GtsRefValidationMode.NONE
        ):
            if self.store.get(value) is None:
                return XGtsRefValidationError(
                    field_path,
                    value,
                    spelling,
                    f"Referenced entity '{value}' not found in registry",
                )
            self.referenced_ids.add(value)
        return None
