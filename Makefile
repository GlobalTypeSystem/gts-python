CI := 1

# These recipes rely on POSIX tools (command -v, touch, rm -rf, sleep, kill,
# cat) and POSIX syntax (background jobs, inline env assignments). Require Bash
# explicitly so GNU Make does not fall back to cmd.exe via COMSPEC on Windows.
# On Windows, run these targets from a Bash environment (e.g. Git Bash / MSYS2).
SHELL := /bin/bash

# Python: PYTHON_BOOTSTRAP is used only to create the virtual environment;
# PYTHON is the venv interpreter used by all other targets.
PYTHON_BOOTSTRAP ?= $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python3)
PY_ENV_DIR ?= .venv
ifeq ($(OS),Windows_NT)
PY_ENV_PYTHON := $(PY_ENV_DIR)/Scripts/python.exe
else
PY_ENV_PYTHON := $(PY_ENV_DIR)/bin/python
endif
PYTHON ?= $(PY_ENV_PYTHON)
PY_ENV_STAMP := $(PY_ENV_DIR)/.stamp
INSTALL_STAMP := $(PY_ENV_DIR)/.install-stamp
LOCAL_DIST_DIR := dist-install-local

ifneq ($(filter install-local uninstall-local,$(MAKECMDGOALS)),)
ifeq ($(origin PYTHON),file)
$(error PYTHON must be set for local package targets (examples: venv: PYTHON=.venv/bin/python3.13 make install-local; global: PYTHON=python3.13 make install-local))
endif
endif

.PHONY: help py-env install build install-local uninstall-local clean dev-fmt all check fmt lint clippy mypy test security update-spec verify-spec-version e2e coverage gts-server

# Default target - show help
.DEFAULT_GOAL := help

# Show this help message
help:
	@awk '/^# / { desc=substr($$0, 3) } /^[a-zA-Z0-9_-]+:/ && desc { target=$$1; sub(/:$$/, "", target); printf "%-20s - %s\n", target, desc; desc="" }' Makefile | sort

# -------- Environment --------

# Create/update the virtual environment and install dev/test dependencies
py-env: $(PY_ENV_STAMP)

$(PY_ENV_PYTHON):
	$(PYTHON_BOOTSTRAP) -m venv --clear $(PY_ENV_DIR)

$(PY_ENV_STAMP): $(PY_ENV_PYTHON) gts/pyproject.toml .gts-spec/tests/requirements.txt requirements.txt Makefile
	@echo "Creating/updating Python virtual environment in $(PY_ENV_DIR)..."
	$(PYTHON_BOOTSTRAP) -m venv $(PY_ENV_DIR)
	$(PYTHON) -m pip install --upgrade pip
	# Spec test-client deps, then httprunner (--no-deps: its own pins are
	# incompatible with this venv), then local dev tooling + version overrides.
	$(PYTHON) -m pip install -r .gts-spec/tests/requirements.txt
	$(PYTHON) -m pip install --no-deps 'httprunner>=4,<5'
	$(PYTHON) -m pip install -r requirements.txt
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
install-local: $(if $(filter $(PY_ENV_PYTHON),$(PYTHON)),$(PY_ENV_PYTHON))
	@rm -rf $(LOCAL_DIST_DIR)
	$(PYTHON) -m pip install --upgrade build
	$(PYTHON) -m build --outdir $(LOCAL_DIST_DIR) ./gts
	$(PYTHON) -m pip install --force-reinstall $(LOCAL_DIST_DIR)/gts-*.whl

# Uninstall gts from the selected interpreter
uninstall-local:
	$(PYTHON) -m pip uninstall --yes gts
	@rm -f $(INSTALL_STAMP)

# Remove venv and build artifacts
clean:
	rm -rf $(PY_ENV_DIR) dist/ $(LOCAL_DIST_DIR) gts/dist/ gts/*.egg-info

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

# Format code and apply auto-fixable lint corrections
fix: py-env
	$(PYTHON) -m ruff format gts/src
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

PORT ?= 8000

gts-server: install
	$(PYTHON) -m gts server --host 127.0.0.1 --port $(PORT)

# Run end-to-end tests against gts-spec (pinned via .gts-spec-version)
e2e: install verify-spec-version
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

# Spec conformance suite is pinned in .gts-spec-version (format vMAJOR.MINOR.PATCH)
# so every checkout reproduces the same e2e run, mirroring the Rust reference.
GTS_SPEC_VERSION ?= $(shell cat .gts-spec-version 2>/dev/null)

# Check out the gts-spec submodule at the pinned .gts-spec-version tag
update-spec:
	git submodule update --init .gts-spec
	git -C .gts-spec fetch --tags --quiet origin
	git -C .gts-spec checkout --quiet "$(GTS_SPEC_VERSION)"
	@echo "gts-spec pinned to $(GTS_SPEC_VERSION)"

# Fail if the checked-out gts-spec submodule does not match the pinned version
verify-spec-version:
	@current="$$(git -C .gts-spec describe --tags 2>/dev/null)"; \
	if [ "$$current" != "$(GTS_SPEC_VERSION)" ]; then \
		echo "gts-spec is at '$$current' but .gts-spec-version pins '$(GTS_SPEC_VERSION)'"; \
		echo "run 'make update-spec' to sync"; \
		exit 1; \
	fi; \
	echo "gts-spec matches pinned $(GTS_SPEC_VERSION)"

# Run all checks and build
all: check build

# Run all quality checks
check: fmt lint test e2e
