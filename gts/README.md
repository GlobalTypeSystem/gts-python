# GTS Python Library

A minimal, idiomatic Python library for working with **GTS** ([Global Type System](https://github.com/gts-spec/gts-spec)) identifiers and JSON/JSON Schema artifacts.

## Featureset

GTS specifiaciton reference implementations status:

---

- [x] **OP#1 - ID Validation**: Verify identifier syntax using regex patterns

```python
from gts import GtsID

GtsID.is_valid("gts.x.core.events.event.v1~")
```

---

- [x] **OP#2 - ID Extraction**: Fetch identifiers from JSON objects or JSON Schema documents

```python
from gts import JsonEntity

content = json.load(open("path/to/file.json"))
entity = JsonEntity(content=content)
entity.gts_id.id
```

---

- [x] **OP#3 - ID Parsing**: Decompose identifiers into constituent parts (vendor, package, namespace, type, version, etc.)

```python
from gts import GtsID

gts = GtsID("gts.x.core.events.event.v1~")
print(gts.gts_id_segments)
```

---

- [x] **OP#4 - ID Pattern Matching**: Match identifiers against patterns containing wildcards

```python
from gts import GtsID, GtsWildcard

gts = GtsID("gts.x.core.events.event.v1~")
pattern = GtsWildcard("gts.x.core.*")
gts.match(pattern)
```

---

- [x] **OP#5 - ID to UUID Mapping**: Generate deterministic UUIDs from GTS identifiers

```python
from gts import GtsID, GtsWildcard

gts = GtsID("gts.x.core.events.event.v1~")
gts.to_uuid()
```

---

- [x] **OP#6 - Schema Validation**: Validate object instances against their corresponding schemas

```python
from gts import GtsID, GtsStore, GtsFileReader

reader = GtsFileReader(path="path/to/gts/files")
store = GtsStore(reader=reader)
store.validate_instance(gts_id="gts.x.core.events.event.v1~")
```

---

- [x] **OP#7 - Relationship Resolution**: Load all schemas and instances, resolve inter-dependencies, and detect broken references

```python
from gts import GtsID, GtsStore, GtsFileReader

reader = GtsFileReader(path="path/to/gts/files")
store = GtsStore(reader=reader)
store.get("gts.x.core.events.event.v1~")
# or build a dependency graph for a specific GTS ID
graph = store.build_schema_graph(gts_id="gts.x.core.events.event.v1~")
```

---

- [ ] **OP#8 - Compatibility Checking**: Verify that schemas with different MINOR versions are compatible

- [ ] **OP#8.1 - Backward compatibility checking**
- [ ] **OP#8.2 - Forward compatibility checking**
- [ ] **OP#8.3 - Full compatibility checking**

---

- [ ] **OP#9 - Version Casting**: Transform instances between compatible MINOR versions

---

- [ ] **OP#10 - Query Execution**: Filter identifier collections using the GTS query language

---

- [x] **OP#11 - Attribute Access**: Retrieve property values and metadata using the attribute selector (`@`)

```python
from gts import GtsStore, GtsFileReader

reader = GtsFileReader(path="path/to/gts/files")
store = GtsStore(reader=reader)
entity = store.get("gts.x.core.events.event.v1~")
res = entity.resolve_path("gtsId")  # see res.value or res.error
```
