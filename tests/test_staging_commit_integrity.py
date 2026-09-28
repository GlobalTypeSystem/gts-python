"""Integrity tests for the validate=true staging/commit flow.

These cover the guarantees a validate=true batch must uphold beyond "a staged
entity is never publicly visible" (see ``test_staging_concurrency.py``):

* A staged entry is identified by a unique token, so a duplicated id (or two
  concurrent batches) never clobber each other and a commit never silently
  overwrites conflicting committed content.
* A survivor is never published when a sibling it depends on is discarded in the
  same batch (iterative discard-then-revalidate).
"""

from gts.entities import GtsEntity
from gts.ops import GtsOps, GtsRefValidationMode

_D7 = "http://json-schema.org/draft-07/schema#"


def _schema(type_id: str, title: str) -> dict:
    return {
        "$schema": _D7,
        "$id": f"gts://{type_id}",
        "type": "object",
        "title": title,
    }


def test_staging_same_id_twice_yields_distinct_tokens_and_commit_does_not_overwrite():
    ops = GtsOps(path=None)
    tid = "gts.x.pyunit._.dup.v1~"
    prep_a = ops._prepare_type_schema(_schema(tid, "a"))
    prep_b = ops._prepare_type_schema(_schema(tid, "b"))
    _, entity_a, _ = prep_a
    _, entity_b, _ = prep_b

    token_a = ops.store.stage(entity_a)
    token_b = ops.store.stage(entity_b)
    assert token_a != token_b

    # First commit publishes A; committing B (different content) must conflict,
    # not clobber A.
    assert ops.store.commit(token_a) == "added"
    assert ops.store.commit(token_b) == "conflict"
    committed = ops.store.get_committed(tid)
    assert committed is not None
    assert committed.content["title"] == "a"


def test_committing_identical_staged_content_twice_is_unchanged():
    ops = GtsOps(path=None)
    tid = "gts.x.pyunit._.same.v1~"
    _, entity_a, _ = ops._prepare_type_schema(_schema(tid, "x"))
    _, entity_b, _ = ops._prepare_type_schema(_schema(tid, "x"))
    token_a = ops.store.stage(entity_a)
    token_b = ops.store.stage(entity_b)
    assert ops.store.commit(token_a) == "added"
    assert ops.store.commit(token_b) == "unchanged"


def test_discarding_one_token_leaves_another_staged_entry_for_the_same_id():
    ops = GtsOps(path=None)
    tid = "gts.x.pyunit._.iso.v1~"
    _, entity_a, _ = ops._prepare_type_schema(_schema(tid, "keep"))
    _, entity_b, _ = ops._prepare_type_schema(_schema(tid, "drop"))
    token_a = ops.store.stage(entity_a)
    token_b = ops.store.stage(entity_b)
    ops.store.discard(token_b)
    assert ops.store.commit(token_a) == "added"
    assert ops.store.get_committed(tid).content["title"] == "keep"


def test_batch_does_not_commit_dependent_of_discarded_sibling():
    """Under any-present ref validation, B x-gts-refs A while A x-gts-refs a type
    that is never registered. Validated against the fully staged set, B passes
    (A is present) while A fails (its target is missing) - a single-pass
    implementation would then commit B with a dangling reference to the discarded
    A. The iterative discard-then-revalidate must reject B too, so neither is
    retrievable afterwards."""
    ops = GtsOps(path=None)
    a = {
        "$schema": _D7,
        "$id": "gts://gts.x.pydep._.a.v1~",
        "type": "object",
        "properties": {
            "r": {"type": "string", "x-gts-ref": "gts.x.pydep._.missing.v1~"}
        },
    }
    b = {
        "$schema": _D7,
        "$id": "gts://gts.x.pydep._.b.v1~",
        "type": "object",
        "properties": {"x": {"type": "string", "x-gts-ref": "gts.x.pydep._.a.v1~"}},
    }
    res = ops.add_schemas(
        [a, b], validate=True, gts_ref_validation=GtsRefValidationMode.ANY_PRESENT
    )
    assert res.ok is False
    assert all(not r.ok for r in res.results)
    assert ops.store.get_committed("gts.x.pydep._.a.v1~") is None
    assert ops.store.get_committed("gts.x.pydep._.b.v1~") is None


def test_batch_with_conflicting_duplicate_id_reports_conflict():
    """A batch that carries the same $id twice with different content is
    internally inconsistent, so the atomic publish must keep NEITHER entry (no
    silent last-wins): the batch is rejected as a whole and nothing is committed."""
    ops = GtsOps(path=None)
    tid = "gts.x.pydup._.t.v1~"
    res = ops.add_schemas([_schema(tid, "a"), _schema(tid, "b")], validate=True)
    assert res.ok is False
    assert all(not r.ok for r in res.results)
    # All-or-nothing: nothing from the inconsistent batch is published.
    assert ops.store.get_committed(tid) is None


def test_snapshot_overlay_is_scoped_to_its_own_staging_session():
    """Regression for the cross-request staging leak: a schema staged under one
    session must be invisible to reads made under a different session (or none),
    so one batch never resolves a $ref/parent against another batch's
    not-yet-validated entry."""
    ops = GtsOps(path=None)
    tid = "gts.x.pysess._.a.v1~"
    _, entity, _ = ops._prepare_type_schema(_schema(tid, "a"))

    with ops.store.staging_session() as session_a:
        ops.store.stage(entity)
        # Visible to its own session.
        assert ops.store.get(tid) is not None
        # Invisible to a different session and to committed-only reads.
        with ops.store.staging_session():
            assert ops.store.get(tid) is None
        assert ops.store.get_committed(tid) is None
    # Outside any session (committed-only), still invisible.
    assert ops.store.get(tid) is None
    assert session_a  # session id was returned


def test_commit_batch_publishes_nothing_when_a_target_conflicts_with_committed():
    """Regression for the non-atomic commit loop: if a survivor's target was
    committed with different content by a concurrent writer, the whole batch
    publishes nothing rather than committing the dependent against changed
    content."""
    ops = GtsOps(path=None)
    parent = "gts.x.pyatomic._.parent.v1~"
    child = "gts.x.pyatomic._.child.v1~"

    # A concurrent writer already committed the parent id with different content.
    committed = ops.add_schema(_schema(parent, "committed"))
    assert committed.ok

    # Build entities directly to simulate a batch whose parent target was won by
    # a concurrent writer AFTER staging (the ops pre-check would otherwise reject
    # staging a conflicting id up front).
    with ops.store.staging_session():
        p_entity = GtsEntity(content=_schema(parent, "staged"), cfg=ops.cfg)
        c_entity = GtsEntity(content=_schema(child, "ok"), cfg=ops.cfg)
        c_token = ops.store.stage(c_entity)
        p_token = ops.store.stage(p_entity)
        outcomes = ops.store.commit_batch([c_token, p_token])
        assert outcomes == ["conflict", "conflict"]

    # Nothing from the batch was published; the parent keeps its committed content.
    assert ops.store.get_committed(child) is None
    assert ops.store.get_committed(parent).content["title"] == "committed"
