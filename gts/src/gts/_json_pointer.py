"""RFC 6901 JSON Pointer resolution.

A JSON Pointer (RFC 6901) addresses a single value inside a JSON document, e.g.
``/properties/type``. Because ``/`` separates reference tokens and ``~`` begins
an escape sequence, those two characters are escaped *inside* a token:

- ``~1`` denotes a literal ``/``
- ``~0`` denotes a literal ``~``

Unescaping MUST replace ``~1`` before ``~0``; otherwise an encoded ``~01`` would
be corrupted. This module is the single home for that logic, which was
previously duplicated (with the same ``~1``/``~0`` magic) across ``traits.py``
and ``x_gts_ref.py``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote

# Sentinel distinguishing "pointer resolved to a real ``None``" from
# "pointer could not be resolved". Callers that care should pass this (or their
# own default) and compare identity against the returned value.
MISSING: Any = object()


def unescape_token(token: str) -> str:
    """Decode a single RFC 6901 reference token (``~1`` -> ``/``, ``~0`` -> ``~``)."""
    return token.replace("~1", "/").replace("~0", "~")


def resolve(document: Any, pointer: str, default: Any = None) -> Any:
    """Resolve an RFC 6901 JSON Pointer against ``document``.

    ``pointer`` accepts three equivalent spellings:

    - the empty string ``""`` - the whole document;
    - a pointer beginning with ``/`` - ``/a/b``;
    - a same-document URI fragment - ``#`` or ``#/a/b``.

    Returns ``default`` if any reference token cannot be resolved (missing key,
    non-integer/out-of-range array index, or descending into a scalar).
    """
    pointer = unquote(pointer.removeprefix("#"))
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        return default

    current = document
    for raw_token in pointer.split("/")[1:]:
        token = unescape_token(raw_token)
        if isinstance(current, dict):
            if token not in current:
                return default
            current = current[token]
        elif isinstance(current, list):
            if not (
                token.isascii()
                and token.isdecimal()
                and (token == "0" or not token.startswith("0"))
            ):
                return default
            try:
                current = current[int(token)]
            except IndexError:
                return default
        else:
            return default
    return current
