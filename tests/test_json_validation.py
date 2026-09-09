import json

from gts._cli import main
from gts.entities import DEFAULT_GTS_CONFIG
from gts._json_validation import GtsJsonValidator


def test_validate_json_reports_all_document_errors(tmp_path):
    (tmp_path / "broken.json").write_text('{ "id": "gts.broken', encoding="utf-8")
    (tmp_path / "invalid-schema.json").write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "gts://gts.example.catalog._.item.v1~",
                "type": 3,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "instance.json").write_text(
        json.dumps(
            {
                "id": "gts.example.catalog._.item.v1~example.catalog._.one.v1",
                "type": "gts.example.catalog._.item.v1~",
            }
        ),
        encoding="utf-8",
    )

    result = GtsJsonValidator(str(tmp_path), DEFAULT_GTS_CONFIG).validate()

    assert result.files == 3
    assert result.documents == 2
    assert result.schemas == 1
    assert result.instances == 1
    assert not result.ok
    assert {issue.stage for issue in result.issues} >= {
        "json",
        "instance",
    }


def test_validate_json_registers_type_only_instances(tmp_path):
    type_id = "gts.example.catalog._.item.v1~"
    (tmp_path / "schema.json").write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": f"gts://{type_id}",
                "type": "object",
                "required": ["type", "name"],
                "properties": {"type": {"const": type_id}, "name": {"type": "string"}},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "instance.json").write_text(
        json.dumps({"type": type_id, "name": "anonymous"}), encoding="utf-8"
    )

    result = GtsJsonValidator(str(tmp_path), DEFAULT_GTS_CONFIG).validate()

    assert result.ok
    assert result.schemas == 1
    assert result.instances == 1


def test_validate_json_cli_outputs_json_only(tmp_path, capsys):
    input_path = tmp_path / "broken.json"
    input_path.write_text('{ "id": "gts.broken', encoding="utf-8")

    main(["validate-all", "--path", str(input_path)])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["ok"] is False
    assert output["issues"][0]["file"] == str(input_path)
    assert captured.err == ""


def test_malformed_schema_id_is_reported(tmp_path):
    malformed = json.dumps({
        "$id": "gts://gtx.cli.core.test.bad.v1~",
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
    })
    (tmp_path / "bad.schema.json").write_text(malformed, encoding="utf-8")

    result = GtsJsonValidator(str(tmp_path), DEFAULT_GTS_CONFIG).validate()

    assert not result.ok, "malformed GTS id should fail"
    assert result.gts_entities == 0
    assert any(
        i.stage == "registry" and "malformed" in i.message for i in result.issues
    ), f"expected a malformed-id diagnostic, got: {result.issues}"


def test_incidental_prefix_mention_is_not_registered(tmp_path):
    doc = json.dumps({"description": "see gts.foo.bar for details", "value": 42})
    (tmp_path / "unrelated.json").write_text(doc, encoding="utf-8")

    result = GtsJsonValidator(str(tmp_path), DEFAULT_GTS_CONFIG).validate()

    assert result.ok, f"incidental mention should not fail: {result.issues}"
    assert result.documents == 1
    assert result.gts_entities == 0
    assert not result.issues


def test_duplicate_entity_is_reported(tmp_path):
    schema = json.dumps({
        "$id": "gts://gts.cli.core.test.base.v1~",
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    })
    (tmp_path / "a.schema.json").write_text(schema, encoding="utf-8")
    (tmp_path / "b.schema.json").write_text(schema, encoding="utf-8")

    result = GtsJsonValidator(str(tmp_path), DEFAULT_GTS_CONFIG).validate()

    assert not result.ok, "duplicate ids should fail"
    assert any(
        "Duplicate" in i.message for i in result.issues
    ), f"expected a duplicate diagnostic, got: {result.issues}"


def test_non_gts_files_are_ignored(tmp_path):
    schema = json.dumps({
        "$id": "gts://gts.cli.core.test.base.v1~",
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    })
    (tmp_path / "base.schema.json").write_text(schema, encoding="utf-8")
    (tmp_path / "package.json").write_text(
        '{"name": "pkg", "version": "1.0.0"}', encoding="utf-8"
    )
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "broken.json").write_text("{ this is not json ", encoding="utf-8")

    result = GtsJsonValidator(str(tmp_path), DEFAULT_GTS_CONFIG).validate()

    assert result.ok, f"non-GTS files must be ignored: {result.issues}"
    assert result.files == 1, "only the GTS schema should be processed"
    assert result.schemas == 1


def test_marker_heuristic_matches_expected_combinations():
    assert GtsJsonValidator._is_gts_marker('{"id": "gts.x.y.z.t.v1~a.b.c.d.v1.0"}')
    assert GtsJsonValidator._is_gts_marker('{"$id": "gts://gts.x.y.z.t.v1~"}')
    assert GtsJsonValidator._is_gts_marker(
        '{"properties": {"p": {"x-gts-ref": "..."}}}'
    )
    assert not GtsJsonValidator._is_gts_marker(
        '{"name": "widgets", "version": "1.0.0"}'
    )


def test_schema_errors_ordered_by_depth_then_gts_id(tmp_path):
    base_schema = json.dumps({
        "$id": "gts://gts.cli.core.test.base.v1~",
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    })
    (tmp_path / "base.schema.json").write_text(base_schema, encoding="utf-8")

    def invalid_base(seg):
        return json.dumps({
            "$id": f"gts://gts.cli.core.test.{seg}.v1~",
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "invalid_type",
        })

    invalid_leaf = json.dumps({
        "$id": "gts://gts.cli.core.test.base.v1~cli.core.test.leaf.v1~",
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "invalid_type",
    })

    (tmp_path / "0_mmm.schema.json").write_text(invalid_base("mmm"), encoding="utf-8")
    (tmp_path / "a_leaf.schema.json").write_text(invalid_leaf, encoding="utf-8")
    (tmp_path / "z_aaa.schema.json").write_text(invalid_base("aaa"), encoding="utf-8")

    result = GtsJsonValidator(str(tmp_path), DEFAULT_GTS_CONFIG).validate()

    schema_issues = [
        i for i in result.issues if i.stage in ("base-type", "derived-type")
    ]

    assert len(schema_issues) == 3, f"issues: {result.issues}"
    assert schema_issues[0].stage == "base-type"
    assert schema_issues[0].file.endswith("z_aaa.schema.json")
    assert schema_issues[1].stage == "base-type"
    assert schema_issues[1].file.endswith("0_mmm.schema.json")
    assert schema_issues[2].stage == "derived-type"
    assert schema_issues[2].file.endswith("a_leaf.schema.json")


def test_instance_errors_ordered_by_gts_id(tmp_path):
    base_schema = json.dumps({
        "$id": "gts://gts.cli.core.test.base.v1~",
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    })
    (tmp_path / "base.schema.json").write_text(base_schema, encoding="utf-8")

    def invalid_instance(seg):
        return json.dumps({
            "id": f"gts.cli.core.test.base.v1~cli.app._.{seg}.v1.0"
        })

    (tmp_path / "z_alpha.json").write_text(
        invalid_instance("alpha"), encoding="utf-8"
    )
    (tmp_path / "a_zeta.json").write_text(
        invalid_instance("zeta"), encoding="utf-8"
    )

    result = GtsJsonValidator(str(tmp_path), DEFAULT_GTS_CONFIG).validate()

    instance_issues = [i for i in result.issues if i.stage == "instance"]

    assert len(instance_issues) == 2, f"issues: {result.issues}"
    assert instance_issues[0].file.endswith("z_alpha.json"), (
        f"alpha should be first: {instance_issues}"
    )
    assert instance_issues[1].file.endswith("a_zeta.json"), (
        f"zeta should be second: {instance_issues}"
    )
