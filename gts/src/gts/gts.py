from __future__ import annotations

import re
import shlex
import uuid
from typing import Iterable, List, Optional, Set, Tuple, Dict, Any

GTS_PREFIX = "gts."
GTS_NS = uuid.uuid5(uuid.NAMESPACE_URL, "gts")


class GtsInvalidSegment(ValueError):
    def __init__(self, num: int, offset: int, segment: str, cause: Optional[str] = None):
        if cause:
            super().__init__(f"Invalid GTS segment #{num} @ offset {offset}: '{segment}': {cause}")
        else:
            super().__init__(f"Invalid GTS segment #{num} @ offset {offset}: '{segment}'")
        self.num = num
        self.offset = offset
        self.segment = segment
        self.cause = cause


class GtsInvalidId(ValueError):
    def __init__(self, gts_id: str, cause: Optional[str] = None):
        if cause:
            super().__init__(f"Invalid GTS identifier: {gts_id}: {cause}")
        else:
            super().__init__(f"Invalid GTS identifier: {gts_id}")
        self.gts_id = gts_id
        self.cause = cause


class GtsInvalidWildcard(ValueError):
    def __init__(self, pattern: str, cause: Optional[str] = None):
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
        self.ver_minor: Optional[int] = None
        self.is_type: bool = False
        self.is_wildcard: bool = False

        self._parse_segment_id(num, offset, segment)

    def _parse_segment_id(self, num: int, offset: int, segment: str):
        if segment.endswith("~"):
            self.is_type = True
            segment = segment[:-1]
        tokens = segment.split(".")
        if len(tokens) > 6:
            raise GtsInvalidSegment(num, offset, segment, "Too many tokens")
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
                raise GtsInvalidSegment(num, offset, segment, "Major version must start with 'v'")
            try:
                self.ver_major = int(tokens[4][1:])
            except ValueError:
                raise GtsInvalidSegment(num, offset, segment, "Major version must be an integer")
        if len(tokens) > 5:
            if tokens[5] == "*":
                self.is_wildcard = True
                return
            try:
                self.ver_minor = int(tokens[5])
            except ValueError:
                raise GtsInvalidSegment(num, offset, segment, "Minor version must be an integer")


class GtsID:
    def __init__(self, id: str):
        raw = id.strip()
        if not raw.startswith(GTS_PREFIX):
            raise GtsInvalidId(id, f"Does not start with '{GTS_PREFIX}'")
        if len(raw) > 1024:
            raise GtsInvalidId(id, "Too long")

        self.id: str = raw
        self.gts_id_segments: List[GtsIdSegment] = []

        # split preserving empties to detect trailing '~'
        _parts = raw[len(GTS_PREFIX):].split("~")
        parts = []
        for i in range(0, len(_parts)):
            if i < len(_parts) - 1:
                parts.append(_parts[i] + "~")
                if i == len(_parts) - 2 and _parts[i + 1] == "":
                    break
            else:
                parts.append(_parts[i])

        offset = len(GTS_PREFIX)
        for i in range(0, len(parts)):
            if parts[i] == "":
                raise GtsInvalidId(id, f"GTS segment #{i+1} @ offset {offset} is empty")

            self.gts_id_segments.append(GtsIdSegment(i+1, offset, parts[i]))
            offset += len(parts[i])

    @property
    def is_type(self) -> bool:
        return self.id.endswith("~")

    def get_type_id(self) -> Optional[str]:
        if len(self.gts_id_segments) < 2:
            return None
        return GTS_PREFIX + "".join([s.segment for s in self.gts_id_segments[:-1]])

    def to_uuid(self) -> uuid.UUID:
        return uuid.uuid5(GTS_NS, self.id)

    @classmethod
    def is_valid(cls, s: str) -> bool:
        if not s.startswith(GTS_PREFIX):
            return False
        try:
            _ = cls(s)
            return True
        except Exception:
            return False

    def wildcard_match(self, pattern: GtsWildcard) -> bool:
        p = pattern.id
        # validated by GtsWildcard constructor
        candidate = self.id
        if '*' not in p:
            return p.strip() == candidate.strip()
        if p.count('*') > 1 or not p.endswith('*'):
            return False
        prefix = p[:-1]
        return candidate.startswith(prefix)

    def parse_query(self, expr: str) -> Tuple[str, Dict[str, str]]:
        base, _, filt = expr.partition("[")
        gts_base = base.strip()
        conditions: Dict[str, str] = {}
        if filt:
            filt = filt.rsplit("]", 1)[0]
            tokens = shlex.split(filt)
            for tok in tokens:
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    conditions[k.strip()] = v.strip().strip('"')
        return gts_base, conditions

    def match_query(self, obj: Dict[str, Any], gts_field: str, expr: str) -> bool:
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
    def split_at_path(cls, gts_with_path: str) -> Tuple[str, Optional[str]]:
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
        if "*" in p and not p.endswith(".*"):
            raise GtsInvalidWildcard(pattern, "The wildcard '*' token is allowed only at the end of the pattern")
        try:
            super().__init__(p)
        except GtsInvalidId as e:
            raise GtsInvalidWildcard(pattern, str(e))
