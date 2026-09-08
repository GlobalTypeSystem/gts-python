"""Tests for GTS ID parsing and validation."""

import pytest
import uuid

from gts.gts import (
    GtsID,
    GtsIdSegment,
    GtsWildcard,
    GtsInvalidId,
    GtsInvalidSegment,
    GtsInvalidWildcard,
    GTS_PREFIX,
)


class TestGtsIdSegment:
    """Tests for GtsIdSegment parsing."""

    def test_valid_segment_basic(self):
        """Test parsing a basic valid segment."""
        seg = GtsIdSegment(1, 0, "vendor.package.namespace.type.v1")
        assert seg.vendor == "vendor"
        assert seg.package == "package"
        assert seg.namespace == "namespace"
        assert seg.type == "type"
        assert seg.ver_major == 1
        assert seg.ver_minor is None
        assert seg.is_type is False
        assert seg.is_wildcard is False

    def test_valid_segment_with_minor_version(self):
        """Test segment with minor version."""
        seg = GtsIdSegment(1, 0, "vendor.package.namespace.type.v2.3")
        assert seg.ver_major == 2
        assert seg.ver_minor == 3

    def test_valid_segment_type_marker(self):
        """Test segment with type marker (~)."""
        seg = GtsIdSegment(1, 0, "vendor.package.namespace.type.v1~")
        assert seg.is_type is True

    def test_valid_segment_wildcard(self):
        """Test segment with wildcard."""
        seg = GtsIdSegment(1, 0, "vendor.package.*")
        assert seg.is_wildcard is True
        assert seg.vendor == "vendor"
        assert seg.package == "package"

    def test_invalid_segment_too_many_tokens(self):
        """Test that too many tokens raises an error."""
        with pytest.raises(GtsInvalidSegment) as exc_info:
            GtsIdSegment(1, 0, "a.b.c.d.v1.0.extra")
        assert "Too many tokens" in str(exc_info.value)

    def test_invalid_segment_too_few_tokens(self):
        """Test that too few tokens raises an error."""
        with pytest.raises(GtsInvalidSegment) as exc_info:
            GtsIdSegment(1, 0, "vendor.package.namespace")
        assert "Too few tokens" in str(exc_info.value)

    def test_invalid_segment_bad_version_format(self):
        """Test invalid version format."""
        with pytest.raises(GtsInvalidSegment) as exc_info:
            GtsIdSegment(1, 0, "vendor.package.namespace.type.1")
        assert "Major version must start with 'v'" in str(exc_info.value)

    def test_invalid_segment_non_integer_version(self):
        """Test non-integer version number."""
        with pytest.raises(GtsInvalidSegment) as exc_info:
            GtsIdSegment(1, 0, "vendor.package.namespace.type.vx")
        assert "Major version must be an integer" in str(exc_info.value)

    def test_invalid_segment_multiple_tildes(self):
        """Test multiple tildes in segment."""
        with pytest.raises(GtsInvalidSegment) as exc_info:
            GtsIdSegment(1, 0, "vendor.package~namespace.type.v1~")
        assert "Too many '~' characters" in str(exc_info.value)


class TestGtsID:
    """Tests for GtsID class."""

    def test_valid_gts_id_basic(self):
        """Test parsing a valid GTS ID."""
        gts_id = GtsID("gts.vendor.package.namespace.type.v1~")
        assert gts_id.id == "gts.vendor.package.namespace.type.v1~"
        assert gts_id.is_type is True
        assert len(gts_id.gts_id_segments) == 1

    def test_valid_gts_id_with_uri_prefix(self):
        """Test GTS ID with URI prefix is normalized."""
        gts_id = GtsID("gts://gts.vendor.package.namespace.type.v1~")
        assert gts_id.id == "gts.vendor.package.namespace.type.v1~"

    def test_valid_gts_id_instance(self):
        """Test instance GTS ID (not ending with ~)."""
        gts_id = GtsID(
            "gts.vendor.package.namespace.type.v1~vendor.package.namespace.instance.v1"
        )
        assert gts_id.is_type is False
        assert len(gts_id.gts_id_segments) == 2

    def test_valid_gts_id_with_minor_version(self):
        """Test GTS ID with minor version."""
        gts_id = GtsID("gts.vendor.package.namespace.type.v1.2~")
        assert gts_id.gts_id_segments[0].ver_major == 1
        assert gts_id.gts_id_segments[0].ver_minor == 2

    def test_invalid_gts_id_uppercase(self):
        """Test that uppercase letters are rejected."""
        with pytest.raises(GtsInvalidId) as exc_info:
            GtsID("gts.Vendor.package.namespace.type.v1~")
        assert "Must be lower case" in str(exc_info.value)

    def test_invalid_gts_id_with_dash(self):
        """Test that dashes are rejected."""
        with pytest.raises(GtsInvalidId) as exc_info:
            GtsID("gts.vendor-pkg.package.namespace.type.v1~")
        assert "Must not contain '-'" in str(exc_info.value)

    def test_invalid_gts_id_missing_prefix(self):
        """Test that missing gts. prefix is rejected."""
        with pytest.raises(GtsInvalidId) as exc_info:
            GtsID("vendor.package.namespace.type.v1~")
        assert f"Does not start with '{GTS_PREFIX}'" in str(exc_info.value)

    def test_invalid_gts_id_too_long(self):
        """Test that overly long IDs are rejected."""
        long_segment = "a" * 1025
        with pytest.raises(GtsInvalidId) as exc_info:
            GtsID(f"gts.{long_segment}")
        assert "Too long" in str(exc_info.value)

    def test_get_type_id(self):
        """Test extracting type ID from instance ID."""
        gts_id = GtsID(
            "gts.vendor.package.namespace.type.v1~vendor.package.namespace.instance.v1"
        )
        type_id = gts_id.get_type_id()
        assert type_id == "gts.vendor.package.namespace.type.v1~"

    def test_to_uuid(self):
        """Test UUID generation is deterministic."""
        gts_id1 = GtsID("gts.vendor.package.namespace.type.v1~")
        gts_id2 = GtsID("gts.vendor.package.namespace.type.v1~")
        assert gts_id1.to_uuid() == gts_id2.to_uuid()
        assert isinstance(gts_id1.to_uuid(), uuid.UUID)

    def test_is_valid_static_method(self):
        """Test static is_valid method."""
        assert GtsID.is_valid("gts.vendor.package.namespace.type.v1~") is True
        assert GtsID.is_valid("gts://gts.vendor.package.namespace.type.v1~") is True
        assert GtsID.is_valid("invalid") is False
        assert GtsID.is_valid("") is False

    def test_split_at_path(self):
        """Test splitting GTS ID with path."""
        gts, path = GtsID.split_at_path(
            "gts.vendor.package.namespace.type.v1~@properties.name"
        )
        assert gts == "gts.vendor.package.namespace.type.v1~"
        assert path == "properties.name"

    def test_split_at_path_no_path(self):
        """Test splitting GTS ID without path."""
        gts, path = GtsID.split_at_path("gts.vendor.package.namespace.type.v1~")
        assert gts == "gts.vendor.package.namespace.type.v1~"
        assert path is None


class TestGtsWildcard:
    """Tests for GtsWildcard class."""

    def test_valid_wildcard_pattern(self):
        """Test valid wildcard pattern."""
        pattern = GtsWildcard("gts.vendor.package.*")
        assert pattern.id == "gts.vendor.package.*"

    def test_valid_wildcard_type_pattern(self):
        """Test valid wildcard type pattern."""
        pattern = GtsWildcard("gts.vendor.package.namespace.type.v1~*")
        assert pattern.id == "gts.vendor.package.namespace.type.v1~*"

    def test_invalid_wildcard_multiple_asterisks(self):
        """Test that multiple wildcards are rejected."""
        with pytest.raises(GtsInvalidWildcard) as exc_info:
            GtsWildcard("gts.*.package.*")
        assert "allowed only once" in str(exc_info.value)

    def test_invalid_wildcard_not_at_end(self):
        """Test that wildcard not at end is rejected."""
        with pytest.raises(GtsInvalidWildcard) as exc_info:
            GtsWildcard("gts.vendor.*.package")
        assert "allowed only at the end" in str(exc_info.value)

    def test_wildcard_match_basic(self):
        """Test wildcard matching."""
        gts_id = GtsID("gts.vendor.package.namespace.type.v1~")
        pattern = GtsWildcard("gts.vendor.package.*")
        assert gts_id.wildcard_match(pattern) is True

    def test_wildcard_match_exact(self):
        """Test exact matching with wildcard pattern."""
        gts_id = GtsID("gts.vendor.package.namespace.type.v1~")
        pattern = GtsWildcard("gts.vendor.package.namespace.type.v1~")
        assert gts_id.wildcard_match(pattern) is True

    def test_wildcard_match_no_match(self):
        """Test wildcard non-matching."""
        gts_id = GtsID("gts.vendor.package.namespace.type.v1~")
        pattern = GtsWildcard("gts.other.*")
        assert gts_id.wildcard_match(pattern) is False

    def test_wildcard_match_version_flexibility(self):
        """Test that minor version is flexible when not specified in pattern."""
        gts_id = GtsID("gts.vendor.package.namespace.type.v1.5~")
        pattern = GtsWildcard("gts.vendor.package.namespace.type.v1~")
        assert gts_id.wildcard_match(pattern) is True


class TestGtsIDEdgeCases:
    """Edge case tests for GTS IDs."""

    def test_trailing_tilde_creates_type(self):
        """Test that trailing tilde marks ID as type."""
        gts_id = GtsID("gts.vendor.package.namespace.type.v1~")
        assert gts_id.is_type is True

    def test_whitespace_trimmed(self):
        """Test that whitespace is trimmed."""
        gts_id = GtsID("  gts.vendor.package.namespace.type.v1~  ")
        assert gts_id.id == "gts.vendor.package.namespace.type.v1~"

    def test_version_zero_allowed(self):
        """Test that version 0 is allowed."""
        gts_id = GtsID("gts.vendor.package.namespace.type.v0~")
        assert gts_id.gts_id_segments[0].ver_major == 0

    def test_underscore_in_tokens_allowed(self):
        """Test that underscores are allowed in tokens."""
        gts_id = GtsID("gts.my_vendor.my_package.my_namespace.my_type.v1~")
        assert gts_id.gts_id_segments[0].vendor == "my_vendor"

    def test_combined_anonymous_id_uses_embedded_uuid(self):
        embedded_uuid = "7a1d2f34-5678-49ab-9012-abcdef123456"
        gts_id = GtsID(
            "gts.vendor.package.namespace.type.v1~" + embedded_uuid
        )

        assert gts_id.uuid_tail == embedded_uuid
        assert gts_id.to_uuid() == uuid.UUID(embedded_uuid)
        assert len(gts_id.gts_id_segments) == 2

    @pytest.mark.parametrize(
        "segment",
        [
            "vendor.package.namespace.type.v01",
            "vendor.package.namespace.type.v1.01",
            "vendor.package.namespace.type.v-1",
        ],
    )
    def test_rejects_noncanonical_versions(self, segment):
        with pytest.raises(GtsInvalidSegment):
            GtsIdSegment(1, 0, segment)

    def test_query_helpers_parse_and_match_filters(self):
        gts_id = GtsID(
            "gts.vendor.package.namespace.type.v1~vendor.package.namespace.item.v1"
        )
        base, filters = gts_id.parse_query(
            'gts.vendor.package.namespace.type.v1~[status="active"]'
        )

        assert base == "gts.vendor.package.namespace.type.v1~"
        assert filters == {"status": "active"}
        assert gts_id.match_query(
            {"gtsId": gts_id.id, "status": "active"},
            "gtsId",
            'gts.vendor.package.namespace.type.v1~[status="active"]',
        )

    def test_split_at_path_rejects_empty_selector(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            GtsID.split_at_path("gts.vendor.package.namespace.type.v1~@")
