"""Additional public behavior coverage for file-backed GTS discovery."""

import json

from gts.files_reader import GtsFileReader


def test_reader_discovers_json_yaml_and_list_entities_and_skips_invalid_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "entities.json").write_text(
        json.dumps(
            [
                {"id": "gts.acme.catalog._.item.v1~acme.catalog._.one.v1"},
                {"id": "not-a-gts-id"},
            ]
        ),
        encoding="utf-8",
    )
    (source / "schema.yaml").write_text(
        """$id: gts://gts.acme.catalog._.item.v1~
$schema: https://json-schema.org/draft/2020-12/schema
type: object
""",
        encoding="utf-8",
    )
    (source / "broken.json").write_text("{not json", encoding="utf-8")
    (source / "notes.txt").write_text("ignored", encoding="utf-8")
    excluded = source / "node_modules"
    excluded.mkdir()
    (excluded / "ignored.json").write_text(
        json.dumps({"id": "gts.acme.catalog._.item.v1~acme.catalog._.ignored.v1"}),
        encoding="utf-8",
    )

    entities = list(GtsFileReader(str(source)))

    assert [entity.gts_id.id for entity in entities] == [
        "gts.acme.catalog._.item.v1~acme.catalog._.one.v1",
        "gts.acme.catalog._.item.v1~",
    ]
    assert entities[0].label == "entities.json#0"
    assert entities[0].file.sequencesCount == 2
    assert entities[1].file.name == "schema.yaml"


def test_reader_accepts_multiple_paths_and_reset_recollects_files(tmp_path):
    first = tmp_path / "first.gts"
    second = tmp_path / "second.jsonc"
    first.write_text(
        json.dumps({"id": "gts.acme.catalog._.item.v1~acme.catalog._.first.v1"}),
        encoding="utf-8",
    )
    second.write_text(
        json.dumps({"id": "gts.acme.catalog._.item.v1~acme.catalog._.second.v1"}),
        encoding="utf-8",
    )
    reader = GtsFileReader([str(first), str(second)])

    assert [entity.raw_id for entity in reader] == [
        "gts.acme.catalog._.item.v1~acme.catalog._.first.v1",
        "gts.acme.catalog._.item.v1~acme.catalog._.second.v1",
    ]
    assert reader.read_by_id("anything") is None

    reader.reset()
    assert [entity.file.name for entity in reader] == ["first.gts", "second.jsonc"]
