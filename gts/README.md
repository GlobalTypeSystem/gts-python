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

### TypeSpec
TypeSpec (`.tsp`) schemas must be pre-compiled to JSON Schema before use with gts-python.

**Setup:**
```bash
# Install TypeSpec compiler
npm install -g @typespec/compiler @typespec/json-schema

# Compile TypeSpec to JSON Schema
tsp compile --emit @typespec/json-schema your-schemas/
```

**Usage:**
```python
from gts import GtsFileReader

# Point to the generated JSON Schema output directory
reader = GtsFileReader("tsp-output/@typespec/json-schema/")
entities = list(reader)
```

See [gts-spec TypeSpec examples](https://github.com/globaltypesystem/gts-spec/tree/main/examples/typespec) for sample TypeSpec definitions.

## Featureset

GTS specification v0.13.1 reference implementation status:

---

- [x] **OP#1 - ID Validation**: Verify identifier syntax

```python
from gts import GtsID

is_valid = GtsID.is_valid("gts.x.core.events.event.v1~")
print(is_valid)  # True or False
```

---

- [x] **OP#2 - ID Extraction**: Extract GTS ID, type_id, and metadata from JSON objects or JSON Schema documents

```python
import json
from gts import GtsEntity, DEFAULT_GTS_CONFIG

content = json.load(open("path/to/file.json"))
entity = GtsEntity(content=content, cfg=DEFAULT_GTS_CONFIG)
if entity.gts_id:
    print(entity.gts_id.id)      # GTS identifier
    print(entity.type_id)         # Parent type ID (if chained)
    print(entity.is_schema)       # True if JSON Schema entity
```

---

- [x] **OP#3 - ID Parsing**: Decompose identifiers into constituent parts (vendor, package, namespace, type, version)

```python
from gts import GtsID

gts = GtsID("gts.x.core.events.event.v1~")
print(gts.is_type)            # True
for seg in gts.gts_id_segments:
    print(seg.vendor, seg.package, seg.namespace, seg.type)
```

Supports combined anonymous instance IDs with UUID tails:
```python
gts = GtsID("gts.x.core.events.type.v1~x.orders.v1.0~7a1d2f34-5678-49ab-9012-abcdef123456")
print(gts.uuid_tail)  # "7a1d2f34-5678-49ab-9012-abcdef123456"
```

---

- [x] **OP#4 - ID Pattern Matching**: Match identifiers against wildcard patterns

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
uuid = gts.to_uuid()  # UUID5 based on GTS namespace
```

Combined anonymous instances return their embedded UUID directly.

---

- [x] **OP#6 - Schema Validation**: Validate object instances against their type schemas. Supports `x-gts-abstract` (rejects direct instances) and `x-gts-ref` constraints.

```python
from gts import GtsStore, GtsFileReader

reader = GtsFileReader(path="path/to/gts/files")
store = GtsStore(reader=reader)
store.validate_instance(gts_id="gts.x.core.events.event.v1~instance.v1")
```

---

- [x] **OP#7 - Relationship Resolution**: Build schema/entity dependency graphs

```python
from gts import GtsStore, GtsFileReader

reader = GtsFileReader(path="path/to/gts/files")
store = GtsStore(reader=reader)
graph = store.build_schema_graph(gts_id="gts.x.core.events.event.v1~")
```

---

- [x] **OP#8 - Compatibility Checking**: Check Type Schema evolution compatibility

- [x] **OP#8.1 - Backward compatibility**
- [x] **OP#8.2 - Forward compatibility**
- [x] **OP#8.3 - Full compatibility**

```python
from gts import GtsStore, GtsFileReader

reader = GtsFileReader(path="path/to/gts/files")
store = GtsStore(reader=reader)
result = store.is_minor_compatible(
    "gts.x.core.events.event.v1.0~",
    "gts.x.core.events.event.v1.1~"
)
print(result.is_backward_compatible)
print(result.is_forward_compatible)
print(result.is_fully_compatible)
```

---

- [x] **OP#9 - Version Casting**: Cast instances between compatible MINOR versions

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

- [x] **OP#10 - Query Execution**: Filter entities using the GTS query language

```python
from gts import GtsStore, GtsFileReader

reader = GtsFileReader(path="path/to/gts/files")
store = GtsStore(reader=reader)
result = store.query("gts.x.core.events.event.v1~[status=active]")
print(f"Found {result.count} entities")
```

---

- [x] **OP#11 - Attribute Access**: Retrieve property values via the attribute selector (`@`)

```python
from gts import GtsStore, GtsFileReader

reader = GtsFileReader(path="path/to/gts/files")
store = GtsStore(reader=reader)
entity = store.get("gts.x.core.events.event.v1~")
if entity:
    res = entity.resolve_path("gtsId")
    if res.resolved:
        print(res.value)
```

---

- [x] **OP#12 - Type Derivation Validation**: Validate that derived GTS Type Schemas correctly extend their base chain

Checks derivation chain compatibility including:
- Constraint tightening (valid) vs loosening (invalid)
- `additionalProperties` closedness inheritance
- `x-gts-final` enforcement (blocks derivation)
- Property type compatibility

---

- [x] **OP#13 - Schema Traits Validation**: Validate `x-gts-traits` and `x-gts-traits-schema`

Supports:
- Effective trait schema composition via `allOf` across the chain
- RFC 7396 JSON Merge Patch for trait value merging
- `const` / `default` materialization
- Required-trait completeness checking (skipped for `x-gts-abstract` types)

---

### Schema Modifiers

- **`x-gts-final`**: Prevents type derivation. Validated during OP#12 chain checks.
- **`x-gts-abstract`**: Prevents direct instantiation. Validated during OP#6 instance validation.
- Both must be booleans, are mutually exclusive, and must appear at schema top level only.
