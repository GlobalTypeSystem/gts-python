CI := 1

# Python: PYTHON_BOOTSTRAP is used only to create the virtual environment;
# PYTHON is the venv interpreter used by all other targets.
PYTHON_BOOTSTRAP ?= $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python3)
PY_ENV_DIR ?= .venv
ifeq ($(OS),Windows_NT)
PYTHON ?= $(PY_ENV_DIR)/Scripts/python
else
PYTHON ?= $(PY_ENV_DIR)/bin/python
endif
PY_ENV_STAMP := $(PY_ENV_DIR)/.stamp
INSTALL_STAMP := $(PY_ENV_DIR)/.install-stamp

ifneq ($(filter install-local uninstall-local,$(MAKECMDGOALS)),)
ifeq ($(origin PYTHON),file)
$(error PYTHON must be set for local package targets (examples: venv: PYTHON=.venv/bin/python3.13 make install-local; global: PYTHON=python3.13 make install-local))
endif
endif

.PHONY: help py-env install build install-local uninstall-local clean dev-fmt all check fmt lint clippy mypy test security update-spec e2e coverage

# Default target - show help
.DEFAULT_GOAL := help

# Show this help message
help:
	@awk '/^# / { desc=substr($$0, 3) } /^[a-zA-Z0-9_-]+:/ && desc { target=$$1; sub(/:$$/, "", target); printf "%-20s - %s\n", target, desc; desc="" }' Makefile | sort

# -------- Environment --------

# Create/update the virtual environment and install dev/test dependencies
py-env: $(PY_ENV_STAMP)

$(PY_ENV_STAMP): gts/pyproject.toml .gts-spec/tests/requirements.txt
	@echo "Creating/updating Python virtual environment in $(PY_ENV_DIR)..."
	$(PYTHON_BOOTSTRAP) -m venv $(PY_ENV_DIR)
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r .gts-spec/tests/requirements.txt
	$(PYTHON) -m pip install --no-deps 'httprunner>=4,<5'
	$(PYTHON) -m pip install ruff mypy
	@touch $@

# Install gts package into the venv (editable, for development)
install: $(INSTALL_STAMP)

$(INSTALL_STAMP): $(PY_ENV_STAMP) gts/pyproject.toml
	$(PYTHON) -m pip install -e ./gts
	@touch $@

# Build source and wheel distributions into dist/
build: py-env
	$(PYTHON) -m pip install --upgrade build
	$(PYTHON) -m build --outdir dist ./gts

# Install the locally built wheel, equivalent to installing the published gts package
install-local: build
	$(PYTHON) -m pip install --force-reinstall dist/gts-*.whl

# Uninstall gts from the selected interpreter
uninstall-local:
	$(PYTHON) -m pip uninstall --yes gts
	@rm -f $(INSTALL_STAMP)

# Remove venv and build artifacts
clean:
	rm -rf $(PY_ENV_DIR) dist/ gts/dist/ gts/*.egg-info

# -------- Code quality --------

# Fix formatting issues
dev-fmt: py-env
	$(PYTHON) -m ruff format gts/src

# Check code formatting
fmt: py-env
	$(PYTHON) -m ruff format --check gts/src

# Run linter (ruff)
lint: py-env
	$(PYTHON) -m ruff check gts/src

# Run clippy-equivalent linter with auto-fix
clippy: py-env
	$(PYTHON) -m ruff check --fix gts/src

# Run type checker
mypy: py-env
	$(PYTHON) -m mypy gts/src/gts --ignore-missing-imports

# -------- Tests --------

# Run all tests
test: install
	$(PYTHON) -m pytest tests/ -v

# Measure code coverage
coverage: install
	$(PYTHON) -m pip install 'pytest-cov>=5,<7'
	$(PYTHON) -m pytest tests/ --cov=gts --cov-report=xml --cov-report=term

# Run end-to-end tests against gts-spec
e2e: install
	@echo "Starting server in background..."
	@$(PYTHON) -m gts server --port 8000 & echo $$! > .server.pid
	@sleep 2
	@echo "Running e2e tests..."
	@PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m pytest -p no:cacheprovider --log-file=e2e.log ./.gts-spec/tests || (kill `cat .server.pid` 2>/dev/null; rm -f .server.pid; exit 1)
	@echo "Stopping server..."
	@kill `cat .server.pid` 2>/dev/null || true
	@rm -f .server.pid
	@echo "E2E tests completed successfully"

# -------- Misc --------

# Check dependencies for security vulnerabilities
security: py-env
	$(PYTHON) -m pip install pip-audit
	$(PYTHON) -m pip_audit

# Update gts-spec submodule to latest
update-spec:
	git submodule update --remote .gts-spec

# Run all checks and build
all: check build

# Run all quality checks
check: fmt lint test e2e
