from __future__ import annotations

import re
import shlex
import uuid
from typing import Any

from . import gts_id_pattern
from ._naming import (
    GTS_PREFIX,
)
from ._naming import (
    has_scheme as _has_scheme,
)
from ._naming import (
    is_type_ref as _is_type_ref,
)
from ._naming import (
    strip_scheme as _strip_scheme,
)
from ._naming import (
    with_scheme as _with_scheme,
)

GTS_NS = uuid.uuid5(uuid.NAMESPACE_URL, "gts")
# Major and minor version components are unsigned 32-bit integers (README §2.3).
GTS_MAX_U32_VERSION = gts_id_pattern.MAX_U32
GTS_SEGMENT_TOKEN_REGEX = re.compile(r"^[a-z_][a-z0-9_]*$")
UUID_REGEX = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class GtsInvalidSegment(ValueError):
    def __init__(self, num: int, offset: int, segment: str, cause: str | None = None):
        if cause:
            super().__init__(
                f"Invalid GTS segment #{num} @ offset {offset}: '{segment}': {cause}"
            )
        else:
            super().__init__(
                f"Invalid GTS segment #{num} @ offset {offset}: '{segment}'"
            )
        self.num = num
        self.offset = offset
        self.segment = segment
        self.cause = cause


class GtsInvalidId(ValueError):
    def __init__(self, gts_id: str, cause: str | None = None):
        if cause:
            super().__init__(f"Invalid GTS identifier: {gts_id}: {cause}")
        else:
            super().__init__(f"Invalid GTS identifier: {gts_id}")
        self.gts_id = gts_id
        self.cause = cause


class GtsInvalidWildcard(ValueError):
    def __init__(self, pattern: str, cause: str | None = None):
        if cause:
            super().__init__(f"Invalid GTS wildcard pattern: {pattern}: {cause}")
        else:
            super().__init__(f"Invalid GTS wildcard pattern: {pattern}")
        self.pattern = pattern
        self.cause = cause


class GtsIdSegment(gts_id_pattern.Segment):
    """A parsed GTS segment.

    The grammar, parsing and version/wildcard bounds live in
    :mod:`gts.gts_id_pattern` (the single identifier/pattern parser, mirroring
    the Go reference's ``gtsid`` package). This thin adapter preserves the
    historical ``GtsIdSegment(num, offset, segment)`` construction API and
    raises :class:`GtsInvalidSegment`; parsed identifiers store plain
    :class:`gts_id_pattern.Segment` instances, which share the same fields.
    """

    __slots__ = ()

    def __init__(self, num: int = 0, offset: int = 0, segment: str = ""):
        super().__init__()
        try:
            parsed = gts_id_pattern.parse_segment(segment)
        except gts_id_pattern.InvalidPattern as error:
            raise GtsInvalidSegment(num, offset, segment, str(error)) from error
        for attr in gts_id_pattern.Segment.__slots__:
            setattr(self, attr, getattr(parsed, attr))


class GtsID:
    """A validated, canonical GTS identifier value object.

    Parsing, the §2.3/§10 grammar, the unsigned 32-bit version bounds and the
    UUID-tail handling are delegated to :mod:`gts.gts_id_pattern`; this class
    adds the higher-level value-object API (URI form, type/instance queries,
    UUID derivation, pattern matching and attribute-path helpers).
    """

    def __init__(self, id: str):
        # The bare canonical form is expected; ``gts://`` URI inputs are
        # stripped by URI-specific callers before reaching here. Leading and
        # trailing whitespace is tolerated for backwards compatibility (the
        # stricter pattern parser rejects it for OP#4 and the reference
        # keywords).
        raw = id.strip()
        try:
            parsed = gts_id_pattern.parse_concrete(raw)
        except gts_id_pattern.InvalidPattern as error:
            raise GtsInvalidId(id, str(error)) from error
        self.id: str = parsed.id
        self.gts_id_segments: list[gts_id_pattern.Segment] = parsed.segments
        # Duck-types as gts_id_pattern.ParsedPattern for matching/coverage.
        self.segments: list[gts_id_pattern.Segment] = parsed.segments
        last = parsed.segments[-1]
        self.uuid_tail: str | None = last.segment if last.is_uuid else None

    @property
    def uri(self) -> str:
        """This identifier rendered in ``gts://`` URI form."""
        return _with_scheme(self.id)

    @property
    def is_type(self) -> bool:
        return self.gts_id_segments[-1].is_type

    @property
    def is_instance(self) -> bool:
        return not self.is_type

    @property
    def type_id(self) -> str | None:
        return self.id if self.is_type else self.get_type_id()

    @property
    def parent_type_id(self) -> str | None:
        return self.get_type_id() if self.is_type else None

    @classmethod
    def parse_type(cls, value: str) -> GtsID:
        if not _is_type_ref(value.strip()):
            raise GtsInvalidId(value, "must end with '~'")
        return cls(value)

    def get_type_id(self) -> str | None:
        if len(self.gts_id_segments) < 2:
            return None
        return GTS_PREFIX + "".join([s.segment for s in self.gts_id_segments[:-1]])

    def to_uuid(self) -> uuid.UUID:
        # For combined anonymous instances, return the embedded UUID directly.
        if self.uuid_tail:
            return uuid.UUID(self.uuid_tail)
        return uuid.uuid5(GTS_NS, self.id)

    @classmethod
    def is_valid(cls, s: str) -> bool:
        # Only the bare canonical form is a valid id; the "gts://" URI form is
        # stripped by URI-specific callers before validation.
        if not s.startswith(GTS_PREFIX):
            return False
        try:
            _ = cls(s)
            return True
        except Exception:  # noqa: BLE001 - any parsing failure means invalid ID
            return False

    def wildcard_match(self, pattern: GtsWildcard) -> bool:
        """Whether this identifier is selected by ``pattern`` (README §10).

        Shares the single matching implementation with OP#4 and the reference
        keywords. A concrete identifier is checked by point matching; a wildcard
        identifier (candidate) by pattern coverage.
        """
        this = gts_id_pattern.ParsedPattern(self.id, self.gts_id_segments)
        other = gts_id_pattern.ParsedPattern(pattern.id, pattern.gts_id_segments)
        if any(seg.is_wildcard for seg in self.gts_id_segments):
            return gts_id_pattern.covers(other, this)
        return gts_id_pattern.matches_pattern(this, other)

    def parse_query(self, expr: str) -> tuple[str, dict[str, str]]:
        base, _, filt = expr.partition("[")
        gts_base = base.strip()
        conditions: dict[str, str] = {}
        if filt:
            filt = filt.rsplit("]", 1)[0]
            tokens = shlex.split(filt)
            for tok in tokens:
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    conditions[k.strip()] = v.strip().strip('"')
        return gts_base, conditions

    def match_query(self, obj: dict[str, Any], gts_field: str, expr: str) -> bool:
        gts_base, cond = self.parse_query(expr)
        if not self.id.startswith(gts_base):
            return False
        # Optionally ensure obj field matches this id
        if str(obj.get(gts_field, "")) != self.id:
            return False
        for k, v in cond.items():
            if str(obj.get(k)) != v:
                return False
        return True

    @classmethod
    def split_at_path(cls, gts_with_path: str) -> tuple[str, str | None]:
        if "@" not in gts_with_path:
            return gts_with_path, None
        gts, path = gts_with_path.split("@", 1)
        if not path:
            raise ValueError("Attribute path cannot be empty")
        return gts, path


class GtsWildcard(GtsID):
    """A validated GTS identifier match pattern (README §10).

    A concrete identifier or one ending in a single trailing ``*`` wildcard
    token; parsing is delegated to :mod:`gts.gts_id_pattern`.
    """

    def __init__(self, pattern: str):
        raw = pattern.strip()
        try:
            parsed = gts_id_pattern.parse_pattern(raw)
        except gts_id_pattern.InvalidPattern as error:
            raise GtsInvalidWildcard(pattern, str(error)) from error
        self.id = parsed.id
        self.gts_id_segments = parsed.segments
        self.segments = parsed.segments
        last = parsed.segments[-1]
        self.uuid_tail = last.segment if last.is_uuid else None


class GtsRef:
    """Classification of a JSON Schema ``$ref`` value used in GTS documents.

    A ``$ref`` is exactly one of three kinds:

    - :attr:`LOCAL` - a same-document JSON Pointer (``#`` or ``#/...``).
    - :attr:`GTS` - a reference to a GTS type, either as a ``gts://`` URI or in
      the bare ``gts.`` form.
    - :attr:`OTHER` - anything else (e.g. an external URL); not resolvable as a
      GTS reference.

    Parsing normalizes the target once (see :attr:`target_id`) so callers never
    strip the ``gts://`` scheme themselves. This is the single classifier for
    ``$ref`` handling shared by the store, entity extraction and validation.
    """

    LOCAL = "local"
    GTS = "gts"
    OTHER = "other"

    def __init__(self, raw: str, kind: str, target_id: str, has_scheme: bool) -> None:
        self.raw = raw
        self.kind = kind
        # Canonical bare target for non-local refs; empty for local pointers
        # (use :attr:`is_local` to distinguish).
        self.target_id = target_id
        # Whether a GTS ref was written in explicit ``gts://`` URI form.
        self.has_scheme = has_scheme

    @classmethod
    def parse(cls, raw: str) -> GtsRef:
        if raw.startswith("#"):
            return cls(raw, cls.LOCAL, "", False)
        scheme = _has_scheme(raw)
        target = _strip_scheme(raw)
        if scheme or target.startswith(GTS_PREFIX):
            return cls(raw, cls.GTS, target, scheme)
        return cls(raw, cls.OTHER, target, False)

    @property
    def is_local(self) -> bool:
        return self.kind == self.LOCAL

    @property
    def is_gts(self) -> bool:
        return self.kind == self.GTS
