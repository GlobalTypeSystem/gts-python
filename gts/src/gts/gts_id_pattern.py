"""GTS identifier pattern parsing, matching and coverage (README §2.3, §10).

This module is a self-contained implementation of the GTS identifier *pattern*
grammar and the two relations the specification builds on it:

- :func:`matches_pattern` — whether a concrete identifier is selected by a
  pattern (``candidate ∈ M(pattern)``), used by OP#4 point matching and by the
  ``x-gts-type-ref`` / ``x-gts-instance-ref`` candidate checks.
- :func:`covers` — whether every identifier selected by one pattern is also
  selected by another (``M(value) ⊆ M(operand)``), used by OP#4 pattern
  matching, ``x-gts-pattern-ref`` and the reference derivation/evolution
  relations.

A *pattern* is a validated GTS identifier that may end in a single trailing
``*`` wildcard token but may also be fully concrete. A zero-wildcard pattern is
not an exact match: a base type id used as a pattern also matches everything
derived from it down the chain, with minor-version flexibility.

It is a faithful port of the Go reference implementation
(``gts-go/gtsid/{id,parse,coverage,match}.go``) and deliberately depends only on
the grammar markers, mirroring that layer's isolation from the rest of the SDK.
"""

from __future__ import annotations

import re

from ._naming import GTS_PREFIX

PREFIX = GTS_PREFIX
TYPE_MARKER = "~"
WILDCARD_MARKER = "*"
TOKEN_SEP = "."
SEGMENT_SEP = TYPE_MARKER
MAX_ID_LENGTH = 1024
MAX_U32 = 4294967295

_SEGMENT_TOKEN_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class InvalidPattern(ValueError):
    """A GTS identifier or pattern string is not well formed (§2.3, §10)."""


def has_prefix(s: str) -> bool:
    return s.startswith(PREFIX)


def has_wildcard(s: str) -> bool:
    return WILDCARD_MARKER in s


def is_type_id(s: str) -> bool:
    """Whether a concrete (non-wildcard) id spelling denotes a type."""
    return s.endswith(TYPE_MARKER)


class Segment:
    """A parsed segment of a GTS identifier or pattern."""

    __slots__ = (
        "has_version",
        "is_type",
        "is_uuid",
        "is_wildcard",
        "namespace",
        "package",
        "segment",
        "type",
        "vendor",
        "ver_major",
        "ver_minor",
    )

    def __init__(self) -> None:
        self.segment: str = ""
        self.vendor: str = ""
        self.package: str = ""
        self.namespace: str = ""
        self.type: str = ""
        self.ver_major: int | None = 0
        self.ver_minor: int | None = None
        self.has_version: bool = False
        self.is_type: bool = False
        self.is_wildcard: bool = False
        self.is_uuid: bool = False

    def ver_major_opt(self) -> tuple[int, bool]:
        return (self.ver_major or 0), self.has_version

    def uuid_tail(self) -> tuple[str, bool]:
        return (self.segment, True) if self.is_uuid else ("", False)

    @property
    def _is_uuid_tail(self) -> bool:
        # Backwards-compatible alias used by the ``GtsID`` value object and
        # schema-cast, which mark the synthetic UUID tail of a combined
        # anonymous instance (spec §3.7).
        return self.is_uuid


class ParsedPattern:
    """A validated GTS identifier or match pattern."""

    __slots__ = ("id", "segments")

    def __init__(self, raw: str, segments: list[Segment]) -> None:
        self.id = raw
        self.segments = segments

    def is_type(self) -> bool:
        return is_type_id(self.id)

    def is_wildcard(self) -> bool:
        return any(seg.is_wildcard for seg in self.segments)


def _split_preserving_tilde(s: str) -> list[str]:
    _parts = s.split(SEGMENT_SEP)
    parts: list[str] = []
    for i in range(len(_parts)):
        if i < len(_parts) - 1:
            parts.append(_parts[i] + SEGMENT_SEP)
            if i == len(_parts) - 2 and _parts[i + 1] == "":
                break
        else:
            parts.append(_parts[i])
    return parts


def _is_uuid_segment(s: str) -> bool:
    return _UUID_RE.match(s) is not None


def _parse_segment(segment: str) -> Segment:
    seg = Segment()
    seg.segment = segment.strip()
    working = seg.segment

    if working.count(TYPE_MARKER) > 0:
        if working.count(TYPE_MARKER) > 1:
            raise InvalidPattern("Too many '~' characters")
        if working.endswith(TYPE_MARKER):
            seg.is_type = True
            working = working[:-1]
        else:
            raise InvalidPattern(" '~' must be at the end")

    tokens = working.split(TOKEN_SEP)

    if len(tokens) > 6:
        raise InvalidPattern("Too many tokens")

    if not working.endswith(WILDCARD_MARKER) and len(tokens) < 5:
        raise InvalidPattern("Too few tokens")

    # Validate the name tokens (first up to four). A bare '*' is permitted only
    # as the final token; every other name token must be a valid segment token.
    name_tokens = min(len(tokens), 4)
    for t in range(name_tokens):
        if tokens[t] == WILDCARD_MARKER:
            if t != len(tokens) - 1:
                raise InvalidPattern("Wildcard '*' is only allowed as the final token")
            break
        if not _SEGMENT_TOKEN_RE.match(tokens[t]):
            raise InvalidPattern("Invalid segment token: " + tokens[t])

    if len(tokens) > 0:
        if tokens[0] == WILDCARD_MARKER:
            seg.is_wildcard = True
            return seg
        seg.vendor = tokens[0]

    if len(tokens) > 1:
        if tokens[1] == WILDCARD_MARKER:
            seg.is_wildcard = True
            return seg
        seg.package = tokens[1]

    if len(tokens) > 2:
        if tokens[2] == WILDCARD_MARKER:
            seg.is_wildcard = True
            return seg
        seg.namespace = tokens[2]

    if len(tokens) > 3:
        if tokens[3] == WILDCARD_MARKER:
            seg.is_wildcard = True
            return seg
        seg.type = tokens[3]

    if len(tokens) > 4:
        if tokens[4] == WILDCARD_MARKER:
            seg.is_wildcard = True
            return seg
        # Glued version wildcard `v*`: any version (§10); only as final token.
        if tokens[4] == "v" + WILDCARD_MARKER:
            if len(tokens) != 5:
                raise InvalidPattern("Wildcard '*' is only allowed as the final token")
            seg.is_wildcard = True
            return seg
        if not tokens[4].startswith("v"):
            raise InvalidPattern("Major version must start with 'v'")
        major_str = tokens[4][1:]
        seg.ver_major = _parse_version(major_str, "Major")
        seg.has_version = True

    if len(tokens) > 5:
        if tokens[5] == WILDCARD_MARKER:
            seg.is_wildcard = True
            return seg
        seg.ver_minor = _parse_version(tokens[5], "Minor")

    return seg


def parse_segment(segment: str) -> Segment:
    """Parse a single GTS segment in isolation (no chain/single-segment rules)."""
    return _parse_segment(segment)


def _parse_version(text: str, label: str) -> int:
    if not text or not text.isdecimal():
        raise InvalidPattern(f"{label} version must be an integer")
    value = int(text)
    # Reject leading zeros ("01") which isdecimal accepts.
    if str(value) != text:
        raise InvalidPattern(f"{label} version must be an integer")
    if value > MAX_U32:
        raise InvalidPattern(f"{label} version must be an unsigned 32-bit integer")
    return value


def _parse_gts_id(raw: str) -> ParsedPattern:
    if raw.strip() != raw:
        raise InvalidPattern("Must not contain leading or trailing whitespace")
    if raw != raw.lower():
        raise InvalidPattern("Must be lower case")
    if not has_prefix(raw):
        raise InvalidPattern(f"Does not start with '{PREFIX}'")
    if len(raw) > MAX_ID_LENGTH:
        raise InvalidPattern("Too long")

    remainder = raw[len(PREFIX) :]
    parts = _split_preserving_tilde(remainder)

    has_uuid_tail = len(parts) >= 2 and _is_uuid_segment(parts[-1])

    gts_parts_to_check = parts[:-1] if has_uuid_tail else parts
    for p in gts_parts_to_check:
        if "-" in p:
            raise InvalidPattern("Must not contain '-'")

    segments: list[Segment] = []
    for i, part in enumerate(parts):
        if part == "":
            raise InvalidPattern(f"GTS segment #{i + 1} is empty")
        if has_uuid_tail and i == len(parts) - 1:
            seg = Segment()
            seg.segment = part
            seg.is_uuid = True
            seg.ver_major = None
            segments.append(seg)
            continue
        segments.append(_parse_segment(part))

    parsed = ParsedPattern(raw, segments)
    if len(segments) == 1 and not parsed.is_type() and not segments[0].is_wildcard:
        raise InvalidPattern(
            "Single-segment instances are prohibited. "
            "Well-known instances must be chained with a type segment"
        )
    return parsed


def _count_wildcards(s: str) -> int:
    return s.count(WILDCARD_MARKER)


def parse_pattern(pattern: str) -> ParsedPattern:
    """Parse a GTS identifier match pattern.

    A concrete GTS identifier or one ending in a single trailing ``*`` wildcard
    token. Raises :class:`InvalidPattern` for any malformed input.
    """
    if pattern.strip() != pattern:
        raise InvalidPattern("must not contain leading or trailing whitespace")
    if not has_prefix(pattern):
        raise InvalidPattern(f"Does not start with '{PREFIX}'")
    count = _count_wildcards(pattern)
    if count > 1:
        raise InvalidPattern("The wildcard '*' token is allowed only once")
    if count == 1 and not pattern.endswith(WILDCARD_MARKER):
        raise InvalidPattern(
            "The wildcard '*' token is allowed only at the end of the pattern"
        )
    return _parse_gts_id(pattern)


def parse_concrete(candidate: str) -> ParsedPattern:
    """Parse a concrete (non-wildcard) GTS identifier, validated as spelled."""
    return _parse_gts_id(candidate)


# --------------------------------------------------------------------------
# Point matching: candidate ∈ M(pattern) (README §10).
# --------------------------------------------------------------------------


def matches_pattern(candidate: ParsedPattern, pattern: ParsedPattern) -> bool:
    return _matches_views(pattern.segments, candidate.segments)


def _matches_views(pattern_segs: list[Segment], candidate: list[Segment]) -> bool:
    if not pattern_segs:
        return False
    last = pattern_segs[-1]
    matches_empty_suffix = last.is_wildcard and last.segment == WILDCARD_MARKER
    required_len = len(pattern_segs) - (1 if matches_empty_suffix else 0)
    if required_len > len(candidate):
        return False

    for i, p_seg in enumerate(pattern_segs):
        if i == len(candidate):
            return matches_empty_suffix and i == len(pattern_segs) - 1
        c_seg = candidate[i]

        if p_seg.is_wildcard:
            if p_seg.vendor and p_seg.vendor != c_seg.vendor:
                return False
            if p_seg.package and p_seg.package != c_seg.package:
                return False
            if p_seg.namespace and p_seg.namespace != c_seg.namespace:
                return False
            if p_seg.type and p_seg.type != c_seg.type:
                return False
            p_major, p_has = p_seg.ver_major_opt()
            if p_has:
                c_major, c_has = c_seg.ver_major_opt()
                if not c_has or p_major != c_major:
                    return False
                # `v1.*` requires an explicit minor in the concrete id.
                if c_seg.ver_minor is None:
                    return False
            # A pinned minor in the wildcard segment must match exactly.
            return p_seg.ver_minor is None or (
                c_seg.ver_minor is not None and p_seg.ver_minor == c_seg.ver_minor
            )

        p_uuid, p_is_uuid = p_seg.uuid_tail()
        if p_is_uuid:
            c_uuid, c_is_uuid = c_seg.uuid_tail()
            if not c_is_uuid or p_uuid != c_uuid:
                return False

        if (
            p_seg.vendor != c_seg.vendor
            or p_seg.package != c_seg.package
            or p_seg.namespace != c_seg.namespace
            or p_seg.type != c_seg.type
        ):
            return False
        if not _major_eq(p_seg, c_seg):
            return False
        if p_seg.ver_minor is not None and (
            c_seg.ver_minor is None or p_seg.ver_minor != c_seg.ver_minor
        ):
            return False
        if p_seg.is_type != c_seg.is_type:
            return False
    return True


def _major_eq(a: Segment, b: Segment) -> bool:
    am, aok = a.ver_major_opt()
    bm, bok = b.ver_major_opt()
    return aok == bok and (not aok or am == bm)


def _minor_eq(a: Segment, b: Segment) -> bool:
    if a.ver_minor is None:
        return b.ver_minor is None
    return b.ver_minor is not None and a.ver_minor == b.ver_minor


# --------------------------------------------------------------------------
# Coverage: M(value) ⊆ M(operand) (README §10).
# --------------------------------------------------------------------------

_SEL_INSTANCE = 0
_SEL_PREFIXED = 1
_MIN_SEGMENT_LEN = 10  # "a.a.a.a.v0"


class _Selection:
    __slots__ = ("kind", "length", "next", "tail", "types")

    def __init__(self, kind: int) -> None:
        self.kind = kind
        self.types: list[Segment] = []
        self.tail: Segment | None = None
        self.length: int = 0
        self.next: Segment | None = None


def _selection_of(pattern: ParsedPattern) -> _Selection:
    segs = pattern.segments
    end = len(segs) - 1
    last = segs[end]
    if last.is_wildcard:
        sel = _Selection(_SEL_PREFIXED)
        sel.types = segs[:end]
        sel.next = None if last.segment == WILDCARD_MARKER else last
        return sel
    if last.is_type:
        sel = _Selection(_SEL_PREFIXED)
        sel.types = segs
        sel.next = None
        return sel
    sel = _Selection(_SEL_INSTANCE)
    sel.types = segs[:end]
    sel.tail = last
    sel.length = len(pattern.id)
    return sel


def _shortest_selected(s: _Selection) -> int:
    if s.kind == _SEL_INSTANCE:
        return s.length
    chain = len(PREFIX)
    for segment in s.types:
        chain += len(segment.segment)
    if s.next is None and len(s.types) > 0:
        return chain
    segment_len = _MIN_SEGMENT_LEN if s.next is None else _shortest_partial(s.next)
    if len(s.types) == 0:
        return chain + segment_len + 1
    return chain + segment_len


def _shortest_partial(partial: Segment) -> int:
    if partial.ver_major_opt()[1]:
        # In `v1.*`, replacing `*` with the shortest minor (`0`) keeps length.
        return len(partial.segment)
    total = 6
    for name in (partial.vendor, partial.package, partial.namespace, partial.type):
        total += 1 if len(name) == 0 else len(name)
    return total


def covers(operand: ParsedPattern, value: ParsedPattern) -> bool:
    """Whether every GTS id matched by ``value`` is also matched by ``operand``."""
    v = _selection_of(value)
    shortest = _shortest_selected(v)
    return shortest > MAX_ID_LENGTH or _covers_nonempty(
        _selection_of(operand), v, shortest == MAX_ID_LENGTH
    )


def _same_names(a: Segment, b: Segment) -> bool:
    return (
        a.vendor == b.vendor
        and a.package == b.package
        and a.namespace == b.namespace
        and a.type == b.type
    )


def _segment_covers(a: Segment, b: Segment) -> bool:
    return (
        _same_names(a, b)
        and _major_eq(a, b)
        and (a.ver_minor is None or _minor_eq(a, b))
    )


def _same_chain_covers(a: list[Segment], b: list[Segment]) -> bool:
    if len(a) != len(b):
        return False
    return all(_segment_covers(a[i], b[i]) for i in range(len(a)))


def _partial_names_cover(a: Segment, b: Segment) -> bool:
    return (
        (a.vendor == "" or a.vendor == b.vendor)
        and (a.package == "" or a.package == b.package)
        and (a.namespace == "" or a.namespace == b.namespace)
        and (a.type == "" or a.type == b.type)
    )


def _partial_admits(partial: Segment, segment: Segment) -> bool:
    if not _partial_names_cover(partial, segment):
        return False
    p_major, ok = partial.ver_major_opt()
    if not ok:
        return True
    s_major, s_ok = segment.ver_major_opt()
    return s_ok and s_major == p_major and segment.ver_minor is not None


def _partial_covers(a: Segment, b: Segment) -> bool:
    if not _partial_names_cover(a, b):
        return False
    a_major, ok = a.ver_major_opt()
    if not ok:
        return True
    b_major, b_ok = b.ver_major_opt()
    return b_ok and b_major == a_major


def _prefixed_covers(
    a: list[Segment], a_next: Segment | None, b: list[Segment], b_next: Segment | None
) -> bool:
    if len(a) > len(b):
        # A first-segment minor wildcard selects types contained in a bare type
        # scope with the same names and major and an omitted minor.
        if len(a) == 1 and a_next is None and len(b) == 0 and b_next is not None:
            only = a[0]
            _, only_has_major = only.ver_major_opt()
            _, partial_has_major = b_next.ver_major_opt()
            return (
                only.ver_minor is None
                and _same_names(only, b_next)
                and only_has_major == partial_has_major
                and (not only_has_major or only.ver_major == b_next.ver_major)
            )
        return False
    for i in range(len(a)):
        if not _segment_covers(a[i], b[i]):
            return False
    if a_next is None:
        return True
    if len(b) > len(a):
        return _partial_admits(a_next, b[len(a)])
    return b_next is not None and _partial_covers(a_next, b_next)


def _covers_nonempty(operand: _Selection, value: _Selection, at_limit: bool) -> bool:
    if operand.kind == _SEL_INSTANCE and value.kind == _SEL_INSTANCE:
        # Instance selections always carry a tail segment (see _selection_of).
        assert operand.tail is not None and value.tail is not None
        if not _same_chain_covers(operand.types, value.types):
            return False
        a_uuid, aok = operand.tail.uuid_tail()
        b_uuid, bok = value.tail.uuid_tail()
        if aok and bok:
            return a_uuid == b_uuid
        if not aok and not bok:
            return _segment_covers(operand.tail, value.tail)
        return False
    if operand.kind == _SEL_INSTANCE and value.kind == _SEL_PREFIXED:
        if not at_limit or value.next is None:
            return False
        assert operand.tail is not None
        tail = operand.tail
        partial = value.next
        _, tail_has_major = tail.ver_major_opt()
        _, partial_has_major = partial.ver_major_opt()
        _, tail_is_uuid = tail.uuid_tail()
        return (
            _same_chain_covers(operand.types, value.types)
            and not tail_is_uuid
            and tail.ver_minor is None
            and _same_names(tail, partial)
            and tail_has_major == partial_has_major
            and (not tail_has_major or tail.ver_major == partial.ver_major)
        )
    if operand.kind == _SEL_PREFIXED and value.kind == _SEL_INSTANCE:
        assert value.tail is not None
        types = operand.types
        ids = value.types
        if len(ids) < len(types):
            return False
        for i in range(len(types)):
            if not _segment_covers(types[i], ids[i]):
                return False
        if operand.next is None:
            return True
        target = ids[len(types)] if len(ids) > len(types) else value.tail
        return _partial_admits(operand.next, target)
    return _prefixed_covers(operand.types, operand.next, value.types, value.next)


# --------------------------------------------------------------------------
# OP#4 entry point.
# --------------------------------------------------------------------------


def match(candidate: str, pattern: str) -> tuple[bool, str | None]:
    """Match a candidate against a pattern (README §10, OP#4).

    Returns ``(matched, error)``. A concrete candidate matches when it is in
    ``M(pattern)``; a wildcard candidate matches when the pattern covers its
    whole selection set. An invalid pattern or candidate returns ``(False,
    error)``.
    """
    try:
        pattern_id = parse_pattern(pattern)
    except InvalidPattern as e:
        return False, str(e)
    if has_wildcard(candidate):
        try:
            candidate_id = parse_pattern(candidate)
        except InvalidPattern as e:
            return False, str(e)
        return covers(pattern_id, candidate_id), None
    try:
        candidate_id = parse_concrete(candidate)
    except InvalidPattern as e:
        return False, str(e)
    return matches_pattern(candidate_id, pattern_id), None
