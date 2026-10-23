"""Regression tests for gts.ref_scope_compat.schema_ref_relation (README §9.6.1).

These cover reference-scope folding cases the gts-spec conformance suite does
not exercise (it only places reference keywords on top-level properties).
"""

from gts import ref_scope_compat as rsc

TYPE_REF = "x-gts-type-ref"


def _rel(base, derived):
    return rsc.schema_ref_relation(base, derived)


class TestInheritedAllOfReference:
    """A reference nested in an inherited ``allOf`` must survive a later overlay
    that redeclares the property with ordinary keywords."""

    def test_overlay_does_not_widen_inherited_allof_reference(self):
        base = {
            "allOf": [
                {"properties": {"link": {"allOf": [{TYPE_REF: "gts.a.*"}]}}},
                {"properties": {"link": {"type": "string"}}},
            ]
        }
        derived = {"properties": {"link": {"type": "string"}}}
        # derived accepts any string; base restricts link to the gts.a.* family.
        assert _rel(base, derived) == rsc.EXCLUDED

    def test_narrower_derived_under_inherited_allof_is_included(self):
        base = {
            "allOf": [
                {"properties": {"link": {"allOf": [{TYPE_REF: "gts.a.*"}]}}},
                {"properties": {"link": {"type": "string"}}},
            ]
        }
        derived = {"properties": {"link": {TYPE_REF: "gts.a.b.*"}}}
        assert _rel(base, derived) == rsc.INCLUDED


class TestReferenceUnderItems:
    """Reference widening under array ``items`` must be detected."""

    def test_items_reference_widening_is_excluded(self):
        base = {
            "type": "array",
            "items": {"type": "string", TYPE_REF: "gts.x.test.*"},
        }
        derived = {"type": "array", "items": {"type": "string", TYPE_REF: "gts.*"}}
        assert _rel(base, derived) == rsc.EXCLUDED

    def test_items_reference_narrowing_is_included(self):
        base = {"type": "array", "items": {"type": "string", TYPE_REF: "gts.x.*"}}
        derived = {
            "type": "array",
            "items": {"type": "string", TYPE_REF: "gts.x.test.*"},
        }
        assert _rel(base, derived) == rsc.INCLUDED

    def test_items_reference_dropped_by_derived_is_excluded(self):
        base = {"type": "array", "items": {"type": "string", TYPE_REF: "gts.x.test.*"}}
        derived = {"type": "array", "items": {"type": "string"}}
        assert _rel(base, derived) == rsc.EXCLUDED

    def test_tuple_items_reference_widening_is_excluded(self):
        base = {
            "type": "array",
            "items": [{"type": "string", TYPE_REF: "gts.x.test.*"}],
        }
        derived = {"type": "array", "items": [{"type": "string", TYPE_REF: "gts.*"}]}
        assert _rel(base, derived) == rsc.EXCLUDED


class TestNoReferenceIsIncluded:
    def test_schemas_without_reference_keywords_are_included(self):
        base = {"type": "array", "items": {"type": "string"}}
        derived = {"type": "array", "items": {"type": "string", "minLength": 1}}
        assert _rel(base, derived) == rsc.INCLUDED
