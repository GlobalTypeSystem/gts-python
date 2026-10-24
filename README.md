> Status: initial draft v0.1, not for production use

# GTS Python Library

A minimal, idiomatic Python library for working with **GTS** ([Global Type System](https://github.com/GlobalTypeSystem/gts-spec)) identifiers and JSON/JSON Schema artifacts.

Targets gts-spec [v0.17.0](https://github.com/GlobalTypeSystem/gts-spec/releases/tag/v0.17.0) — recorded in [`.gts-spec-version`](.gts-spec-version) and pinned by the `.gts-spec` submodule. Run `make update-spec` to check the pinned release out.

## Roadmap

Featureset:

- [x] **OP#1 - ID Validation**: Verify identifier syntax using regex patterns
- [x] **OP#2 - ID Extraction**: Fetch identifiers from JSON objects or JSON Schema documents
- [x] **OP#3 - ID Parsing**: Decompose identifiers into constituent parts (vendor, package, namespace, type, version, etc.)
- [x] **OP#4 - ID Pattern Matching**: Match identifiers against patterns containing wildcards
- [x] **OP#5 - ID to UUID Mapping**: Generate deterministic UUIDs from GTS identifiers
- [x] **OP#6 - Instance Validation**: Validate object instances against their corresponding schemas
- [x] **OP#7 - Relationship Resolution**: Load all schemas and instances, resolve inter-dependencies, and detect broken references
- [x] **OP#8 - Compatibility Checking**: Verify that schemas with different MINOR versions are compatible
- [x] **OP#8.1 - Backward compatibility checking**
- [x] **OP#8.2 - Forward compatibility checking**
- [x] **OP#8.3 - Full compatibility checking**
- [x] **OP#9 - Version Casting**: Transform instances between compatible MINOR versions
- [x] **OP#10 - Query Execution**: Filter identifier collections using the GTS query language
- [x] **OP#11 - Attribute Access**: Retrieve property values and metadata using the attribute selector (`@`)
- [x] **OP#12 - Type Derivation Validation**: Validate that derived GTS Type Schemas correctly extend their base chain
- [x] **OP#13 - Schema Traits Validation**: Validate schema traits (`x-gts-traits-schema` / `x-gts-traits`).

See details in [gts/README.md](gts/README.md)

Other GTS spec [Reference Implementation](https://github.com/GlobalTypeSystem/gts-spec/blob/main/README.md#9-reference-implementation-recommendations) recommended features support:

- [x] **In-memory entities registry** - simple GTS entities registry with optional GTS references validation on entity registration
- [x] **CLI** - command-line interface for all GTS operations
- [x] **Web server** - a non-production web-server with REST API for the operations processing and testing
- [x] **x-gts-type-ref / x-gts-instance-ref** - kind-specific GTS entity references in schemas (spec §9.6.1)
- [x] **x-gts-pattern-ref** - pattern-valued string fields: GTS identifier pattern syntax and coverage by an operand, independent of the registry (spec §9.6.2, §10)
- [x] **x-gts-final / x-gts-abstract** - GTS Type Schema modifiers controlling inheritance and instantiation
- [x] **YAML support** - to support YAML files (*.yml, *.yaml) as input files
- [x] **UUID for instances** - to support UUID as ID in JSON instances (anonymous instances)
- [ ] **TypeSpec support** - direct support for [typespec.io](https://typespec.io/) files (*.tsp) input files

Technical Backlog:

- [x] **Code coverage** - target is 90%
- [x] **Documentation** - add documentation for all the features
- [x] **Interface** - export publicly available interface and keep cli and others private
- [x] **Server API** - finalise the server API
- [x] **Final code cleanup** - remove unused code, denormalize, add critical comments, etc.

## Installation

GTS requires Python 3.9 or later. `google-re2` ships wheels for CPython 3.10–3.14; on Python 3.9 pip builds it from source, which needs a C++17 compiler and the RE2 and Abseil development headers.

### Local development

From the repository root, build and install the same wheel artifact that will later be published and installed with `pip install gts`. `install-local` requires an explicit `PYTHON` environment variable so you choose the target interpreter:

```bash
PYTHON=.venv/bin/python make install-local
```

This creates the `.venv` virtual environment when needed, writes source and wheel distributions to `dist/`, and installs the wheel into the interpreter specified by `PYTHON`. Activate it to use the locally installed library and CLI:

```bash
source .venv/bin/activate
python -c "import gts; print(gts.__file__)"
gts --help
```

Use `make build` when you only need the distributable artifacts. Remove the locally installed package with `PYTHON=.venv/bin/python make uninstall-local`. For an editable installation while changing source files, use `make install`.

### Published package

After `gts` is published to PyPI, install it with:

```bash
pip install gts
```

## Usage

### CLI

```bash
# Basic operations
gts validate-id --gts-id 'gts.example.demo._.event.v1~'
gts parse-id --gts-id 'gts.example.demo._.event.v1~'
gts match-id-pattern --candidate 'gts.example.demo._.event.v1~' --pattern 'gts.example.*'
gts uuid --gts-id 'gts.example.demo._.event.v1~'

# Operations with loaded entities.
# Placeholders: point --path at a directory holding your GTS schemas and
# instances, and substitute their IDs (no ./examples directory ships with
# this repository; the gts-spec submodule's .gts-spec/examples has samples).
gts --path ./examples validate-instance --gts-id 'gts.example.demo._.event.v1~example.demo._.created.v1'
gts --path ./examples validate-type-schema --gts-id 'gts.example.demo._.event.v1.1~'
gts --path ./examples validate-entity --gts-id 'gts.example.demo._.event.v1~'
gts --path ./examples resolve-relationships --gts-id 'gts.example.demo._.event.v1~'
gts --path ./examples compatibility --old-schema-id 'gts.example.demo._.event.v1.0~' --new-schema-id 'gts.example.demo._.event.v1.1~'
gts --path ./examples cast --from-id 'gts.example.demo._.event.v1~example.demo._.created.v1' --to-schema-id 'gts.example.demo._.event.v1.1~'
gts --path ./examples query --expr 'gts.example.*' --limit 10
gts --path ./examples attr --gts-with-path 'gts.example.demo._.event.v1~@properties.name'
gts --path ./examples list --limit 100

# See all commands and options
gts --help
```

### Library

`GtsOps` provides the high-level in-memory API:

```python
from gts.ops import GtsOps

ops = GtsOps()

# OP#1 - ID Validation
result = ops.validate_id("gts.example.demo._.event.v1~")
assert result.valid

# OP#2 - ID Extraction
extracted = ops.extract_id({"id": "gts.example.demo._.event.v1.0", "name": "Event"})
print(extracted.id)

# OP#3 - ID Parsing
parsed = ops.parse_id("gts.example.demo._.event.v1~")
for seg in parsed.segments:
    print(seg.vendor, seg.package, seg.type, seg.ver_major)

# OP#4 - Pattern Matching
match = ops.match_id_pattern("gts.example.demo._.event.v1.0", "gts.example.*")
assert match.match

# OP#5 - ID to UUID Mapping
uuid_res = ops.uuid("gts.example.demo._.event.v1~")
print(uuid_res.uuid)
```

See [gts/README.md](gts/README.md) for full library documentation including schema validation, store operations, query execution, traits, and casting.

### Web server

The web server is a non-production web server with REST API for operations processing and testing. It implements the reference API for [gts-spec tests](https://github.com/GlobalTypeSystem/gts-spec/tree/main/tests).

```bash
# Start the web server (default: http://127.0.0.1:8000)
gts server

# Start the web server on a different port
gts server --port 8081

# Pre-populate server with entities from files or directories
gts --path {PATH_TO_JSON_FILES} server

# Allow re-registering an entity with different content (default: reject with 409 Conflict)
gts server --allow-entity-updates

# Generate the OpenAPI specification
gts openapi-spec --out openapi.json

# Or fetch from running server
curl -s http://127.0.0.1:8000/openapi.json | jq > ./openapi.json
curl -s http://127.0.0.1:8000/openapi.json | jq | less -S
```

#### API Endpoints

##### Entity Management
- `GET /entities` - List registered entities (`limit` query parameter, 1–1000)
- `GET /entities/{gts_id}` - Retrieve a specific entity
- `POST /entities` - Register a single entity (optional `?validate=true`)
- `POST /entities/bulk` - Register multiple entities
- `POST /type-schemas` - Register a batch of GTS Type Schemas (each `type_id` derived from its embedded `$id`)

##### GTS Operations
- `GET /validate-id?gts_id=<id>` - Validate GTS ID (OP#1)
- `POST /extract-id` - Extract GTS ID from JSON (OP#2)
- `GET /parse-id?gts_id=<id>` - Parse GTS ID (OP#3)
- `GET /match-id-pattern?candidate=<id>&pattern=<pat>` - Match pattern (OP#4)
- `GET /uuid?gts_id=<id>` - Generate UUID (OP#5)
- `POST /validate-instance` - Validate registered instance (OP#6)
- `POST /validate-json` - Validate transient instance or Type Schema JSON without registering (OP#6)
- `POST /validate-json/{gts_type}` - Validate transient instance JSON against explicit type (OP#6)
- `GET /resolve-relationships?gts_id=<id>` - Resolve relationships and detect broken references (OP#7)
- `GET /compatibility?old_type_id=<id>&new_type_id=<id>` - Check Type Schema evolution compatibility (OP#8)
- `POST /cast` - Cast instance to target schema (OP#9)
- `GET /query?expr=<expr>&limit=<limit>` - Query entities (OP#10)
- `GET /attr?gts_with_path=<id@path>` - Retrieve attribute value (OP#11)
- `POST /validate-type-schema` - Validate derived Type Schema against base chain (OP#12)
- `POST /validate-entity` - Validate entity by GTS ID (OP#12/OP#13)

## Development

Common development tasks are wrapped in the `Makefile`:

```bash
make py-env      # create/update virtualenv and install dependencies
make install     # install gts in editable mode
make build       # build source and wheel distributions into dist/
make check       # run all quality checks (fmt, lint, mypy, test, e2e)
make test        # run unit tests
make coverage    # measure code coverage
make fmt         # check code formatting (ruff)
make dev-fmt     # auto-format code (ruff)
make lint        # run linter (ruff)
make mypy        # run type checker
make security    # check dependencies for vulnerabilities (pip-audit)
```

## Testing

You can test the gts-python library by utilizing the shared test suite from the [gts-spec](https://github.com/GlobalTypeSystem/gts-spec) specification and executing the tests against the web server.

With `make`, `e2e` starts the server and runs the conformance suite. By default it uses the `.gts-spec` submodule and requires it to match `.gts-spec-version`; `GTS_SPEC_DIR` selects another checkout (used as-is, e.g. an unreleased spec draft) and `E2E_PORT` the server port:

```bash
make e2e                                          # pinned submodule, port 8000
GTS_SPEC_DIR=../gts-spec E2E_PORT=18082 make e2e  # local gts-spec checkout
```

Changing `GTS_SPEC_DIR` reinstalls the selected checkout's test requirements in the shared virtual environment, including when switching back to a previous checkout.

Executing gts-spec Tests on the Server:

```bash
# getting the tests
git clone https://github.com/GlobalTypeSystem/gts-spec.git
cd gts-spec/tests

# run tests against the web server on port 8000 (default)
pytest

# override server URL using GTS_BASE_URL environment variable
GTS_BASE_URL=http://127.0.0.1:8001 pytest

# or set it persistently
export GTS_BASE_URL=http://127.0.0.1:8001
pytest
```

## License

Apache License 2.0
