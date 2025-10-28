> Status: initial draft v0.1, not for production use

# GTS Python Library

A minimal, idiomatic Python library for working with **GTS** ([Global Type System](https://github.com/gts-spec/gts-spec)) identifiers and JSON/JSON Schema artifacts.

## Roadmap

Featureset:

- [x] **OP#1 - ID Validation**: Verify identifier syntax using regex patterns
- [x] **OP#2 - ID Extraction**: Fetch identifiers from JSON objects or JSON Schema documents
- [x] **OP#3 - ID Parsing**: Decompose identifiers into constituent parts (vendor, package, namespace, type, version, etc.)
- [x] **OP#4 - ID Pattern Matching**: Match identifiers against patterns containing wildcards
- [x] **OP#5 - ID to UUID Mapping**: Generate deterministic UUIDs from GTS identifiers
- [x] **OP#6 - Schema Validation**: Validate object instances against their corresponding schemas
- [x] **OP#7 - Relationship Resolution**: Load all schemas and instances, resolve inter-dependencies, and detect broken references
- [ ] **OP#8 - Compatibility Checking**: Verify that schemas with different MINOR versions are compatible
- [ ] **OP#8.1 - Backward compatibility checking**
- [ ] **OP#8.2 - Forward compatibility checking**
- [ ] **OP#8.3 - Full compatibility checking**
- [x] **OP#9 - Version Casting**: Transform instances between compatible MINOR versions
- [ ] **OP#10 - Query Execution**: Filter identifier collections using the GTS query language
- [x] **OP#11 - Attribute Access**: Retrieve property values and metadata using the attribute selector (`@`)

See details in [gts/README.md](gts/README.md)

Other features:

- [ ] **Web server** - a non-production web-server with REST API for the operations processing and testing
- [ ] **Tests** - re-usable tests suite implemented as REST API request and response validation

## Installation

```bash
# install in editable mode
pip install -e ./gts

# install from PyPI, not supported yet
# pip install gts
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

The web server is a non-production web-server with REST API for the operations processing and testing.


```bash
# start the web server
gts serve

# Generate the OpenAPI schema
curl -s http://127.0.0.1:8000/openapi.json -o ./openapi.json

# See the schema
curl -s http://127.0.0.1:8000/openapi.json | jq | less -S
```

## License

Apache License 2.0
