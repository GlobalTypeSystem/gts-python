import json

from gts._cli import main
from gts.entities import DEFAULT_GTS_CONFIG
from gts._json_validation import GtsJsonValidator


def test_validate_json_reports_all_document_errors(tmp_path):
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
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
        "json-schema",
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


def test_validate_json_cli_emits_issues_to_stderr(tmp_path, capsys):
    input_path = tmp_path / "broken.json"
    input_path.write_text("{", encoding="utf-8")

    main(["validate-json", "--path", str(input_path)])

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["ok"] is False
    assert output["issues"][0]["file"] == str(input_path)
    assert f"{input_path}: json:" in captured.err
