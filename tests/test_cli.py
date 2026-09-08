"""Tests for the command-line interface dispatch."""

import json

import pytest

from gts._cli import main


@pytest.mark.parametrize(
    "arguments",
    [
        ["validate-id", "--gts-id", "gts.vendor.package.namespace.type.v1~"],
        ["parse-id", "--gts-id", "gts.vendor.package.namespace.type.v1~"],
        [
            "match-id-pattern",
            "--candidate",
            "gts.vendor.package.namespace.type.v1~",
            "--pattern",
            "gts.vendor.package.*",
        ],
        ["uuid", "--gts-id", "gts.vendor.package.namespace.type.v1~"],
        [
            "validate-instance",
            "--gts-id",
            "gts.vendor.package.namespace.type.v1~vendor.package.namespace.item.v1",
        ],
        [
            "validate-type-schema",
            "--gts-id",
            "gts.vendor.package.namespace.type.v1~",
        ],
        [
            "validate-entity",
            "--gts-id",
            "gts.vendor.package.namespace.type.v1~",
        ],
        ["resolve-relationships", "--gts-id", "gts.vendor.package.namespace.type.v1~"],
        [
            "compatibility",
            "--old-schema-id",
            "gts.vendor.package.namespace.type.v1~",
            "--new-schema-id",
            "gts.vendor.package.namespace.type.v1.1~",
        ],
        [
            "cast",
            "--from-id",
            "gts.vendor.package.namespace.type.v1~vendor.package.namespace.item.v1",
            "--to-schema-id",
            "gts.vendor.package.namespace.type.v1.1~",
        ],
        ["query", "--expr", "gts.vendor.package.*"],
        ["attr", "--gts-with-path", "gts.vendor.package.namespace.type.v1~@name"],
        ["list"],
    ],
)
def test_cli_operations_emit_json(arguments, capsys):
    main(arguments)

    assert json.loads(capsys.readouterr().out)


def test_cli_writes_openapi_spec(tmp_path, capsys):
    output_path = tmp_path / "openapi.json"

    main(["openapi-spec", "--out", str(output_path)])

    assert json.loads(capsys.readouterr().out) == {"ok": True, "out": str(output_path)}
    assert json.loads(output_path.read_text())["openapi"]
