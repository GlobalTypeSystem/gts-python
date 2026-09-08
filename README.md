> Status: initial draft v0.1, not for production use

# GTS Python Library

A minimal, idiomatic Python library for working with **GTS** ([Global Type System](https://github.com/gts-spec/gts-spec)) identifiers and JSON/JSON Schema artifacts.

Current supported GTS spec version: `0.13.1`

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

Other GTS spec [Reference Implementation](https://github.com/globaltypesystem/gts-spec/blob/main/README.md#9-reference-implementation-recommendations) recommended features support:

- [x] **In-memory entities registry** - simple GTS entities registry with optional GTS references validation on entity registration
- [x] **CLI** - command-line interface for all GTS operations
- [x] **Web server** - a non-production web-server with REST API for the operations processing and testing
- [x] **x-gts-ref support** - to support special GTS entity reference annotation in schemas
- [x] **YAML support** - to support YAML files (*.yml, *.yaml) as input files
- [x] **UUID for instances** - to support UUID as ID in JSON instances
- [ ] **TypeSpec support** - direct support for [typespec.io](https://typespec.io/) files (*.tsp) input files

Technical Backlog:

- [x] **Code coverage** - target is 90%
- [x] **Documentation** - add documentation for all the features
- [x] **Interface** - export publicly available interface and keep cli and others private
- [x] **Server API** - finalise the server API
- [x] **Final code cleanup** - remove unused code, denormalize, add critical comments, etc.

## Installation

GTS requires Python 3.9 or later.

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
gts <command> <args>

# see available commands
gts --help
```

### Library

See [gts/README.md](gts/README.md)

### Web server

The web server is a non-production web-server with REST API for the operations processing and testing. It implements reference API for gts-spec [tests](https://github.com/GlobalTypeSystem/gts-spec/tree/main/tests)


```bash
# start the web server, default location is http://127.0.0.1:8000
gts serve

# start the web server on different port
gts server --port 8081

# pre-populate server with the JSON instancens and schemas from the gts-spec tests
gts server --path {PATH_TO_JSON_FILES}

# Generate the OpenAPI schema
curl -s http://127.0.0.1:8000/openapi.json | jq > ./gts/openapi.json

# See the schema
curl -s http://127.0.0.1:8000/openapi.json | jq | less -S
```

### Testing

You can test the gts-python library by utilizing the shared test suite from the [gts-spec](https://github.com/GlobalTypeSystem/gts-spec) specification and executing the tests against the web server.

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
