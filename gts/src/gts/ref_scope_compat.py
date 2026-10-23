"""Derivation/evolution comparison of the GTS reference keywords.

README §9.6.1 "Derivation and evolution" (ADR-0007/0008). A property that
constrains a GTS reference accepts a set of strings; a derived property is
admissible when the strings it accepts are a subset of those the base accepts.
The two entity-reference keywords compare kind-filtered scopes; x-gts-pattern-ref
compares selection-set coverage. Composition (allOf, anyOf, not) combines scopes
by intersection, union and complement.

The comparison is a sound three-valued decision (:data:`INCLUDED`,
:data:`EXCLUDED`, :data:`UNKNOWN`): it returns included or excluded only when it
can prove the relation, and unknown otherwise. OP#12 admission fails closed
(unknown is rejected); OP#8 reports unknown.

Ported from the Go reference (``gts-go/gts/ref_scope_compat.go``).
"""

from __future__ import annotations

import copy
from typing import Any

from . import gts_id_pattern as idp
from ._naming import strip_scheme
from .x_gts_ref import (
    REF_KEYWORDS,
    SELF_ID_REF,
    X_GTS_INSTANCE_REF,
    X_GTS_PATTERN_REF,
    ref_candidate_violation,
)

# Three-valued subset result.
INCLUDED = "included"
EXCLUDED = "excluded"
UNKNOWN = "unknown"

_MAX_DEPTH = 64
_MIN_INSTANCE_SEGMENT_LEN = 10  # "a.a.a.a.v0"

# refScope kinds.
_TOP = "top"
_ATOM = "atom"
_UNION = "union"
_INTER = "inter"
_NOT = "not"
_UNDECIDABLE = "undecidable"


class _Scope:
    __slots__ = ("child", "children", "empty", "kind", "pattern", "ref_keyword")

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.ref_keyword: str | None = None
        self.pattern: idp.ParsedPattern | None = None
        self.empty: bool = False
        self.children: list[_Scope] = []
        self.child: _Scope | None = None


def is_ref_keyword(key: str) -> bool:
    return key in REF_KEYWORDS


def _ref_atom(keyword: str, operand: Any) -> _Scope | None:
    """Single-keyword scope from an operand, or None when it is not usable (an
    invalid operand is a schema error caught separately)."""
    if not isinstance(operand, str) or operand == SELF_ID_REF:
        return None
    try:
        pattern = idp.parse_pattern(operand)
    except idp.InvalidPattern:
        return None
    atom = _Scope(_ATOM)
    atom.ref_keyword = keyword
    atom.pattern = pattern
    # An instance reference to a concrete Type ID too long for any instance tail
    # selects nothing (README §9.6.1 "Derivation and evolution").
    if (
        keyword == X_GTS_INSTANCE_REF
        and not idp.has_wildcard(operand)
        and idp.is_type_id(operand)
        and len(operand) + _MIN_INSTANCE_SEGMENT_LEN > idp.MAX_ID_LENGTH
    ):
        atom.empty = True
    return atom


def build_ref_scope(prop: Any, depth: int = 0) -> tuple[_Scope, bool]:
    """The reference scope a property declares, and whether it mentions any
    reference keyword at all (directly or through composition). Operands are
    expected to be already resolved (no ``/$id``)."""
    if not isinstance(prop, dict):
        return _Scope(_TOP), False
    if depth >= _MAX_DEPTH:
        return _Scope(_UNDECIDABLE), True

    parts: list[_Scope] = []
    has = False

    for keyword in REF_KEYWORDS:
        if keyword not in prop:
            continue
        has = True
        atom = _ref_atom(keyword, prop[keyword])
        if atom is not None:
            parts.append(atom)

    not_value = prop.get("not")
    if isinstance(not_value, dict):
        child_scope, child_has = build_ref_scope(not_value, depth + 1)
        if child_has:
            has = True
            node = _Scope(_NOT)
            node.child = child_scope
            parts.append(node)

    for key in ("anyOf", "oneOf"):
        branches = prop.get(key)
        if not isinstance(branches, list):
            continue
        union: list[_Scope] = []
        branch_has = False
        for branch in branches:
            if not isinstance(branch, dict):
                union.append(_Scope(_TOP))
                continue
            bs, bh = build_ref_scope(branch, depth + 1)
            if bh:
                branch_has = True
            union.append(bs)
        if branch_has:
            has = True
            node = _Scope(_UNION)
            node.children = union
            parts.append(node)

    all_of = prop.get("allOf")
    if isinstance(all_of, list):
        for branch in all_of:
            if not isinstance(branch, dict):
                continue
            bs, bh = build_ref_scope(branch, depth + 1)
            if bh:
                has = True
                parts.append(bs)

    if not parts:
        return _Scope(_TOP), has
    if len(parts) == 1:
        return parts[0], has
    node = _Scope(_INTER)
    node.children = parts
    return node, has


def relation(base: _Scope, derived: _Scope) -> str:
    """Whether Valid(derived) ⊆ Valid(base) for the reference dimension."""
    if base.kind == _UNDECIDABLE or derived.kind == _UNDECIDABLE:
        return UNKNOWN
    if base.kind == _TOP:
        return INCLUDED
    if derived.kind == _TOP:
        return EXCLUDED

    # Decompose the derived side first.
    if derived.kind == _UNION:
        result = INCLUDED
        for child in derived.children:
            r = relation(base, child)
            if r == EXCLUDED:
                return EXCLUDED
            if r == UNKNOWN:
                result = UNKNOWN
        return result
    if derived.kind == _INTER:
        for child in derived.children:
            if relation(base, child) == INCLUDED:
                return INCLUDED
        return UNKNOWN
    if derived.kind == _NOT:
        if base.kind == _NOT:
            # complement(D) ⊆ complement(B) ⟺ B ⊆ D.
            assert derived.child is not None and base.child is not None
            return relation(derived.child, base.child)
        return UNKNOWN

    # Derived is an atom; dispatch on the base side.
    if base.kind == _UNION:
        for child in base.children:
            if relation(child, derived) == INCLUDED:
                return INCLUDED
        return UNKNOWN
    if base.kind == _INTER:
        result = INCLUDED
        for child in base.children:
            r = relation(child, derived)
            if r == EXCLUDED:
                return EXCLUDED
            if r == UNKNOWN:
                result = UNKNOWN
        return result
    if base.kind == _NOT:
        return UNKNOWN
    if base.kind == _ATOM:
        return _atom_relation(base, derived)
    return UNKNOWN


def _atom_relation(base: _Scope, derived: _Scope) -> str:
    if derived.empty:
        return INCLUDED
    base_pattern_kw = base.ref_keyword == X_GTS_PATTERN_REF
    derived_pattern_kw = derived.ref_keyword == X_GTS_PATTERN_REF
    if base_pattern_kw != derived_pattern_kw:
        return UNKNOWN
    if base.ref_keyword != derived.ref_keyword:
        return EXCLUDED  # type vs instance: disjoint nonempty scopes
    assert base.pattern is not None and derived.pattern is not None
    if idp.covers(base.pattern, derived.pattern):
        return INCLUDED
    return EXCLUDED


def ref_scope_relation(base_prop: Any, derived_prop: Any) -> str:
    """Whether the derived property's reference scope is included in the base's.

    Also admits a derived property that narrows with ordinary keywords: a finite
    const/enum is included when every value lies in the base scope.
    """
    base_scope, base_has = build_ref_scope(base_prop)
    if not base_has:
        return INCLUDED  # base imposes no reference restriction
    derived_scope, derived_has = build_ref_scope(derived_prop)
    if derived_has:
        return relation(base_scope, derived_scope)
    values, enumerates = _collect_enumerated_values(derived_prop)
    if not enumerates:
        return EXCLUDED  # a plain string or regex accepts values outside the base scope
    for value in values:
        if not isinstance(value, str) or not ref_scope_accepts(base_scope, value):
            return EXCLUDED
    return INCLUDED


def _collect_enumerated_values(prop: Any) -> tuple[list[Any], bool]:
    if not isinstance(prop, dict):
        return [], False
    if "const" in prop:
        return [prop["const"]], True
    enum = prop.get("enum")
    if isinstance(enum, list):
        return list(enum), True
    return [], False


def ref_scope_accepts(scope: _Scope, value: str) -> bool:
    if scope.kind == _TOP:
        return True
    if scope.kind == _NOT:
        assert scope.child is not None
        return not ref_scope_accepts(scope.child, value)
    if scope.kind == _UNION:
        return any(ref_scope_accepts(c, value) for c in scope.children)
    if scope.kind == _INTER:
        return all(ref_scope_accepts(c, value) for c in scope.children)
    if scope.kind == _ATOM:
        if scope.empty:
            return False
        assert scope.ref_keyword is not None and scope.pattern is not None
        return (
            ref_candidate_violation(
                scope.ref_keyword, value, scope.pattern, scope.pattern.id
            )
            is None
        )
    return False


# --- effective-property folding (allOf) ------------------------------------


def _narrower_ref_operand(keyword: str, a: Any, b: Any) -> tuple[Any, bool]:
    """The operand (of two for the same keyword) selecting the smaller set, with
    ok=True; ok=False when neither covers the other or either is uncomparable."""
    atom_a = _ref_atom(keyword, a)
    atom_b = _ref_atom(keyword, b)
    if atom_a is None or atom_b is None:
        return None, False
    assert atom_a.pattern is not None and atom_b.pattern is not None
    if idp.covers(atom_a.pattern, atom_b.pattern):
        return b, True  # b ⊆ a
    if idp.covers(atom_b.pattern, atom_a.pattern):
        return a, True  # a ⊆ b
    return None, False


def merge_effective_property(prev: Any, nxt: Any) -> Any:
    """Fold a later allOf-branch declaration of a property onto an earlier one.

    Ordinary keywords follow innermost-wins; a GTS reference keyword conjoins
    like allOf (narrower operand survives, incomparable operands are kept as an
    allOf conjunction) so a wider overlay never widens the inherited scope.
    """
    if prev is None or not isinstance(prev, dict) or not isinstance(nxt, dict):
        return nxt
    merged = dict(nxt)
    for keyword in REF_KEYWORDS:
        if keyword not in prev:
            continue
        prev_operand = prev[keyword]
        if keyword not in merged:
            merged[keyword] = prev_operand
            continue
        narrower, ok = _narrower_ref_operand(keyword, prev_operand, merged[keyword])
        if ok:
            merged[keyword] = narrower
            continue
        # Incomparable operands: keep both as a conjunction.
        next_operand = merged.pop(keyword)
        existing = merged.get("allOf")
        both = list(existing) if isinstance(existing, list) else []
        both.append({keyword: prev_operand})
        both.append({keyword: next_operand})
        merged["allOf"] = both
    return merged


def _effective_properties(schema: Any, depth: int = 0) -> dict[str, Any]:
    """Property-name → effective property schema, folding allOf branches with
    reference-aware merging so an inherited narrower scope is not widened."""
    result: dict[str, Any] = {}
    if not isinstance(schema, dict) or depth >= _MAX_DEPTH:
        return result
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for branch in all_of:
            for name, prop in _effective_properties(branch, depth + 1).items():
                result[name] = merge_effective_property(result.get(name), prop)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, prop in properties.items():
            result[name] = merge_effective_property(result.get(name), prop)
    return result


# --- self-reference resolution ---------------------------------------------


def resolve_self_ref_operands(node: Any, self_id: str, depth: int = 0) -> None:
    """Replace every ``/$id`` reference-keyword operand under ``node`` with
    ``self_id`` (the bare top-level identifier of the schema being compared),
    in place."""
    if depth >= _MAX_DEPTH:
        return
    if isinstance(node, dict):
        for keyword in REF_KEYWORDS:
            if node.get(keyword) == SELF_ID_REF:
                node[keyword] = self_id
        for value in node.values():
            resolve_self_ref_operands(value, self_id, depth + 1)
    elif isinstance(node, list):
        for value in node:
            resolve_self_ref_operands(value, self_id, depth + 1)


def _self_id_of(schema: Any) -> str | None:
    if isinstance(schema, dict):
        sid = schema.get("$id")
        if isinstance(sid, str):
            return strip_scheme(sid)
    return None


def _combine(current: str, child: str) -> str:
    if child == EXCLUDED:
        return EXCLUDED
    if child == UNKNOWN and current != EXCLUDED:
        return UNKNOWN
    return current


def _schema_ref_relation(base: Any, derived: Any, depth: int) -> str:
    if depth >= _MAX_DEPTH:
        return UNKNOWN
    result = ref_scope_relation(base, derived)
    if result == EXCLUDED:
        return EXCLUDED
    base_props = _effective_properties(base)
    derived_props = _effective_properties(derived)
    for name in set(base_props) | set(derived_props):
        child = _schema_ref_relation(
            base_props.get(name, {}), derived_props.get(name, {}), depth + 1
        )
        result = _combine(result, child)
        if result == EXCLUDED:
            return EXCLUDED
    return result


def schema_ref_relation(
    base: Any,
    derived: Any,
    base_self_id: str | None = None,
    derived_self_id: str | None = None,
) -> str:
    """Three-valued reference-dimension relation ``Valid(derived) ⊆ Valid(base)``
    over two resolved schemas, resolving each schema's ``/$id`` to its own type.
    """
    base_copy = copy.deepcopy(base)
    derived_copy = copy.deepcopy(derived)
    base_id = base_self_id or _self_id_of(base)
    derived_id = derived_self_id or _self_id_of(derived)
    if base_id:
        resolve_self_ref_operands(base_copy, base_id)
    if derived_id:
        resolve_self_ref_operands(derived_copy, derived_id)
    return _schema_ref_relation(base_copy, derived_copy, 0)
