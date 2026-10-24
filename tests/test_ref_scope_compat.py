"""Regression tests for gts.ref_scope_compat.schema_ref_relation (README §9.6.1).

These cover reference-scope folding cases the gts-spec conformance suite does
not exercise (it only places reference keywords on top-level properties).
"""

from typing import ClassVar

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


class TestTupleAdditionalItemsScope:
    """A tuple position the base leaves to ``additionalItems`` (or the 2020-12
    ``items`` tail) must be compared against that tail scope."""

    BASE: ClassVar[dict] = {
        "type": "array",
        "items": [{"type": "string", TYPE_REF: "gts.x.a.*"}],
        "additionalItems": {"type": "string", TYPE_REF: "gts.x.test.*"},
    }

    def test_unrestricted_new_tuple_entry_is_excluded(self):
        derived = {
            "type": "array",
            "items": [
                {"type": "string", TYPE_REF: "gts.x.a.*"},
                {"type": "string"},
            ],
            "additionalItems": {"type": "string", TYPE_REF: "gts.x.test.*"},
        }
        assert _rel(self.BASE, derived) == rsc.EXCLUDED

    def test_narrower_new_tuple_entry_is_included(self):
        derived = {
            "type": "array",
            "items": [
                {"type": "string", TYPE_REF: "gts.x.a.*"},
                {"type": "string", TYPE_REF: "gts.x.test.b.*"},
            ],
            "additionalItems": {"type": "string", TYPE_REF: "gts.x.test.*"},
        }
        assert _rel(self.BASE, derived) == rsc.INCLUDED

    def test_shorter_derived_tuple_uses_its_additional_items(self):
        derived = {
            "type": "array",
            "items": [],
            "additionalItems": {"type": "string", TYPE_REF: "gts.x.test.*"},
        }
        # Index 0 is now governed by derived additionalItems (gts.x.test.*),
        # which escapes the base's items[0] scope (gts.x.a.*).
        assert _rel(self.BASE, derived) == rsc.EXCLUDED

    def test_prefix_items_new_entry_checked_against_items_tail(self):
        base = {
            "type": "array",
            "prefixItems": [{"type": "string", TYPE_REF: "gts.x.a.*"}],
            "items": {"type": "string", TYPE_REF: "gts.x.test.*"},
        }
        derived = {
            "type": "array",
            "prefixItems": [
                {"type": "string", TYPE_REF: "gts.x.a.*"},
                {"type": "string"},
            ],
            "items": {"type": "string", TYPE_REF: "gts.x.test.*"},
        }
        assert _rel(base, derived) == rsc.EXCLUDED

    def test_single_items_base_against_tuple_derived(self):
        base = {"type": "array", "items": {"type": "string", TYPE_REF: "gts.x.*"}}
        narrowed = {
            "type": "array",
            "items": [{"type": "string", TYPE_REF: "gts.x.a.*"}],
            "additionalItems": {"type": "string", TYPE_REF: "gts.x.b.*"},
        }
        assert _rel(base, narrowed) == rsc.INCLUDED
        open_tail = {
            "type": "array",
            "items": [{"type": "string", TYPE_REF: "gts.x.a.*"}],
        }
        assert _rel(base, open_tail) == rsc.EXCLUDED


class TestInheritedUnionReference:
    """A reference-bearing anyOf/oneOf in an earlier allOf branch must survive a
    later redeclaration of the property."""

    def _base(self, applicator):
        return {
            "allOf": [
                {
                    "properties": {
                        "link": {
                            applicator: [{TYPE_REF: "gts.a.*"}, {TYPE_REF: "gts.b.*"}]
                        }
                    }
                },
                {"properties": {"link": {"type": "string"}}},
            ]
        }

    def test_overlay_does_not_widen_inherited_union(self):
        derived = {"properties": {"link": {"type": "string"}}}
        for applicator in ("anyOf", "oneOf"):
            assert _rel(self._base(applicator), derived) == rsc.EXCLUDED, applicator

    def test_narrower_derived_under_inherited_union_is_included(self):
        derived = {"properties": {"link": {TYPE_REF: "gts.b.x.*"}}}
        for applicator in ("anyOf", "oneOf"):
            assert _rel(self._base(applicator), derived) == rsc.INCLUDED, applicator

    def test_derived_redeclaring_union_keeps_inherited_conjunct(self):
        base = {"properties": {"link": {TYPE_REF: "gts.a.*"}}}
        derived = {
            "allOf": [
                {"properties": {"link": {"anyOf": [{TYPE_REF: "gts.a.x.*"}]}}},
                {"properties": {"link": {"anyOf": [{TYPE_REF: "gts.*"}]}}},
            ]
        }
        # Effective derived link is gts.a.x.* ∩ gts.*, which lies within gts.a.*.
        assert _rel(base, derived) == rsc.INCLUDED

    def test_inherited_not_reference_is_preserved(self):
        base = {
            "allOf": [
                {"properties": {"link": {"not": {TYPE_REF: "gts.a.*"}}}},
                {"properties": {"link": {"type": "string"}}},
            ]
        }
        derived = {"properties": {"link": {"type": "string"}}}
        assert _rel(base, derived) == rsc.EXCLUDED


class TestNoReferenceIsIncluded:
    def test_schemas_without_reference_keywords_are_included(self):
        base = {"type": "array", "items": {"type": "string"}}
        derived = {"type": "array", "items": {"type": "string", "minLength": 1}}
        assert _rel(base, derived) == rsc.INCLUDED
