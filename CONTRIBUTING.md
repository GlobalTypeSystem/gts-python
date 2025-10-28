# Contributing to GTS Specification

Thank you for your interest in contributing to the Global Type System (GTS) Specification! This document provides guidelines and information for contributors.

## Quick Start

### Prerequisites

- **Git** for version control
- **Python 3.8+** (optional, for running reference implementations)

### Development Setup

```bash
# Clone the repository
git clone <repository-url>
cd gts-python

# Optional: Install Python dependencies for reference implementations
pip install jsonschema
```

### Repository Layout

```
gts-python/
- README.md                 # Main specification document
- CONTRIBUTING.md           # This file
- LICENSE                   # License information
- gts/                 # Example schemas and instances
    ├── events/               # Event-related examples
    │   ├── schemas/          # JSON Schema definitions
    │   └── instances/        # JSON instance examples
    └── ...                   # Other domain examples
```

## Development Workflow

### 1. Create a Feature Branch or fork the repository

```bash
git checkout -b feature/your-feature-name
```

Use descriptive branch names:
- `feature/add-event-examples`
- `fix/schema-validation-error`
- `docs/clarify-chaining-rules`
- `spec/minor-version-compatibility`

### 2. Make Your Changes

Follow the specification standards and patterns described below.

### 3. Validate Your Changes

TODO

### 4. Commit Changes

Follow a structured commit message format:

```text
<type>(<module>): <description>
```

- `<type>`: change category (see table below)
- `<module>` (optional): the area touched (e.g., spec, examples, schemas)
- `<description>`: concise, imperative summary

Accepted commit types:

| Type       | Meaning                                                     |
|------------|-------------------------------------------------------------|
| feat       | New feature                                                 |
| fix        | Bug fixes in schemas or examples                            |
| docs       | Documentation updates                                       |
| examples   | Adding or updating example schemas/instances                |
| test       | Adding or modifying validation tests                        |
| style      | Formatting changes (whitespace, JSON formatting, etc.)      |
| chore      | Misc tasks (tooling, scripts)                               |
| breaking   | Backward incompatible specification changes                 |

Best practices:

- Keep the title concise (ideally <50 chars)
- Use imperative mood (e.g., "Fix bug", not "Fixed bug")
- Make commits atomic (one logical change per commit)
- Add details in the body when necessary (what/why, not how)
- For breaking changes, either use `spec!:` or include a `BREAKING CHANGE:` footer
