# GTS Python Library

Python helpers and a reference HTTP service for the [Global Type System (GTS)](https://github.com/globaltypesystem/gts-spec). The package supports GTS identifier parsing, JSON Schema-backed validation, schema compatibility and derivation checks, traits, casting, queries, file loading, a CLI, and a FastAPI application.

The package targets GTS specification v0.13.1 and requires Python 3.9 or later.

## Installation

```bash
python -m pip install gts
```

The package installs these runtime dependencies:

- `jsonschema` for JSON Schema validation;
- `referencing` for standards-aware `$ref` resolution during instance validation;
- `jsonsubschema` for accepted-instance-set inclusion checks;
- `fastapi` and `uvicorn` for the HTTP server;
- `PyYAML` for YAML input.

## Quick start

`GtsOps` is the high-level in-memory API. It is intentionally imported from `gts.ops`; the package root exports the lower-level model, reader, store, and ID classes.

```python
from gts.ops import GtsOps

ops = GtsOps()
schema_id = "gts.example.demo._.event.v1~"
instance_id = "gts.example.demo._.event.v1~example.demo._.created.v1"

schema_result = ops.add_entity(
    {
        "$id": f"gts://{schema_id}",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["id", "name"],
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
        },
    },
    validate=True,
)
assert schema_result.ok

instance_result = ops.add_entity(
    {"id": instance_id, "name": "created"},
    validate=True,
)
assert instance_result.ok

assert ops.validate_entity(instance_id).ok
print(ops.get_entity(instance_id).to_dict())
```

`add_entity(..., validate=True)` validates a schema fully, including schema-chain, final/abstract, and trait checks. A failed registration is rolled back, including restoration of an entity that was replaced by the candidate.

## Public Python API

### Package-root exports

```python
from gts import (
    DEFAULT_GTS_CONFIG,
    GtsConfig,
    GtsEntity,
    GtsFile,
    GtsFileReader,
    GtsID,
    GtsIdSegment,
    GtsPathResolver,
    GtsReader,
    GtsStore,
    GtsWildcard,
    JsonEntity,
    JsonFile,
    JsonPathResolver,
    ValidationError,
    ValidationResult,
)
```

`JsonEntity`, `JsonFile`, and `JsonPathResolver` are backward-compatible aliases for their `Gts*` counterparts.

### GTS identifiers

```python
from gts import GtsID, GtsWildcard

schema_id = GtsID("gts.example.demo._.event.v1~")
assert schema_id.is_type
assert schema_id.get_type_id() is None
assert GtsID.is_valid("gts://gts.example.demo._.event.v1~")

instance_id = GtsID(
    "gts.example.demo._.event.v1~example.demo._.created.v1"
)
assert instance_id.get_type_id() == "gts.example.demo._.event.v1~"
print(instance_id.to_uuid())

pattern = GtsWildcard("gts.example.demo._.event.v1~*")
assert instance_id.wildcard_match(pattern)
```

`GtsID` accepts either `gts.<segment>...` or the URI form `gts://gts.<segment>...`. A type identifier ends in `~`; a well-known instance identifier appends one or more relative segments. `GtsID.to_uuid()` returns a deterministic UUID5, except combined anonymous IDs return their embedded UUID tail.

`GtsIdSegment` exposes `vendor`, `package`, `namespace`, `type`, `ver_major`, `ver_minor`, `is_type`, and `is_wildcard`. `GtsID.gts_id_segments` contains the parsed segments. `GtsID.split_at_path(value)` separates an optional `@path` selector, and `GtsID.parse_query(expr)` / `GtsID.match_query(obj, gts_field, expr)` provide lower-level query parsing and matching helpers.

Wildcard patterns may end in `.*` or `~*`. `~*` matches the base type and its descendants for ID matching; OP#10 queries apply an additional depth rule and return only IDs with a suffix at the wildcard position.

### Entities and configuration

```python
from gts import DEFAULT_GTS_CONFIG, GtsEntity

entity = GtsEntity(
    content={
        "id": "gts.example.demo._.event.v1~example.demo._.created.v1",
        "name": "created",
    },
    cfg=DEFAULT_GTS_CONFIG,
)

print(entity.raw_id)
print(entity.gts_id)
print(entity.type_id)
print(entity.selected_entity_field)
print(entity.selected_type_id_field)
```

`GtsEntity` detects schemas from `http://json-schema.org/` and `https://json-schema.org/` `$schema` URLs. It derives a raw ID from `GtsConfig.entity_id_fields` and an instance type ID from `GtsConfig.schema_id_fields`. `DEFAULT_GTS_CONFIG` recognizes common GTS field names including `$id`, `gtsId`, `id`, `gtsType`, and `type`.

`GtsPathResolver.resolve(path)` accepts dot paths, slash paths, and array indexes. It returns the resolver with `resolved`, `value`, `error`, and `available_fields` populated; `to_dict()` returns the corresponding serializable result.

Public entity helpers:

- `entity.resolve_path(path)` resolves dot, slash, and array-index paths and returns a `GtsPathResolver` result;
- `entity.cast(to_schema, from_schema, resolver=None)` casts an instance to a schema;
- `entity.gts_refs` and `entity.schemaRefs` list discovered GTS IDs and `$ref` values with source paths.

### File loading

`GtsFileReader(path, cfg=None)` accepts one file path or a list of file/directory paths. It recursively loads `.json`, `.jsonc`, `.gts`, `.yaml`, and `.yml` files, skips `node_modules`, `dist`, and `build` directories, and yields entities with valid GTS IDs. JSON-family files use Python's standard JSON parser, so `.jsonc` files must not contain comments.

```python
from gts import GtsFileReader, GtsStore

reader = GtsFileReader(["schemas", "instances.yaml"])
store = GtsStore(reader)
for entity_id, entity in store.items():
    print(entity_id, entity.is_schema)
```

TypeSpec (`.tsp`) inputs must be compiled to JSON Schema before loading.

### Store API

`GtsStore` is the low-level registry. Use `GtsStore(reader)` to populate it from a `GtsReader`, or `GtsStore(reader=None)` for an empty in-memory store.

| Method | Purpose |
| --- | --- |
| `register(entity)` / `unregister(entity_id)` | Add or remove an in-memory entity. Instances are keyed by `raw_id`; schemas use their GTS ID. |
| `register_schema(type_id, schema)` | Legacy schema registration helper; `type_id` must end in `~`. |
| `get(entity_id)` | Return `GtsEntity` or `None`. |
| `items()` | Return an iterator over in-memory `(entity_id, entity)` pairs. |
| `get_schema_content(type_id)` | Return a schema dictionary or raise `KeyError`. |
| `validate_schema_basic(type_id)` | Check `$ref` format, `x-gts-ref` declarations, and GTS keyword placement. |
| `validate_schema(type_id)` | Run full JSON Schema, derivation, final/abstract, x-gts-ref, and trait validation. |
| `validate_instance(gts_id)` | Validate a well-known or UUID-addressed instance against its type schema. |
| `is_minor_compatible(old_schema_id, new_schema_id)` | Return compatibility verdicts for two registered schemas. |
| `cast(from_id, target_schema_id)` | Cast a registered **instance** to a target schema. |
| `build_schema_graph(gts_id)` | Build the entity/schema reference graph. |
| `query(expr, limit=100)` | Execute an OP#10 query and return `GtsStoreQueryResult`. |

### High-level operations API

Import `GtsOps` and its result dataclasses from `gts.ops`.

```python
from gts.ops import GtsOps

ops = GtsOps(path=["schemas", "instances"])
ops.reload_from_path("replacement-directory")

ops.add_entity(content, validate=False)
ops.add_entities([content_a, content_b])
ops.add_schema(type_id, schema)
ops.extract_id(content)
ops.validate_id(gts_id)
ops.parse_id(gts_id)
ops.match_id_pattern(candidate, pattern)
ops.uuid(gts_id)
ops.validate_instance(gts_id)
ops.validate_schema(type_id)
ops.validate_entity(gts_id)
ops.schema_graph(gts_id)
ops.compatibility(old_schema_id, new_schema_id)
ops.cast(instance_id, target_schema_id)
ops.query(expr, limit=100)
ops.attr("gts.example.demo._.event.v1~@properties.name")
ops.get_entity(gts_id)
ops.get_entities(limit=100)
ops.list(limit=100)
```

Operation methods return result objects with `.to_dict()`. Validation failures are represented by `ok=False` and an `error` string in the facade API; direct `GtsStore` validation methods raise exceptions.

## Schema validation and GTS extensions

### JSON Schema dialects and references

The library validates schemas with the dialect named by `$schema`; if absent, it uses Draft 7 for schema meta-validation. GTS permits local JSON Pointers (`#/...`) and GTS references (`gts://gts...`) in `$ref`. Other external `$ref` URI schemes are rejected.

During instance validation, GTS references are resolved with `referencing.Registry`. Draft 2019-09 and Draft 2020-12 `$ref` sibling constraints are preserved, so a sibling such as `minLength` is enforced.

### `x-gts-ref`

`x-gts-ref` restricts a string value to a GTS ID or pattern. It accepts an absolute `gts.` pattern or a JSON Pointer beginning with `/` that resolves to one. If a store is present, the referenced entity must be registered.

`x-gts-ref` can appear in `oneOf`, `anyOf`, and `allOf`. For x-gts-ref-only combinator branches, GTS evaluates the x-gts-ref constraints as the combinator condition. For ordinary or mixed JSON Schema branches, normal JSON Schema structural matching determines which branch’s x-gts-ref constraints are applied.

### `x-gts-final` and `x-gts-abstract`

Both keywords must be top-level booleans and cannot both be `true`:

- `x-gts-final: true` prevents derived type schemas;
- `x-gts-abstract: true` prevents direct instances and defers required trait completeness to concrete descendants.

### Traits

`x-gts-traits-schema` declares the schema of type traits, while `x-gts-traits` supplies values. Effective schemas compose through `allOf`; values merge root-to-leaf according to RFC 7396 JSON Merge Patch.

Missing trait properties are materialized from the nearest `default`, including `default: null`. A JSON Schema `const` constrains a supplied value but is **not** materialized as a missing trait value. Concrete types must resolve required trait properties; abstract types still validate supplied trait values but defer completeness.

### Derivation and compatibility

OP#12 derivation accepts a derived schema only when its declared accepted-instance set is included in its base schema, subject to GTS rules for disabled properties and closed `additionalProperties` branches.

OP#8 compatibility has three string verdicts:

- `compatible`: inclusion was proved;
- `incompatible`: inclusion was disproved;
- `unknown`: the inclusion engine could not prove the relation.

`GtsEntityCastResult.to_dict()` returns `backward_compatibility`, `forward_compatibility`, and `full_compatibility` using those strings. Its boolean `is_*_compatible` fields are `True` only for `compatible`; they are `False` for both `incompatible` and `unknown`.

## HTTP server

Start a local server:

```bash
gts --path schemas server --host 127.0.0.1 --port 8000
```

`GtsHttpServer` is available from `gts.server` when embedding the application:

```python
from gts.ops import GtsOps
from gts.server import GtsHttpServer

app = GtsHttpServer(ops=GtsOps()).app
```

| Endpoint | Method | Request |
| --- | --- | --- |
| `/entities` | `GET` | `limit` query parameter, 1–1000; lists registered entities. |
| `/entities/{gts_id}` | `GET` | Retrieves one entity. |
| `/entities` | `POST` | Entity/schema body; optional `validate=true` runs full validation. Failed registration returns 422 and is rolled back. |
| `/entities/bulk` | `POST` | JSON array of entity/schema objects. |
| `/type-schemas` | `POST` | `{"type_id": "...~", "type_schema": {...}}`. |
| `/validate-id` | `GET` | `gts_id` query parameter. |
| `/extract-id` | `POST` | JSON entity/schema object. |
| `/parse-id` | `GET` | `gts_id` query parameter. |
| `/match-id-pattern` | `GET` | `candidate` and `pattern` query parameters. |
| `/uuid` | `GET` | `gts_id` query parameter. |
| `/validate-instance` | `POST` | `{"instance_id": "..."}`. |
| `/validate-type-schema` | `POST` | `{"type_id": "...~"}`. |
| `/validate-entity` | `POST` | `{"entity_id": "..."}` or `{"gts_id": "..."}`. When both are supplied, they must be equal. |
| `/resolve-relationships` | `GET` | `gts_id` query parameter. |
| `/compatibility` | `GET` | `old_type_id` and `new_type_id` query parameters. |
| `/cast` | `POST` | `{"instance_id": "...", "to_type_id": "...~"}`. |
| `/query` | `GET` | `expr` and optional `limit` query parameters, 1–1000. |
| `/attr` | `GET` | `gts_with_path` query parameter containing `@path`. |

FastAPI exposes interactive OpenAPI documentation when the server is running. Generate the OpenAPI JSON without running the service:

```bash
gts openapi-spec --out openapi.json
```

## CLI

All commands accept optional global `--path`, `--config`, and repeatable `-v` / `--verbose` options. Place global options before the subcommand, for example `gts --path schemas query --expr 'gts.example.*'`.

```bash
gts validate-id --gts-id 'gts.example.demo._.event.v1~'
gts parse-id --gts-id 'gts.example.demo._.event.v1~'
gts match-id-pattern --candidate 'gts.example.demo._.event.v1~' --pattern 'gts.example.*'
gts uuid --gts-id 'gts.example.demo._.event.v1~'
gts validate-instance --gts-id 'gts.example.demo._.event.v1~example.demo._.created.v1'
gts resolve-relationships --gts-id 'gts.example.demo._.event.v1~'
gts compatibility --old-schema-id 'gts.example.demo._.event.v1.0~' --new-schema-id 'gts.example.demo._.event.v1.1~'
gts cast --from-id 'gts.example.demo._.event.v1~example.demo._.created.v1' --to-schema-id 'gts.example.demo._.event.v1.1~'
gts query --expr 'gts.example.demo.*[status=active]' --limit 10
gts attr --gts-with-path 'gts.example.demo._.event.v1~@properties.name'
gts list --limit 100
gts server --port 8000
gts openapi-spec --out openapi.json
```

CLI commands print their result as JSON. The CLI provides `validate-instance`, but not a `validate-schema` subcommand; use the Python API or `POST /validate-type-schema` for full type-schema validation.

## File format support

### JSON and YAML

JSON (`.json`, `.jsonc`, `.gts`) and YAML (`.yaml`, `.yml`) files are loaded identically by `GtsFileReader`.

### TypeSpec

Compile TypeSpec schemas to JSON Schema before loading them:

```bash
npm install -g @typespec/compiler @typespec/json-schema
tsp compile --emit @typespec/json-schema your-schemas/
```

```python
from gts import GtsFileReader

entities = list(GtsFileReader("tsp-output/@typespec/json-schema/"))
```
