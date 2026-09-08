"""Regression tests for schema validation and compatibility behavior."""

import pytest
from jsonschema import ValidationError

from gts.compatibility import INCOMPATIBLE, check_backward_compatibility
from gts.entities import DEFAULT_GTS_CONFIG, GtsEntity
from gts.ops import GtsOps
from gts._server import ValidateEntityRequest
from gts.store import GtsStore
from gts.traits import build_effective_traits
from gts.x_gts_ref import XGtsRefValidator


def _schema_entity(gts_id, content):
    return GtsEntity(
        content={
            "$id": f"gts://{gts_id}",
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **content,
        },
        cfg=DEFAULT_GTS_CONFIG,
    )


class TestXGtsRefCombinators:
    def test_plain_one_of_is_left_to_jsonschema(self):
        errors = XGtsRefValidator().validate_instance(
            "value", {"oneOf": [{"type": "string"}, {"type": "integer"}]}
        )

        assert errors == []

    def test_x_gts_ref_only_one_of_still_requires_one_match(self):
        schema = {
            "oneOf": [
                {"x-gts-ref": "gts.x.test._.first.v1~"},
                {"x-gts-ref": "gts.x.test._.second.v1~"},
            ]
        }

        valid = "gts.x.test._.first.v1~x.test._.item.v1"
        invalid = "gts.x.test._.other.v1~x.test._.item.v1"

        assert XGtsRefValidator().validate_instance(valid, schema) == []
        assert [
            error.reason
            for error in XGtsRefValidator().validate_instance(invalid, schema)
        ] == ["oneOf: no branch matched"]


class TestReferenceResolution:
    def test_boolean_schema_does_not_require_reference_resolution(self):
        assert GtsStore(reader=None)._resolve_schema_refs(True) is True

    def test_modern_ref_siblings_are_preserved_during_instance_validation(self):
        store = GtsStore(reader=None)
        target_id = "gts.x.test._.target.v1~"
        source_id = "gts.x.test._.source.v1~"
        instance_id = "gts.x.test._.source.v1~x.test._.item.v1"

        store.register(_schema_entity(target_id, {"type": "string"}))
        store.register(
            _schema_entity(
                source_id,
                {
                    "type": "object",
                    "properties": {
                        "value": {
                            "$ref": f"gts://{target_id}",
                            "minLength": 3,
                        }
                    },
                },
            )
        )
        store.register(
            GtsEntity(
                content={"id": instance_id, "value": "x"},
                cfg=DEFAULT_GTS_CONFIG,
            )
        )

        with pytest.raises(ValidationError):
            store.validate_instance(instance_id)


class TestCompatibility:
    def test_type_constraint_is_not_dropped_when_enum_contains_other_types(self):
        result = check_backward_compatibility(
            {"enum": ["x", 1]},
            {"enum": ["x", 1], "type": "string"},
        )

        assert result == INCOMPATIBLE


class TestTraits:
    def test_null_default_is_materialized_and_overrides_ancestor_default(self):
        effective = build_effective_traits(
            [
                {"properties": {"value": {"default": "ancestor"}}},
                {"properties": {"value": {"default": None}}},
            ],
            {},
            None,
        )

        assert effective.values == {"value": None}


class TestRegistrationAndRequestValidation:
    def test_failed_schema_registration_rolls_back_the_candidate(self):
        ops = GtsOps()
        base_id = "gts.x.test._.base.v1~"
        derived_id = "gts.x.test._.base.v1~x.test._.child.v1~"

        assert ops.add_entity(
            {
                "$id": f"gts://{base_id}",
                "$schema": "http://json-schema.org/draft-07/schema#",
                "x-gts-final": True,
            },
            validate=True,
        ).ok
        assert not ops.add_entity(
            {
                "$id": f"gts://{derived_id}",
                "$schema": "http://json-schema.org/draft-07/schema#",
            },
            validate=True,
        ).ok

        assert ops.store.get(derived_id) is None

    def test_validate_entity_request_requires_an_identifier(self):
        with pytest.raises(ValueError, match="entity_id"):
            ValidateEntityRequest()
