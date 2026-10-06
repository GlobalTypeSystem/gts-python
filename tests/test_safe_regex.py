"""Safe regular-expression profile (spec sec 11.0.1, ADR-0006)."""

from __future__ import annotations

import time

import pytest
from gts.compatibility import check_backward_compatibility
from gts.entities import GtsEntity
from gts.gts import GtsID
from gts.ops import GtsOps
from gts.schema_validation import (
    FORMAT_CHECKER,
    RegexEvaluationError,
    UnsupportedRegexError,
    check_schema,
    check_schema_regexes,
    validator_for,
)
from gts.store import GtsStore
from jsonschema import SchemaError

from gts import safe_regex

DRAFT7 = "http://json-schema.org/draft-07/schema#"
DRAFT2019 = "https://json-schema.org/draft/2019-09/schema"
DRAFT2020 = "https://json-schema.org/draft/2020-12/schema"
DIALECTS = [DRAFT7, DRAFT2019, DRAFT2020]
MODERN = [DRAFT2019, DRAFT2020]
TYPE_ID = "gts.x.re2._.root.v1~"


def _schema(body, dialect=DRAFT2020, name="root"):
    return {"$schema": dialect, "$id": f"gts://gts.x.re2._.{name}.v1~", **body}


def _validator(schema):
    return validator_for(schema)(schema, format_checker=FORMAT_CHECKER)


# --- profile membership -------------------------------------------------------

# The expressions of the gts-spec conformance suite (tests/test_regex_validation.py)
# plus edge cases of the spec's spelling rules.
PROFILE = [
    "",
    "abc",
    "é😀",
    "^[A-Za-z0-9]+$",
    "[^abc]",
    "(foo|bar)+",
    "(?:ab)+",
    "[a-z]{1,3}",
    "a{3}",
    "a{2,}",
    "(a(b)?c)*",
    "a.*?b",
    "a*?b+?c??d{1,2}?e{2,}?f{2}?",
    r"\(x\)\.\*",
    r"\\C",
    r"\n\r\t\f\v",
    r"\x41",
    r"\d\D\w\W\s\S",
    "^a.c$",
    "(a*)*b",
    "(a+)+$",
    "^(a|aa)+$",
    r"^(?:(a|aa)+$|a+!$)",
    "^P[^\\n\\r\u2028\u2029]+",
    # Meta-schema patterns of the supported dialects.
    "^[^#]*#?$",
    "^[A-Za-z_][-A-Za-z0-9._]*$",
    "^[A-Za-z][-A-Za-z0-9.:_]*$",
    "a|",
    "()",
    "(?:^)*",
    r"[\s\S]",
    r"[^\s\S]",
    r"[\Sa]",
    r"[\x41-\x5A\-]",
    "[-a]",
    "[a-]",
    "[^-a]",
    "[a-z-]",
    "[a^]",
    "a/b",
    r"\/",
    r"\{\}\[\]\|\^\$",
    "\n",
    "a{0}",
    # At the common support bounds.
    "a{1000}",
    "a{1000,}",
    "a{0,1000}",
    "a{1000}?",
    "(?:a{500}){2}",
    "(?:a{10}){100}",
    "(?:a{1000}){0}",
    "(?:a{1000}){0,1}",
    "(?:(?:a{10})*){100}",
    "a{1000}" * 4 + "a" * 72,
    "a{999,}" * 4 + "a" * 72,
    "(?:ab){681}",
    "x" * safe_regex.MAX_EXPANDED_LENGTH,
    "(" * safe_regex.MAX_GROUP_DEPTH + "a" + ")" * safe_regex.MAX_GROUP_DEPTH,
    "(" * safe_regex.MAX_GROUP_DEPTH + ")" * safe_regex.MAX_GROUP_DEPTH,
]

OUTSIDE_PROFILE = [
    # Malformed.
    "[",
    "[unclosed",
    "(unclosed",
    "a)",
    "a{3,2}",
    "\\",
    "*abc",
    # Accepted by only one of ECMA-262 `u` and RE2.
    "a(?=b)",
    "a(?!b)",
    "(?<=a)b",
    "(?<!a)b",
    r"^(a+)\1$",
    r"^(?<w>a)\k<w>$",
    "\\" + "u0041",
    r"\Qx.y\E",
    "(?U)a+",
    "(?i)abc",
    r"abc\z",
    r"\x{41}",
    "[[:alpha:]]",
    r"\C",
    "(?>a+)b",
    "a++b",
    "a**",
    # Accepted by both, excluded by the draft profile.
    "(?i:a)b",
    "(?<w>a)",
    "(?P<w>a)",
    r"^\p{L}+$",
    r"\ba\b",
    r"\B",
    r"\0",
    r"\cA",
    r"\A",
    "[\\b]",
    # Ambiguous across engines (spec spelling rules) or outside the profile.
    "^[a&&b]$",
    "[a--b]",
    "[a~~b]",
    "[!-&&]",
    "[!--]",
    "[!-~~]",
    "[&&]",
    "[a[b]",
    "[]",
    "[^]",
    "[a-b-c]",
    r"\-",
    r"[\d-a]",
    r"[a-\s]",
    "[z-a]",
    "^*",
    "$?",
    "a{",
    "a{,3}",
    "a}",
    "]",
    "{1}",
    "a{01}",
    "a{1,02}",
    "a{2}{3}",
    r"\x4",
    r"\xZZ",
    "\ud800",
    5,
    # Support bounds.
    "a{1001}",
    "a{1001,}",
    "a{0,1001}",
    "(?:a{1000}){2}",
    "(?:a{1000,}){2}",
    "(?:a{2}){0,501}",
    "(?:(?:a{10}){10}){11}",
    "a{1000}" * 4 + "a" * 73,
    "a{999,}" * 4 + "a" * 73,
    "a{1000}?" * 4 + "a" * 69,
    "(?:ab){1000}",
    "(?:ab){682}",
    "x" * (safe_regex.MAX_EXPANDED_LENGTH + 1),
    "(" * (safe_regex.MAX_GROUP_DEPTH + 1)
    + "a"
    + ")" * (safe_regex.MAX_GROUP_DEPTH + 1),
]


@pytest.mark.parametrize("pattern", PROFILE)
def test_profile_expressions_are_supported(pattern):
    assert safe_regex.is_supported(pattern)
    safe_regex.search(pattern, "probe")


@pytest.mark.parametrize("pattern", OUTSIDE_PROFILE)
def test_expressions_outside_the_profile_are_rejected(pattern):
    assert not safe_regex.is_supported(pattern)
    with pytest.raises(UnsupportedRegexError, match="Unsupported pattern"):
        safe_regex.check_supported(pattern)
    with pytest.raises(UnsupportedRegexError):
        safe_regex.search(pattern, "probe")


def test_membership_does_not_compile(monkeypatch):
    def failing_compile(translation):
        raise AssertionError("membership must not compile")

    monkeypatch.setattr(safe_regex, "_compile", failing_compile)
    assert safe_regex.is_supported("^(a|b)+$")
    assert not safe_regex.is_supported("a(?=b)")


# --- matching ----------------------------------------------------------------


def test_declares_no_deviation():
    assert safe_regex.DECLARED_BEHAVIOR == {
        "digit": "reference",
        "word": "reference",
        "space": "reference",
    }


@pytest.mark.parametrize(
    "pattern, matches, non_matches",
    [
        ("abc", ["abc", "xabcx"], ["ab", "ABC"]),
        ("^abc$", ["abc"], ["abc\n", "x\nabc", "xabc"]),
        ("", ["", "abc"], []),
        # `.` excludes only LF.
        (
            "^.$",
            ["a", " ", "\t", "\v", "\r", "\u2028", "\u2029", "\u0085", "😀", "é"],
            ["", "\n", "ab"],
        ),
        ("^.{2}$", ["ab", "😀a"], ["😀"]),
        # `\s` is [\t\n\f\r ].
        (
            r"^\s$",
            [" ", "\t", "\n", "\f", "\r"],
            ["", "a", "\v", "\u00a0", "\u2028", "\ufeff"],
        ),
        (r"^\S$", ["a", "-", "\v", "\u00a0", "\ufeff"], [" ", "\t"]),
        (r"^[\s\S]$", ["a", "\n", "😀"], ["", "ab"]),
        (r"^[^\s\S]$", [], ["a", "\n", ""]),
        (r"^[^\S]$", [" ", "\r"], ["a", "\u3000"]),
        (r"^[\Sa]$", ["a", "b"], [" "]),
        # `\d` and `\w` are ASCII.
        (r"^\d+$", ["0123456789"], ["١", "a"]),
        (r"^\D$", ["a", "١"], ["0"]),
        (r"^\w+$", ["azAZ09_"], ["é", "-"]),
        (r"^\W$", ["é", "-"], ["a", "_"]),
        ("^[^a]$", ["😀"], ["a", "😀😀"]),
        ("^[😀é]$", ["😀", "é"], ["😀é"]),
        (r"^[\x41-\x43]+$", ["ABC"], ["D"]),
        (r"^\t\n\r\f\v$", ["\t\n\r\f\v"], ["\t\n\r\f"]),
        (r"^\(a\.b\)\*$", ["(a.b)*"], ["(axb)*"]),
        ("^a{2,3}$", ["aa", "aaa"], ["a", "aaaa"]),
        ("^a+?b??c{1,2}?$", ["ac", "abcc", "aabc"], ["a", "accc"]),
        ("^(?:^)*a$", ["a"], ["ba"]),
        ("^\u2028$", ["\u2028"], ["\n"]),
    ],
)
def test_matching_semantics(pattern, matches, non_matches):
    for text in matches:
        assert safe_regex.search(pattern, text), text
    for text in non_matches:
        assert not safe_regex.search(pattern, text), text


def test_matching_is_linear_for_classic_redos_patterns():
    started = time.perf_counter()
    for pattern in (r"^(a+)+$", r"^(a|aa)+$", r"^a*a*a*b$", "(a*)*b"):
        assert not safe_regex.search(pattern, "a" * 200_000 + "!")
    assert time.perf_counter() - started < 2


def test_input_that_is_not_unicode_fails_evaluation():
    with pytest.raises(RegexEvaluationError):
        safe_regex.search("a", "\ud800")


def test_compilation_failure_is_an_evaluation_error(monkeypatch):
    def failing_compile(translation):
        raise safe_regex.re2.error("pattern too large - compile failed")

    safe_regex._compile.cache_clear()
    monkeypatch.setattr(safe_regex, "_new_regexp", failing_compile)
    try:
        assert safe_regex.is_supported("^unique-compile-probe$")
        with pytest.raises(RegexEvaluationError, match="compilation failed"):
            safe_regex.search("^unique-compile-probe$", "x")
    finally:
        safe_regex._compile.cache_clear()


def test_oversized_expression_is_rejected_before_caching():
    pattern = "x" * 1_000_000
    before = safe_regex._unsupported_reason.cache_info().currsize
    with pytest.raises(UnsupportedRegexError, match="longer than") as error:
        safe_regex.check_supported(pattern)
    assert safe_regex._unsupported_reason.cache_info().currsize == before
    assert len(str(error.value)) < 400


# --- validator family -------------------------------------------------------


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize(
    "pattern, good, bad",
    [("^abc$", "abc", "abc\n"), (r"^\d+$", "123", "١"), (r"^\w+$", "a_1", "é")],
)
def test_subschema_restating_dialect_keeps_safe_engine(dialect, pattern, good, bad):
    schema = _schema(
        {"properties": {"value": {"$schema": dialect, "pattern": pattern}}}, dialect
    )
    validator = _validator(schema)
    assert validator.is_valid({"value": good})
    assert not validator.is_valid({"value": bad})


@pytest.mark.parametrize("dialect", DIALECTS)
def test_registered_reference_target_keeps_safe_engine(dialect):
    ops = GtsOps()
    target = _schema({"pattern": r"^\w+$"}, dialect, "target")
    source = _schema({"properties": {"value": {"$ref": target["$id"]}}}, dialect)
    assert ops.add_entity(target, validate=True).ok
    assert ops.add_entity(source, validate=True).ok
    assert ops.validate_json({"value": "abc"}, TYPE_ID).ok
    assert not ops.validate_json({"value": "é"}, TYPE_ID).ok


@pytest.mark.parametrize("dialect", DIALECTS)
def test_empty_pattern_classifies_every_property(dialect):
    schema = _schema(
        {"additionalProperties": False, "patternProperties": {"": {"type": "integer"}}},
        dialect,
    )
    validator = _validator(schema)
    assert validator.is_valid({"any": 1})
    assert not validator.is_valid({"any": "bad"})


@pytest.mark.parametrize("dialect", MODERN)
@pytest.mark.parametrize("keyword", ["allOf", "anyOf", "oneOf"])
def test_unevaluated_properties_follow_composition(dialect, keyword):
    schema = _schema(
        {
            keyword: [{"patternProperties": {r"^\w+$": {"type": "integer"}}}],
            "unevaluatedProperties": False,
        },
        dialect,
    )
    validator = _validator(schema)
    assert validator.is_valid({"abc": 1})
    assert not validator.is_valid({"é": 1})


@pytest.mark.parametrize("dialect", MODERN)
def test_unevaluated_properties_follow_local_ref(dialect):
    schema = _schema(
        {
            "$defs": {"p": {"patternProperties": {r"^\w+$": True}}},
            "$ref": "#/$defs/p",
            "unevaluatedProperties": False,
        },
        dialect,
    )
    validator = _validator(schema)
    assert validator.is_valid({"abc": 1})
    assert not validator.is_valid({"é": 1})


@pytest.mark.parametrize("dialect", MODERN)
def test_schema_valued_additional_properties_are_evaluated(dialect):
    schema = _schema(
        {"additionalProperties": {"type": "integer"}, "unevaluatedProperties": False},
        dialect,
    )
    validator = _validator(schema)
    assert validator.is_valid({"x": 1})
    assert not validator.is_valid({"x": "bad"})


def test_meta_schema_check_uses_profile_format():
    with pytest.raises(SchemaError):
        check_schema({"$schema": DRAFT7, "pattern": "a(?=b)"})
    with pytest.raises(SchemaError):
        check_schema({"$schema": DRAFT2020, "patternProperties": {"(?<=a)": True}})
    with pytest.raises(SchemaError):
        check_schema({"$schema": DRAFT2020, "pattern": r"\p{L}"})
    check_schema({"$schema": DRAFT2020, "pattern": r"^[A-Z]\w*$"})


def test_unsupported_regex_string_is_an_invertible_format_violation():
    schema = _schema({"not": {"format": "regex"}})
    validator = _validator(schema)
    assert validator.is_valid("a(?=b)")
    assert not validator.is_valid("a+")


@pytest.mark.parametrize(
    "body, instance",
    [
        ({"not": {"pattern": "a"}}, "zz"),
        (
            {"not": {"patternProperties": {"a": True}, "additionalProperties": False}},
            {"a": 1},
        ),
        ({"additionalProperties": False, "patternProperties": {"a": True}}, {"a": 1}),
        ({"unevaluatedProperties": False, "patternProperties": {"a": True}}, {"a": 1}),
    ],
)
def test_evaluation_failure_is_never_inverted(monkeypatch, body, instance):
    def failing_search(pattern, text):
        raise RegexEvaluationError("simulated engine failure")

    monkeypatch.setattr(safe_regex, "search", failing_search)
    validator = _validator(_schema(body))
    with pytest.raises(RegexEvaluationError):
        validator.is_valid(instance)


def test_evaluation_failure_fails_whole_instance_validation(monkeypatch):
    ops = GtsOps()
    schema = _schema({"properties": {"value": {"not": {"pattern": "a"}}}})
    assert ops.add_entity(schema, validate=True).ok

    def failing_search(pattern, text):
        raise RegexEvaluationError("simulated engine failure")

    monkeypatch.setattr(safe_regex, "search", failing_search)
    result = ops.validate_json({"value": "zz"}, TYPE_ID)
    assert not result.ok
    assert "simulated engine failure" in result.error


# --- schema-position preflight ----------------------------------------------


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize(
    "body",
    [
        {"not": {"pattern": "a(?=b)"}},
        {"if": False, "then": {"pattern": "a(?=b)"}},
        {"anyOf": [True, {"pattern": "a(?=b)"}]},
        {"x-gts-traits-schema": {"properties": {"v": {"pattern": "a(?=b)"}}}},
        {"propertyNames": {"pattern": "a(?=b)"}},
        {"patternProperties": {"a(?=b)": True}},
        {
            "anyOf": [True, {"$ref": "#/custom/unused"}],
            "custom": {"unused": {"pattern": "a(?=b)"}},
        },
        {
            "anyOf": [True, {"$ref": "#/examples/0"}],
            "examples": [{"pattern": "a(?=b)"}],
        },
    ],
)
def test_preflight_checks_every_schema_position(dialect, body):
    with pytest.raises(UnsupportedRegexError):
        check_schema_regexes(_schema(body, dialect))


@pytest.mark.parametrize(
    "dialect, container",
    [(DRAFT7, "definitions"), (DRAFT2019, "$defs"), (DRAFT2020, "$defs")],
)
def test_preflight_checks_definitions(dialect, container):
    with pytest.raises(UnsupportedRegexError):
        check_schema_regexes(
            _schema({container: {"u": {"pattern": "a(?=b)"}}}, dialect)
        )


@pytest.mark.parametrize("dialect", MODERN)
@pytest.mark.parametrize("definitions_first", [True, False])
def test_registration_preserves_relative_id_scope_of_reference_targets(
    dialect, definitions_first
):
    container = "$defs"
    definitions = {
        container: {
            "inner": {
                "$id": "sub/",
                "$ref": "#/hidden",
                "hidden": {"pattern": "a(?=b)"},
            }
        }
    }
    reference = {"anyOf": [True, {"$ref": f"#/{container}/inner"}]}
    body = (
        {**definitions, **reference}
        if definitions_first
        else {**reference, **definitions}
    )
    result = GtsOps().add_entity(_schema(body, dialect), validate=True)
    assert not result.ok
    assert "Unsupported pattern" in result.error


@pytest.mark.parametrize("dialect", MODERN)
@pytest.mark.parametrize("dependencies_first", [True, False])
def test_registration_uses_reference_scope_for_legacy_dependencies(
    dialect, dependencies_first
):
    dependencies = {"dependencies": {"x": {"$id": "sub/", "$ref": "#/hidden"}}}
    reference = {"anyOf": [True, {"$ref": "#/dependencies/x"}]}
    body = (
        {**dependencies, **reference}
        if dependencies_first
        else {**reference, **dependencies}
    )
    result = GtsOps().add_entity(
        _schema({**body, "hidden": {"pattern": "a(?=b)"}}, dialect), validate=True
    )
    assert not result.ok
    assert "Unsupported pattern" in result.error


@pytest.mark.parametrize("dialect", MODERN)
@pytest.mark.parametrize("dependencies_first", [True, False])
def test_registration_checks_both_scopes_of_nested_legacy_dependencies(
    dialect, dependencies_first
):
    dependencies = {
        "dependencies": {
            "x": {"properties": {"y": {"$id": "sub/", "$ref": "#/hidden"}}}
        }
    }
    reference = {
        "anyOf": [
            True,
            {"$ref": "#/dependencies/x"},
            {"$ref": "#/dependencies/x/properties/y"},
        ]
    }
    body = (
        {**dependencies, **reference}
        if dependencies_first
        else {**reference, **dependencies}
    )
    result = GtsOps().add_entity(
        _schema({**body, "hidden": {"pattern": "a(?=b)"}}, dialect), validate=True
    )
    assert not result.ok
    assert "Unsupported pattern" in result.error


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("operation", ["register", "validate_json", "cast"])
def test_unrelated_deferred_dialect_does_not_block_instance_operations(
    dialect, operation
):
    ops = GtsOps()
    deferred = _schema({}, "http://json-schema.org/draft-04/schema#", name="deferred")
    assert ops.add_entity(deferred, validate=False).ok
    assert ops.add_entity(_schema({"type": "object"}, dialect), validate=True).ok
    instance = {"id": f"{TYPE_ID}x.re2._.instance.v1"}
    if operation == "register":
        result = ops.add_entity(instance, validate=True)
        assert result.ok, result.error
    elif operation == "validate_json":
        result = ops.validate_json(instance)
        assert result.ok, result.error
    else:
        target = _schema({"type": "object"}, dialect, name="target")
        assert ops.add_entity(target, validate=True).ok
        assert ops.add_entity(instance, validate=False).ok
        result = ops.cast(instance["id"], "gts.x.re2._.target.v1~")
        assert not result.error
        assert result.casted_entity is not None


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("keyword", ["examples", "default", "const"])
def test_reference_in_literal_data_does_not_validate_deferred_dialect(dialect, keyword):
    ops = GtsOps()
    deferred = _schema({}, "http://json-schema.org/draft-04/schema#", name="deferred")
    assert ops.add_entity(deferred, validate=False).ok
    literal = {"$ref": deferred["$id"]}
    body = {keyword: [literal] if keyword == "examples" else literal}
    result = ops.add_entity(_schema(body, dialect), validate=True)
    assert result.ok, result.error


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("through_literal", [True, False])
@pytest.mark.parametrize(
    "operation", ["register", "schema_json", "instance_json", "cast"]
)
def test_reached_deferred_dialect_is_rejected(dialect, through_literal, operation):
    ops = GtsOps()
    deferred = _schema({}, "http://json-schema.org/draft-04/schema#", name="deferred")
    assert ops.add_entity(deferred, validate=False).ok
    reference = {"$ref": deferred["$id"]}
    body = (
        {"anyOf": [True, {"$ref": "#/examples/0"}], "examples": [reference]}
        if through_literal
        else {"anyOf": [True, reference]}
    )
    schema = _schema(body, dialect)
    if operation == "register":
        result = ops.add_entity(schema, validate=True)
    elif operation == "schema_json":
        result = ops.validate_json(schema)
    elif operation == "instance_json":
        assert ops.add_entity(schema, validate=False).ok
        result = ops.validate_json({}, TYPE_ID)
    else:
        assert ops.add_entity(schema, validate=False).ok
        source = _schema({"type": "object"}, dialect, name="source")
        assert ops.add_entity(source, validate=True).ok
        instance = {"id": "gts.x.re2._.source.v1~x.re2._.instance.v1"}
        assert ops.add_entity(instance, validate=False).ok
        result = ops.cast(instance["id"], TYPE_ID)
    assert "Unsupported JSON Schema dialect" in result.error
    assert "draft-04" in result.error


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("with_dependency", [True, False])
def test_unrelated_deferred_dialect_does_not_block_schema_validation(
    dialect, with_dependency
):
    ops = GtsOps()
    deferred = _schema({}, "http://json-schema.org/draft-04/schema#", name="deferred")
    assert ops.add_entity(deferred, validate=False).ok
    body = {"type": "string", "pattern": "^a+$"}
    if with_dependency:
        target = _schema(body, dialect, name="target")
        assert ops.add_entity(target, validate=False).ok
        body = {"$ref": target["$id"]}
    result = ops.add_entity(_schema(body, dialect), validate=True)
    assert result.ok, result.error
    assert ops.validate_schema(TYPE_ID).ok
    deferred_result = ops.validate_schema("gts.x.re2._.deferred.v1~")
    assert not deferred_result.ok
    assert "draft-04" in deferred_result.error


@pytest.mark.parametrize("dialect", DIALECTS)
def test_registration_preflight_follows_references_into_external_literal_data(dialect):
    store = GtsStore(reader=None)
    target = _schema(
        {"$ref": "#/default", "default": {"pattern": "a(?=b)"}},
        dialect,
        name="target",
    )
    store.register(
        GtsEntity(
            content=target,
            gts_id=GtsID("gts.x.re2._.target.v1~"),
            is_schema=True,
        )
    )
    source = _schema({"anyOf": [True, {"$ref": target["$id"]}]}, dialect)
    with pytest.raises(UnsupportedRegexError):
        store.validate_schema_content(TYPE_ID, source)


@pytest.mark.parametrize(
    "dialect, body",
    [
        (DRAFT2020, {"default": {"pattern": "a(?=b)"}}),
        (DRAFT2020, {"examples": [{"pattern": "a(?=b)"}]}),
        (DRAFT2020, {"const": {"pattern": "a(?=b)"}}),
        (DRAFT2020, {"enum": [{"patternProperties": {"(?=": True}}]}),
        (DRAFT2020, {"customAnnotation": {"pattern": "a(?=b)"}}),
        (DRAFT2020, {"properties": {"pattern": {"type": "string"}}}),
        (DRAFT7, {"prefixItems": [{"pattern": "a(?=b)"}]}),
        (DRAFT7, {"dependentSchemas": {"x": {"pattern": "a(?=b)"}}}),
        (DRAFT7, {"$defs": {"x": {"pattern": "a(?=b)"}}}),
        (DRAFT2020, {"additionalItems": {"pattern": "a(?=b)"}}),
    ],
)
def test_preflight_ignores_literal_data_and_undefined_keywords(dialect, body):
    check_schema_regexes(_schema(body, dialect))


@pytest.mark.parametrize("dialect", MODERN)
def test_preflight_follows_dynamic_references(dialect):
    keyword = "$recursiveRef" if dialect == DRAFT2019 else "$dynamicRef"
    anchor = (
        {"$recursiveAnchor": True}
        if dialect == DRAFT2019
        else {"$dynamicAnchor": "node"}
    )
    target = "#" if dialect == DRAFT2019 else "#node"
    body = {
        **anchor,
        "anyOf": [True, {keyword: target}],
        "pattern": "a",
        "properties": {"v": {"pattern": "ok"}},
        "unknown": {"pattern": "a(?=b)"},
    }
    check_schema_regexes(_schema(body, dialect))
    body["anyOf"].append({"$ref": "#/unknown"})
    with pytest.raises(UnsupportedRegexError):
        check_schema_regexes(_schema(body, dialect))


@pytest.mark.parametrize(
    "value_schema, value",
    [({"pattern": "a(?=b)"}, "ab"), ({"not": {"pattern": "a(?=b)"}}, "zz")],
)
def test_schema_registered_without_validation_cannot_bypass_profile(
    value_schema, value
):
    ops = GtsOps()
    schema = _schema({"properties": {"value": value_schema}})
    assert ops.add_entity(schema, validate=False).ok
    explicit = ops.validate_schema(TYPE_ID)
    assert not explicit.ok and "Unsupported pattern" in explicit.error
    result = ops.validate_json({"value": value}, TYPE_ID)
    assert not result.ok and "Unsupported pattern" in result.error


def test_schema_validation_reports_engine_failure_before_matching(monkeypatch):
    def failing_compile(pattern):
        raise safe_regex.re2.error("pattern too large - compile failed")

    safe_regex._compile.cache_clear()
    monkeypatch.setattr(safe_regex, "_new_regexp", failing_compile)
    try:
        # The pattern sits in an inactive branch, so no match would reach it.
        schema = _schema(
            {"anyOf": [True, {"properties": {"value": {"pattern": "^engine-probe$"}}}]}
        )
        ops = GtsOps()
        assert ops.add_entity(schema, validate=False).ok
        # The profile check alone does not compile.
        check_schema_regexes(schema)
        with pytest.raises(
            RegexEvaluationError, match="compilation failed: .* at 'anyOf"
        ):
            check_schema_regexes(schema, compile_patterns=True)
        result = ops.validate_schema(TYPE_ID)
        assert not result.ok and "compilation failed" in result.error
        assert "Unsupported pattern" not in result.error
    finally:
        safe_regex._compile.cache_clear()


def test_unsupported_pattern_in_referenced_document_blocks_instance_validation():
    store = GtsStore(reader=None)
    target = _schema({"$defs": {"bad": {"pattern": "a(?=b)"}}}, name="target")
    source = _schema(
        {"anyOf": [True, {"$ref": "gts://gts.x.re2._.target.v1~#/$defs/bad"}]}
    )
    for content in (target, source):
        gts_id = GtsID(content["$id"].removeprefix("gts://"))
        store.register(GtsEntity(content=content, gts_id=gts_id, is_schema=True))
    with pytest.raises(UnsupportedRegexError):
        store.validate_instance_content({"x": 1}, TYPE_ID)


# --- compatibility -----------------------------------------------------------


@pytest.mark.parametrize("value, pattern", [("١", r"^\d+$"), ("abc\n", "^abc$")])
def test_compatibility_never_uses_non_re2_semantics(value, pattern):
    old = {"type": "object", "properties": {"x": {"enum": [value]}}}
    new = {
        "type": "object",
        "properties": {"x": {"type": "string", "pattern": pattern}},
    }
    assert check_backward_compatibility(old, new) == "unknown"


def test_identical_patterns_are_factored_out():
    base = {
        "type": "object",
        "properties": {"s": {"type": "string", "pattern": "^[a-z]+$"}},
    }
    derived = {
        "type": "object",
        "properties": {
            "s": {"type": "string", "pattern": "^[a-z]+$"},
            "n": {"type": "integer"},
        },
    }
    assert check_backward_compatibility(derived, base) == "compatible"
    dropped = {"type": "object", "properties": {"s": {"type": "string"}}}
    assert check_backward_compatibility(dropped, base) == "unknown"


@pytest.mark.parametrize("keyword", ["not", "anyOf", "oneOf"])
def test_identical_regex_applicators_are_factored_out(keyword):
    def applicator(pattern):
        inner = {"properties": {"s": {"type": "string", "pattern": pattern}}}
        return {keyword: inner if keyword == "not" else [inner, {"required": ["n"]}]}

    base = {"type": "object", **applicator("^a$")}
    derived = {"allOf": [base, {"properties": {"n": {"type": "integer"}}}]}
    assert check_backward_compatibility(derived, base) == "compatible"
    changed = {"allOf": [{"type": "object", **applicator("^b$")}]}
    assert check_backward_compatibility(changed, base) == "unknown"


@pytest.mark.parametrize("keyword", ["not", "anyOf", "oneOf", "propertyNames"])
@pytest.mark.parametrize("old, new", [(True, 1), (False, 0)])
def test_factored_constraints_distinguish_booleans_from_numbers(keyword, old, new):
    def body(value):
        inner = {"pattern": "a", "const": value}
        return {keyword: [inner] if keyword in ("anyOf", "oneOf") else inner}

    assert check_backward_compatibility(body(old), body(new)) != "compatible"


def test_regex_any_of_is_not_factored_beside_unevaluated_properties():
    base = _schema(
        {
            "anyOf": [{"properties": {"s": {"pattern": "^a$"}}}],
            "unevaluatedProperties": False,
        }
    )
    assert check_backward_compatibility(base, base) == "unknown"


def test_pattern_factoring_respects_prefix_items():
    old = _schema(
        {
            "type": "array",
            "prefixItems": [True],
            "items": {"type": "string", "pattern": "^a$"},
        }
    )
    new = _schema({"type": "array", "items": {"type": "string", "pattern": "^a$"}})
    assert check_backward_compatibility(old, new) == "unknown"


def test_compatibility_sees_references_into_literal_data():
    old = _schema({"type": "object", "properties": {"x": {"enum": ["abc\n"]}}})
    new = _schema(
        {
            "type": "object",
            "properties": {"x": {"$ref": "#/default"}},
            "default": {"type": "string", "pattern": "^abc$"},
        }
    )
    assert check_backward_compatibility(old, new) == "unknown"


def test_compatibility_asserts_regex_format():
    regex_string = {"type": "string", "format": "regex"}
    assert (
        check_backward_compatibility({"enum": ["a(?=b)"]}, regex_string)
        == "incompatible"
    )
    assert check_backward_compatibility({"enum": ["a+"]}, regex_string) == "compatible"
    assert check_backward_compatibility({"type": "string"}, regex_string) == "unknown"
    assert check_backward_compatibility(regex_string, regex_string) == "compatible"


def test_derivation_ignores_regex_inside_inherited_trait_schema():
    ops = GtsOps()
    base_id = "gts.x.re2._.traits.v1~"
    derived_id = base_id + "x.re2._.child.v1~"
    base = {
        "$schema": DRAFT7,
        "$id": f"gts://{base_id}",
        "type": "object",
        "required": ["id"],
        "properties": {"id": {"type": "string"}},
        "x-gts-traits-schema": {
            "type": "object",
            "properties": {"rule": {"type": "string", "format": "regex"}},
        },
    }
    derived = {
        "$schema": DRAFT7,
        "$id": f"gts://{derived_id}",
        "allOf": [{"$ref": f"gts://{base_id}"}, {"type": "object"}],
        "x-gts-traits": {"rule": "a+"},
    }
    assert ops.add_entity(base, validate=True).ok
    assert ops.add_entity(derived, validate=True).ok
    assert ops.validate_schema(derived_id).ok
    derived["x-gts-traits"] = {"rule": "a(?=b)"}
    rejected = ops.add_entity(
        {**derived, "$id": derived["$id"].replace("child", "bad")}, validate=True
    )
    assert not rejected.ok


def test_operational_failure_in_wildcard_target_fails_validation(monkeypatch):
    ops = GtsOps()
    target_type = _schema(
        {
            "type": "object",
            "properties": {"id": {"type": "string"}, "v": {"pattern": "^a$"}},
        },
        name="target",
    )
    source = _schema(
        {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "x-gts-ref": "gts.x.re2._.target.v1~*"}
            },
        },
        # Draft-07's meta-schema has no patterns, so the failure first occurs
        # while validating the wildcard's candidate target.
        DRAFT7,
    )
    target = {"id": "gts.x.re2._.target.v1~x.re2._.one.v1", "v": "a"}
    assert ops.add_entity(target_type, validate=True).ok
    assert ops.add_entity(target, validate=False).ok
    assert ops.add_entity(source, validate=True).ok
    # Schema validation requires a valid registered target for the wildcard.
    assert ops.validate_schema(TYPE_ID).ok

    def failing_search(pattern, text):
        raise RegexEvaluationError("simulated engine failure")

    monkeypatch.setattr(safe_regex, "search", failing_search)
    result = ops.validate_schema(TYPE_ID)
    assert not result.ok
    assert "simulated engine failure" in result.error
