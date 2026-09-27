"""Concurrency test for validate=true batch staging.

A ``validate=true`` batch stages entries invisibly, validates them, and commits
only the survivors, so a concurrent reader must never observe an entity that has
not passed validation. This test runs a batch containing a deliberately-invalid
entry while several threads hammer the committed read path for that entry's id,
asserting it is never observable. The cycle is repeated many times to widen the
window. ``GtsStore`` is thread-safe (an ``RLock`` guards the maps) and no lock is
held across validation, so the reads run concurrently with the batch writer.
"""

import threading
import uuid

from gts.ops import GtsOps

_DRAFT7 = "http://json-schema.org/draft-07/schema#"
_CYCLES = 10
_PROBE_THREADS = 4
_VALID_PER_BATCH = 80


def _batch(ns: str):
    valid = [
        {
            "$schema": _DRAFT7,
            "$id": f"gts://gts.x.{ns}._.t{i}.v1~",
            "type": "object",
            "properties": {"p": {"type": "string"}},
        }
        for i in range(_VALID_PER_BATCH)
    ]
    invalid_id = f"gts.x.{ns}._.invalid.v1~"
    invalid = {
        "$schema": _DRAFT7,
        "$id": f"gts://{invalid_id}",
        "type": "object",
        "properties": {"a": {"$ref": f"gts://gts.x.{ns}._.never.v1~"}},
    }
    return valid + [invalid], invalid_id


def test_validate_batch_never_exposes_uncommitted_entities():
    ops = GtsOps(path=None)
    leaks: list[object] = []
    leaks_lock = threading.Lock()

    for cycle in range(_CYCLES):
        ns = f"pyconc{uuid.uuid4().hex[:8]}c{cycle}"
        batch, invalid_id = _batch(ns)
        stop = threading.Event()

        def probe(invalid_id=invalid_id):
            while not stop.is_set():
                entity = ops.store.get_committed(invalid_id)
                if entity is not None:
                    with leaks_lock:
                        leaks.append(entity)

        probers = [threading.Thread(target=probe) for _ in range(_PROBE_THREADS)]
        for prober in probers:
            prober.start()
        try:
            result = ops.add_schemas(batch, validate=True)
        finally:
            stop.set()
            for prober in probers:
                prober.join(timeout=10)

        assert result.ok is False, "batch with an invalid entry must report ok=false"
        # The valid entries are committed; the invalid one is not.
        assert ops.store.get_committed(f"gts.x.{ns}._.t0.v1~") is not None
        assert ops.store.get_committed(invalid_id) is None

    assert leaks == [], (
        "an uncommitted/invalid entity was exposed to a concurrent reader "
        f"({len(leaks)} time(s))"
    )
