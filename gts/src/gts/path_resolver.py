from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List


@dataclass
class JsonPathResolver:
    gts_id: str
    content: Any
    path: str = ""
    value: Any = None
    resolved: bool = False
    error: str | None = None
    available_fields: List[str] = None  # type: ignore

    def _normalize(self, path: str) -> str:
        return path.replace('/', '.')

    def _split_raw_parts(self, norm: str) -> List[str]:
        return [seg for seg in norm.split('.') if seg != '']

    def _parse_part(self, seg: str) -> List[str]:
        out: List[str] = []
        buf = ''
        i = 0
        while i < len(seg):
            ch = seg[i]
            if ch == '[':
                if buf:
                    out.append(buf)
                    buf = ''
                j = seg.find(']', i + 1)
                if j == -1:
                    buf += seg[i:]
                    break
                out.append(seg[i:j + 1])
                i = j + 1
            else:
                buf += ch
                i += 1
        if buf:
            out.append(buf)
        return out

    def _parts(self, path: str) -> List[str]:
        norm = self._normalize(path)
        raw = self._split_raw_parts(norm)
        parts: List[str] = []
        for seg in raw:
            parts.extend(self._parse_part(seg))
        return parts

    def _list_available(self, node: Any, prefix: str, out: List[str]) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                p = f"{prefix}.{k}" if prefix else str(k)
                out.append(p)
                if isinstance(v, (dict, list)):
                    self._list_available(v, p, out)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                p = f"{prefix}[{i}]" if prefix else f"[{i}]"
                out.append(p)
                if isinstance(v, (dict, list)):
                    self._list_available(v, p, out)

    def _collect_from(self, node: Any) -> List[str]:
        acc: List[str] = []
        self._list_available(node, '', acc)
        return acc

    def resolve(self, path: str) -> JsonPathResolver:
        self.path = path
        self.value = None
        self.resolved = False
        self.error = None
        self.available_fields = []

        parts = self._parts(path)
        cur: Any = self.content
        for p in parts:
            if isinstance(cur, list):
                if p.startswith('[') and p.endswith(']'):
                    idx_str = p[1:-1]
                    try:
                        idx = int(idx_str)
                    except ValueError:
                        self.error = f"Expected list index at segment '{p}'"
                        self.available_fields = self._collect_from(cur)
                        return self
                else:
                    try:
                        idx = int(p)
                    except ValueError:
                        self.error = f"Expected list index at segment '{p}'"
                        self.available_fields = self._collect_from(cur)
                        return self
                if idx < 0 or idx >= len(cur):
                    self.error = f"Index out of range at segment '{p}'"
                    self.available_fields = self._collect_from(cur)
                    return self
                cur = cur[idx]
            elif isinstance(cur, dict):
                if p.startswith('[') and p.endswith(']'):
                    self.error = f"Path not found at segment '{p}' in '{path}', see available fields"
                    self.available_fields = self._collect_from(cur)
                    return self
                if p not in cur:
                    self.error = f"Path not found at segment '{p}' in '{path}', see available fields"
                    self.available_fields = self._collect_from(cur)
                    return self
                cur = cur[p]
            else:
                self.error = f"Cannot descend into {type(cur)} at segment '{p}'"
                self.available_fields = self._collect_from(cur) if isinstance(cur, (dict, list)) else []
                return self
        self.value = cur
        self.resolved = True
        return self

    def failure(self, path: str, error: str) -> JsonPathResolver:
        self.path = path
        self.value = None
        self.resolved = False
        self.error = error
        self.available_fields = []
        return self

    def to_dict(self) -> dict:
        ret = {
            "gts_id": self.gts_id,
            "path": self.path,
            "value": self.value,
            "resolved": self.resolved,
        }

        if self.error:
            ret["error"] = self.error
        if self.available_fields:
            ret["available_fields"] = self.available_fields

        return ret
