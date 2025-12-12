# GTS Python Library

A minimal, idiomatic Python library for working with **GTS** ([Global Type System](https://github.com/gts-spec/gts-spec)) identifiers and type definitions.

## File Format Support

GTS Python supports multiple file formats for schemas and instances:

### JSON (Native)
Standard JSON format with `.json`, `.jsonc`, and `.gts` extensions.

### YAML
Full YAML support with `.yaml` and `.yml` extensions. YAML files are automatically parsed and treated identically to JSON.

```python
from gts import GtsFileReader

# Reads both JSON and YAML files
reader = GtsFileReader("path/to/schemas/")
for entity in reader:
    print(f"{entity.gts_id.id}: {entity.file.name}")
```

### TypeSpec (Native Compilation)

TypeSpec (`.tsp`) files are now natively supported. When a `.tsp` file is encountered, gts-python automatically compiles it to JSON Schema using the TypeSpec compiler.

**Requirements:**
```bash
# Install TypeSpec compiler (one-time setup)
npm install -g @typespec/compiler @typespec/json-schema
```

**Usage:**
```python
from gts import GtsFileReader

# Reads JSON, YAML, and TypeSpec files directly
reader = GtsFileReader("path/to/schemas/")
for entity in reader:
    print(f"{entity.gts_id.id}: {entity.file.name}")
```

TypeSpec files are automatically compiled on-the-fly. If the TypeSpec compiler is not available, `.tsp` files are skipped with a warning.

See [gts-spec TypeSpec examples](https://github.com/globaltypesystem/gts-spec/tree/main/examples/typespec) for sample TypeSpec definitions.

## Featureset

GTS specifiaciton reference implementations status:

---

- [x] **OP#1 - ID Validation**: Verify identifier syntax using regex patterns

```python
from gts import GtsID

is_valid = GtsID.is_valid("gts.x.core.events.event.v1~")
print(is_valid)  # True or False
```

---

- [x] **OP#2 - ID Extraction**: Fetch identifiers from JSON objects or JSON Schema documents

```python
import json
from gts import GtsEntity, DEFAULT_GTS_CONFIG

content = json.load(open("path/to/file.json"))
entity = GtsEntity(content=content, cfg=DEFAULT_GTS_CONFIG)
if entity.gts_id:
    print(entity.gts_id.id)
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

gts = GtsID("gts.x.core.events.event.v1.0~")
pattern = GtsWildcard("gts.x.core.events.event.v1~*")
gts.wildcard_match(pattern)  # True - v1~* matches any v1.x~
```

---

- [x] **OP#5 - ID to UUID Mapping**: Generate deterministic UUIDs from GTS identifiers

```python
from gts import GtsID

gts = GtsID("gts.x.core.events.event.v1~")
uuid = gts.to_uuid()
print(uuid)
```

---

- [x] **OP#6 - Schema Validation**: Validate object instances against their corresponding schemas

```python
from gts import GtsStore, GtsFileReader

reader = GtsFileReader(path="path/to/gts/files")
store = GtsStore(reader=reader)
try:
    store.validate_instance(gts_id="gts.x.core.events.event.v1.0~instance.v1")
    print("Validation successful")
except Exception as e:
    print(f"Validation failed: {e}")
```

---

- [x] **OP#7 - Relationship Resolution**: Load all schemas and instances, resolve inter-dependencies, and detect broken references

```python
from gts import GtsStore, GtsFileReader

reader = GtsFileReader(path="path/to/gts/files")
store = GtsStore(reader=reader)
entity = store.get("gts.x.core.events.event.v1~")
# or build a dependency graph for a specific GTS ID
graph = store.build_schema_graph(gts_id="gts.x.core.events.event.v1~")
```

---

- [x] **OP#8 - Compatibility Checking**: Verify that schemas with different MINOR versions are compatible

- [x] **OP#8.1 - Backward compatibility checking**
- [x] **OP#8.2 - Forward compatibility checking**
- [x] **OP#8.3 - Full compatibility checking**

```python
from gts import GtsStore, GtsFileReader

reader = GtsFileReader(path="path/to/gts/files")
store = GtsStore(reader=reader)
compatible = store.is_minor_compatible(
    "gts.x.core.events.event.v1.0~",
    "gts.x.core.events.event.v1.1~"
)
print(compatible.is_backward_compatible)
print(compatible.is_forward_compatible)
print(compatible.is_fully_compatible)
```

---

- [x] **OP#9 - Version Casting**: Transform instances between compatible MINOR versions

```python
from gts import GtsStore, GtsFileReader

reader = GtsFileReader(path="path/to/gts/files")
store = GtsStore(reader=reader)
result = store.cast(
    from_id="gts.x.core.events.event.v1.0~instance.v1",
    target_schema_id="gts.x.core.events.event.v1.1~"
)
```

---

- [x] **OP#10 - Query Execution**: Filter identifier collections using the GTS query language

```python
from gts import GtsStore, GtsFileReader

reader = GtsFileReader(path="path/to/gts/files")
store = GtsStore(reader=reader)
result = store.query("gts.x.core.events.event.v1~[status=active, user=123]")
print(f"Found {result.count} entities")
for entity in result.results:
    print(entity)
```

---

- [x] **OP#11 - Attribute Access**: Retrieve property values and metadata using the attribute selector (`@`)

```python
from gts import GtsStore, GtsFileReader

reader = GtsFileReader(path="path/to/gts/files")
store = GtsStore(reader=reader)
entity = store.get("gts.x.core.events.event.v1~")
if entity:
    res = entity.resolve_path("gtsId")
    if res.resolved:
        print(res.value)
    else:
        print(res.error)
```
