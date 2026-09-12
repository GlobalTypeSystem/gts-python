from __future__ import annotations

import re
import shlex
import uuid
from typing import Any

from ._naming import (
    GTS_PREFIX,
    GTS_TYPE_MARKER,
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


class GtsIdSegment:
    """Parsed GTS segment. Accepts a segment string in the constructor.

    The `segment` may be absolute (starts with 'gts.') or relative (no prefix).
    The original string is stored in `segment`.
    """

    def __init__(self, num: int, offset: int, segment: str):
        self.num: int = num
        self.offset: int = offset
        self.segment: str = segment.strip()

        self.vendor: str = ""
        self.package: str = ""
        self.namespace: str = ""
        self.type: str = ""
        self.ver_major: int = 0
        self.ver_minor: int | None = None
        self.is_type: bool = False
        self.is_wildcard: bool = False

        self._parse_segment_id(num, offset, segment)

    def _parse_segment_id(self, num: int, offset: int, segment: str):
        if segment.count(GTS_TYPE_MARKER) > 0:
            if segment.count(GTS_TYPE_MARKER) > 1:
                raise GtsInvalidSegment(num, offset, segment, "Too many '~' characters")
            if segment.endswith(GTS_TYPE_MARKER):
                self.is_type = True
                segment = segment[:-1]
            else:
                raise GtsInvalidSegment(num, offset, segment, " '~' must be at the end")

        tokens = segment.split(".")

        if len(tokens) > 6:
            raise GtsInvalidSegment(num, offset, segment, "Too many tokens")

        if not segment.endswith("*"):
            if len(tokens) < 5:
                raise GtsInvalidSegment(num, offset, segment, "Too few tokens")

            for t in range(4):
                if not GTS_SEGMENT_TOKEN_REGEX.match(tokens[t]):
                    raise GtsInvalidSegment(
                        num, offset, segment, "Invalid segment token: " + tokens[t]
                    )

        if len(tokens) > 0:
            if tokens[0] == "*":
                self.is_wildcard = True
                return
            self.vendor = tokens[0]

        if len(tokens) > 1:
            if tokens[1] == "*":
                self.is_wildcard = True
                return
            self.package = tokens[1]

        if len(tokens) > 2:
            if tokens[2] == "*":
                self.is_wildcard = True
                return
            self.namespace = tokens[2]

        if len(tokens) > 3:
            if tokens[3] == "*":
                self.is_wildcard = True
                return
            self.type = tokens[3]

        if len(tokens) > 4:
            if tokens[4] == "*":
                self.is_wildcard = True
                return

            if not tokens[4].startswith("v"):
                raise GtsInvalidSegment(
                    num, offset, segment, "Major version must start with 'v'"
                )
            try:
                self.ver_major = int(tokens[4][1:])
            except ValueError:
                raise GtsInvalidSegment(
                    num, offset, segment, "Major version must be an integer"
                )

            if self.ver_major < 0:
                raise GtsInvalidSegment(
                    num, offset, segment, "Major version must be >= 0"
                )
            if str(self.ver_major) != tokens[4][1:]:
                raise GtsInvalidSegment(
                    num, offset, segment, "Major version must be an integer"
                )

        if len(tokens) > 5:
            if tokens[5] == "*":
                self.is_wildcard = True
                return

            try:
                self.ver_minor = int(tokens[5])
            except ValueError:
                raise GtsInvalidSegment(
                    num, offset, segment, "Minor version must be an integer"
                )

            if self.ver_minor < 0:
                raise GtsInvalidSegment(
                    num, offset, segment, "Minor version must be >= 0"
                )
            if str(self.ver_minor) != tokens[5]:
                raise GtsInvalidSegment(
                    num, offset, segment, "Minor version must be an integer"
                )

    @classmethod
    def _uuid_tail_segment(cls, num: int, offset: int, uuid_str: str) -> GtsIdSegment:
        """Create a special UUID tail segment for combined anonymous instances."""
        seg = object.__new__(cls)
        seg.num = num
        seg.offset = offset
        seg.segment = uuid_str
        seg.vendor = ""
        seg.package = ""
        seg.namespace = ""
        seg.type = ""
        seg.ver_major = None
        seg.ver_minor = None
        seg.is_type = False
        seg.is_wildcard = False
        seg._is_uuid_tail = True
        return seg


class GtsID:
    def __init__(self, id: str):
        raw = id.strip()

        # Normalize to the canonical bare form at this boundary.
        raw = _strip_scheme(raw)

        # Validate it's lower case
        if raw != raw.lower():
            raise GtsInvalidId(id, "Must be lower case")

        if not raw.startswith(GTS_PREFIX):
            raise GtsInvalidId(id, f"Does not start with '{GTS_PREFIX}'")
        if len(raw) > 1024:
            raise GtsInvalidId(id, "Too long")

        self.id: str = raw
        self.gts_id_segments: list[GtsIdSegment] = []
        self.uuid_tail: str | None = None

        # Detect combined anonymous instance: last tilde-part is a UUID
        remainder = raw[len(GTS_PREFIX) :]
        tilde_parts = remainder.split("~")
        last_part = tilde_parts[-1] if tilde_parts else ""
        if UUID_REGEX.match(last_part) and len(tilde_parts) >= 2:
            self.uuid_tail = last_part
            # Hyphens are only allowed in the UUID tail
            segments_portion = raw[: len(raw) - len(last_part) - 1]  # strip ~<uuid>
            if "-" in segments_portion:
                raise GtsInvalidId(id, "Must not contain '-'")
        else:
            if "-" in raw:
                raise GtsInvalidId(id, "Must not contain '-'")

        # split preserving empties to detect trailing '~'
        _parts = raw[len(GTS_PREFIX) :].split("~")

        # If UUID tail, exclude it from segment parsing
        if self.uuid_tail:
            # All parts before UUID are type segments (end with ~)
            seg_count = len(_parts) - 1  # exclude UUID tail
            parts = []
            for i in range(seg_count):
                if _parts[i] == "":
                    raise GtsInvalidId(id, f"GTS segment #{i + 1} is empty")
                parts.append(_parts[i] + "~")
        else:
            parts = []
            for i in range(len(_parts)):
                if i < len(_parts) - 1:
                    parts.append(_parts[i] + "~")
                    if i == len(_parts) - 2 and _parts[i + 1] == "":
                        break
                else:
                    parts.append(_parts[i])

        offset = len(GTS_PREFIX)
        for i in range(len(parts)):
            if parts[i] == "":
                raise GtsInvalidId(
                    id, f"GTS segment #{i + 1} @ offset {offset} is empty"
                )

            self.gts_id_segments.append(GtsIdSegment(i + 1, offset, parts[i]))
            offset += len(parts[i])

        # Add UUID tail as a special segment if present
        if self.uuid_tail:
            self.gts_id_segments.append(
                GtsIdSegment._uuid_tail_segment(
                    len(self.gts_id_segments) + 1, offset, self.uuid_tail
                )
            )

        # Issue #37: Single-segment instance IDs are not allowed
        # An instance ID (not ending with ~) must be chained (have at least 2 segments)
        # UUID tail is exempt
        non_uuid_segments = [
            s for s in self.gts_id_segments if not getattr(s, "_is_uuid_tail", False)
        ]
        if (
            not self.id.endswith(GTS_TYPE_MARKER)
            and self.uuid_tail is None
            and len(non_uuid_segments) == 1
            and not any(seg.is_wildcard for seg in self.gts_id_segments)
        ):
            # Check if it's a wildcard (wildcards are allowed as single segment)
            raise GtsInvalidId(
                id,
                "Single-segment instance IDs are not allowed. "
                "Instance IDs must be chained (e.g., type~instance).",
            )

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
        # For combined anonymous instances, return the embedded UUID directly
        if self.uuid_tail:
            return uuid.UUID(self.uuid_tail)
        return uuid.uuid5(GTS_NS, self.id)

    @classmethod
    def is_valid(cls, s: str) -> bool:
        if not _strip_scheme(s).startswith(GTS_PREFIX):
            return False
        try:
            _ = cls(s)
            return True
        except Exception:  # noqa: BLE001 - any parsing failure means invalid ID
            return False

    def wildcard_match(self, pattern: GtsWildcard) -> bool:
        p = pattern.id

        # Helper function to match segments with version flexibility
        def match_segments(
            pattern_segs: list[GtsIdSegment], candidate_segs: list[GtsIdSegment]
        ) -> bool:
            # Pattern ending with '~*' means "this type and any descendants".
            # It should match both:
            # - the base type itself (same prefix, no extra segment), and
            # - instances/derived ids under that prefix.
            if (
                pattern_segs
                and pattern_segs[-1].is_wildcard
                and len(pattern_segs) == len(candidate_segs) + 1
            ):
                return match_segments(pattern_segs[:-1], candidate_segs)

            # If pattern is longer than candidate, no match
            if len(pattern_segs) > len(candidate_segs):
                return False

            for i, p_seg in enumerate(pattern_segs):
                c_seg = candidate_segs[i]

                # If pattern segment is a wildcard, check non-wildcard fields first
                if p_seg.is_wildcard:
                    # Check the fields that are set (non-empty) in the wildcard pattern
                    if p_seg.vendor and p_seg.vendor != c_seg.vendor:
                        return False
                    if p_seg.package and p_seg.package != c_seg.package:
                        return False
                    if p_seg.namespace and p_seg.namespace != c_seg.namespace:
                        return False
                    if p_seg.type and p_seg.type != c_seg.type:
                        return False
                    # Check version fields when version is explicitly present in
                    # the wildcard segment (including v0.*).
                    if ".v" in p_seg.segment and p_seg.ver_major != c_seg.ver_major:
                        return False
                    if (
                        p_seg.ver_minor is not None
                        and p_seg.ver_minor != c_seg.ver_minor
                    ):
                        return False
                    # Check is_type flag if set; if it doesn't match, wildcard fails,
                    # otherwise wildcard matches - accept anything after this point
                    return not (p_seg.is_type and p_seg.is_type != c_seg.is_type)

                # Non-wildcard segment - all fields must match exactly
                # Check vendor, package, namespace, type match
                if p_seg.vendor != c_seg.vendor:
                    return False
                if p_seg.package != c_seg.package:
                    return False
                if p_seg.namespace != c_seg.namespace:
                    return False
                if p_seg.type != c_seg.type:
                    return False

                # Check version matching
                # Major version must match
                if p_seg.ver_major != c_seg.ver_major:
                    return False

                # Minor version: if pattern has no minor version, accept any minor in candidate
                # If pattern has minor version, it must match exactly
                if p_seg.ver_minor is not None and p_seg.ver_minor != c_seg.ver_minor:
                    return False
                # else: pattern has no minor version, so any minor version in candidate is OK

                # Check is_type flag matches
                if p_seg.is_type != c_seg.is_type:
                    return False

            # If we've matched all pattern segments, it's a match
            return True

        # No wildcard case - need exact match with version flexibility
        if "*" not in p:
            # Parse both as segments and compare
            return match_segments(pattern.gts_id_segments, self.gts_id_segments)

        # Wildcard case
        if p.count("*") > 1 or not p.endswith("*"):
            return False

        # Use segment matching for wildcard patterns too
        return match_segments(pattern.gts_id_segments, self.gts_id_segments)

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
    def __init__(self, pattern: str):
        p = pattern.strip()
        if not p.startswith(GTS_PREFIX):
            raise GtsInvalidWildcard(pattern, f"Does not start with '{GTS_PREFIX}'")
        if p.count("*") > 1:
            raise GtsInvalidWildcard(
                pattern, "The wildcard '*' token is allowed only once"
            )
        if "*" in p and not p.endswith(".*") and not p.endswith("~*"):
            raise GtsInvalidWildcard(
                pattern,
                "The wildcard '*' token is allowed only at the end of the pattern",
            )
        try:
            super().__init__(p)
        except GtsInvalidId as e:
            raise GtsInvalidWildcard(pattern, str(e))


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

    def __init__(
        self, raw: str, kind: str, target_id: str, has_scheme: bool
    ) -> None:
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
