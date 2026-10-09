"""GTS regex profile (spec sec 11.0.1, ADR-0006).

A dedicated parser checks the shared ECMA-262 ``u``/RE2 syntax and support
bounds. Accepted patterns compile unchanged with ``google-re2`` for unanchored,
case-sensitive, linear-time search, with multiline and dot-all off.

RE2 reference semantics apply without deviations: ``.`` excludes only LF;
``\\s`` is ``[\\t\\n\\f\\r ]``; ``\\d`` and ``\\w`` are ASCII; anchors match
only at input boundaries. See ``DECLARED_BEHAVIOR`` and the spec for details.

Compiled programs are limited to ``MAX_MEM_BYTES``. Compilation/matching
failures and invalid Unicode raise ``RegexEvaluationError`` and fail the whole
validation. Linear search does not bound aggregate work; deployments should
limit input sizes and validation time.
"""

from __future__ import annotations

import functools
from importlib import metadata

import re2

ENGINE = "RE2 (google-re2)"
try:
    ENGINE_VERSION = metadata.version("google-re2")
except metadata.PackageNotFoundError:  # pragma: no cover - vendored/unusual installs
    ENGINE_VERSION = "unknown"

# RE2 reference semantics; leave GTS_TEST_REGEX_DECLARED_BEHAVIOR unset.
DECLARED_BEHAVIOR = {
    "digit": "reference",
    "word": "reference",
    "space": "reference",
}

# The common support bounds of ADR-0006.
MAX_EXPANDED_LENGTH = 4096
MAX_GROUP_DEPTH = 32
MAX_REPEAT = 1000

MAX_MEM_BYTES = 16 << 20
_SUPPORT_CACHE_SIZE = 1024
_COMPILED_CACHE_SIZE = 32
_SHOWN_PATTERN_LENGTH = 200

_SYNTAX_CHARACTERS = frozenset("^$\\.*+?()[]{}|")
_IDENTITY_ESCAPES = _SYNTAX_CHARACTERS | {"/"}
_CONTROL_ESCAPES = {"n": 0x0A, "r": 0x0D, "t": 0x09, "f": 0x0C, "v": 0x0B}
_CLASS_ESCAPES = frozenset("dDwWsS")
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


class UnsupportedRegexError(ValueError):
    """The expression is outside the GTS profile or its support bounds."""

    def __init__(self, pattern: object, reason: str, location: str | None = None):
        self.pattern = pattern
        self.reason = reason
        self.location = location
        where = f" at '{location}'" if location else ""
        shown = pattern
        if isinstance(pattern, str) and len(pattern) > _SHOWN_PATTERN_LENGTH:
            shown = pattern[:_SHOWN_PATTERN_LENGTH] + "..."
        super().__init__(f"Unsupported pattern{where} {shown!r}: {reason}")

    def at(self, location: str) -> UnsupportedRegexError:
        return UnsupportedRegexError(self.pattern, self.reason, location)


class RegexEvaluationError(RuntimeError):
    """Compilation or matching failed; abort the whole validation.

    Raised rather than yielded as a validation error, so applicators and property
    classification cannot turn the failure into a match result.
    """


class _Unsupported(Exception):
    pass


class _Parser:
    """Recognize profile syntax and support bounds.

    Productions return expanded length (counted operands expanded, other syntax
    counted by spelling) and the largest repetition product along a nesting path.
    """

    def __init__(self, pattern: str):
        self.pattern = pattern
        self.index = 0

    def parse(self) -> None:
        length, _ = self.disjunction(0)
        if self.index < len(self.pattern):
            # Only an unmatched ')' stops a top-level disjunction.
            raise _Unsupported("unmatched ')'")
        if length > MAX_EXPANDED_LENGTH:
            raise _Unsupported(
                f"expanded length {length} exceeds {MAX_EXPANDED_LENGTH}"
            )

    def peek(self, offset: int = 0) -> str | None:
        index = self.index + offset
        return self.pattern[index] if index < len(self.pattern) else None

    def disjunction(self, depth: int) -> tuple[int, int]:
        length, product = 0, 1
        while True:
            branch_length, branch_product = self.alternative(depth)
            length += branch_length
            product = max(product, branch_product)
            if self.peek() != "|":
                return length, product
            self.index += 1
            length += 1

    def alternative(self, depth: int) -> tuple[int, int]:
        length, product = 0, 1
        while (char := self.peek()) is not None and char not in "|)":
            term_length, term_product = self.term(depth)
            length += term_length
            product = max(product, term_product)
        return length, product

    def term(self, depth: int) -> tuple[int, int]:
        char = self.peek()
        if char in ("^", "$"):
            self.index += 1
            if (following := self.peek()) is not None and following in "*+?{":
                raise _Unsupported(f"quantifier after assertion {char!r}")
            return 1, 1
        length, product = self.atom(depth)
        start = self.index
        repetition = self.quantifier()
        if repetition is None:
            return length, product
        factor, copies = repetition
        product *= factor
        if product > MAX_REPEAT:
            raise _Unsupported(
                f"nested counted repetition {product} exceeds {MAX_REPEAT}"
            )
        return length * copies + self.index - start, product

    def atom(self, depth: int) -> tuple[int, int]:
        char = self.peek()
        assert char is not None
        if char == "(":
            return self.group(depth)
        start = self.index
        if char == "[":
            self.character_class()
        elif char == "\\":
            self.atom_escape()
        elif char in _SYNTAX_CHARACTERS and char != ".":
            raise _Unsupported(f"unexpected {char!r} at offset {self.index}")
        else:
            self.index += 1
        return self.index - start, 1

    def group(self, depth: int) -> tuple[int, int]:
        if depth >= MAX_GROUP_DEPTH:
            raise _Unsupported(f"groups nested deeper than {MAX_GROUP_DEPTH}")
        start = self.index
        self.index += 1
        if self.peek() == "?":
            if self.peek(1) != ":":
                raise _Unsupported(
                    "only (...) and (?:...) groups are supported "
                    "(no lookaround, named groups, inline flags or atomic groups)"
                )
            self.index += 2
        prefix = self.index - start
        length, product = self.disjunction(depth + 1)
        if self.peek() != ")":
            raise _Unsupported("unterminated group")
        self.index += 1
        return prefix + length + 1, product

    def quantifier(self) -> tuple[int, int] | None:
        """The (product factor, expanded copies) of a quantifier, or None."""
        char = self.peek()
        if char in ("*", "+", "?"):
            self.index += 1
            repetition = (1, 1)
        elif char == "{":
            repetition = self.counted_repetition()
        else:
            return None
        if self.peek() == "?":
            self.index += 1
        if (char := self.peek()) is not None and char in "*+?{":
            raise _Unsupported("stacked or possessive quantifier")
        return repetition

    def counted_repetition(self) -> tuple[int, int]:
        # {n,m} and {n} contribute their upper count to the product and expand
        # to that many copies; {n,} contributes n and expands to n + 1 copies.
        # A zero count contributes one and expands to one copy.
        self.index += 1
        low = self.count()
        high: int | None = low
        if self.peek() == ",":
            self.index += 1
            high = None if self.peek() == "}" else self.count()
        if self.peek() != "}":
            raise _Unsupported("malformed counted repetition")
        self.index += 1
        if high is None:
            return max(low, 1), low + 1
        if high < low:
            raise _Unsupported(f"counted repetition {{{low},{high}}} is reversed")
        return max(high, 1), max(high, 1)

    def count(self) -> int:
        start = self.index
        while (char := self.peek()) is not None and "0" <= char <= "9":
            self.index += 1
        digits = self.pattern[start : self.index]
        if not digits:
            raise _Unsupported("malformed counted repetition")
        if len(digits) > 1 and digits[0] == "0":
            raise _Unsupported("counts with leading zeros are not supported")
        if len(digits) > len(str(MAX_REPEAT)) or int(digits) > MAX_REPEAT:
            raise _Unsupported(f"repetition count {digits} exceeds {MAX_REPEAT}")
        return int(digits)

    def character_escape(self, escaped: str) -> int | None:
        """Code point of a single-character escape (after the backslash)."""
        if escaped in _CONTROL_ESCAPES:
            self.index += 1
            return _CONTROL_ESCAPES[escaped]
        if escaped == "x":
            digits = self.pattern[self.index + 1 : self.index + 3]
            if len(digits) != 2 or not set(digits) <= _HEX_DIGITS:
                raise _Unsupported("\\x must be followed by exactly two hex digits")
            self.index += 3
            return int(digits, 16)
        if escaped in _IDENTITY_ESCAPES:
            self.index += 1
            return ord(escaped)
        return None

    def atom_escape(self) -> None:
        self.index += 1
        escaped = self.peek()
        if escaped is None:
            raise _Unsupported("trailing backslash")
        if escaped in _CLASS_ESCAPES:
            self.index += 1
        elif self.character_escape(escaped) is None:
            raise _Unsupported(f"escape \\{escaped} is not supported")

    def reject_set_operator(self) -> None:
        # Unescaped `&&`, `--` and `~~` are literals in ECMA-262 `u` and RE2
        # but set operations in other engines (Rust `regex`, ECMA-262 `v`).
        if self.pattern.startswith(("&&", "--", "~~"), self.index):
            raise _Unsupported(
                f"{self.pattern[self.index] * 2!r} inside a class is ambiguous"
            )

    def class_atom(self) -> int | None:
        """One class atom: its code point, or None for a class escape."""
        char = self.peek()
        if char is None:
            raise _Unsupported("unterminated character class")
        if char == "[":
            raise _Unsupported("'[' inside a class must be escaped")
        if char != "\\":
            self.reject_set_operator()
            self.index += 1
            return ord(char)
        self.index += 1
        escaped = self.peek()
        if escaped is None:
            raise _Unsupported("unterminated character class")
        if escaped in _CLASS_ESCAPES:
            self.index += 1
            return None
        if escaped == "-":
            self.index += 1
            return ord("-")
        code_point = self.character_escape(escaped)
        if code_point is None:
            raise _Unsupported(f"escape \\{escaped} is not supported in a class")
        return code_point

    def character_class(self) -> None:
        self.index += 1
        if self.peek() == "^":
            self.index += 1
        if self.peek() == "]":
            raise _Unsupported("empty character classes are not supported")
        first = True
        while True:
            char = self.peek()
            if char == "]":
                self.index += 1
                return
            if char == "-" and not first and self.peek(1) != "]":
                raise _Unsupported("'-' inside a class must be first, last or escaped")
            low = self.class_atom()
            first = False
            if self.peek() == "-" and self.peek(1) not in ("]", None):
                self.reject_set_operator()
                self.index += 1
                high = self.class_atom()
                if low is None or high is None:
                    raise _Unsupported("a class range needs single-character endpoints")
                if high < low:
                    raise _Unsupported("class range is out of order")


def _surrogate_offset(text: str) -> int | None:
    for offset, char in enumerate(text):
        if 0xD800 <= ord(char) <= 0xDFFF:
            return offset
    return None


@functools.lru_cache(maxsize=_SUPPORT_CACHE_SIZE)
def _unsupported_reason(pattern: str) -> str | None:
    # Cache reasons without tracebacks. Callers bound key length before lookup.
    offset = _surrogate_offset(pattern)
    if offset is not None:
        return f"lone surrogate at offset {offset}"
    try:
        _Parser(pattern).parse()
    except _Unsupported as reason:
        return str(reason)
    return None


# --- public API ------------------------------------------------------------


def check_supported(pattern: object) -> None:
    """Check membership without compiling; raise ``UnsupportedRegexError`` if unsupported."""
    if not isinstance(pattern, str):
        raise UnsupportedRegexError(pattern, "expression must be a string")
    if len(pattern) > MAX_EXPANDED_LENGTH:
        raise UnsupportedRegexError(
            pattern, f"expression is longer than {MAX_EXPANDED_LENGTH} code points"
        )
    reason = _unsupported_reason(pattern)
    if reason is not None:
        raise UnsupportedRegexError(pattern, reason)


def is_supported(pattern: object) -> bool:
    try:
        check_supported(pattern)
    except UnsupportedRegexError:
        return False
    return True


def check_compiles(pattern: object) -> None:
    """Check membership, then compile ``pattern`` with the engine.

    Raises:
        UnsupportedRegexError: ``pattern`` is not in the profile.
        RegexEvaluationError: the engine could not compile it.
    """
    check_supported(pattern)
    assert isinstance(pattern, str)
    _compile(pattern)


def _options() -> re2.Options:
    options = re2.Options()
    options.max_mem = MAX_MEM_BYTES
    options.log_errors = False
    options.never_capture = True
    return options


_OPTIONS = _options()


def _new_regexp(pattern: str) -> re2._Regexp:
    # Bypass re2.compile's 128-entry cache to enforce _COMPILED_CACHE_SIZE.
    return re2._Regexp(pattern, _OPTIONS)


@functools.lru_cache(maxsize=_COMPILED_CACHE_SIZE)
def _compile(pattern: str) -> re2._Regexp:
    try:
        compiled = _new_regexp(pattern)
    except (re2.error, MemoryError) as error:
        raise RegexEvaluationError(
            f"regular expression compilation failed: {error}"
        ) from error
    return compiled


def search(pattern: object, text: str) -> bool:
    """Whether ``pattern`` matches anywhere in ``text`` (JSON Schema semantics).

    Raises:
        UnsupportedRegexError: ``pattern`` is not in the profile.
        RegexEvaluationError: the match could not be completed.
    """
    check_supported(pattern)
    assert isinstance(pattern, str)
    compiled = _compile(pattern)
    try:
        return compiled.search(text) is not None
    except UnicodeEncodeError as error:
        # Unpaired surrogates are not valid GTS input (spec sec 11.0.1); RE2
        # matches UTF-8, which cannot represent them.
        raise RegexEvaluationError(
            f"cannot match {pattern!r}: the input string is not valid Unicode"
        ) from error
    except (re2.error, MemoryError) as error:
        raise RegexEvaluationError(
            f"regular expression evaluation failed for {pattern!r}: {error}"
        ) from error
