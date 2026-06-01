# Grove — single source of truth for local dev tasks.
#
# All targets shell out to `uv` so the venv is implicit. CI workflows call
# the same targets, so what works locally works on every push.
#
# Canonical install for end users is `uvx grove` (see README); this Makefile
# is for contributors only.

UV ?= uv
PROJECT := grove
DIST := dist
BUILD := build

.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show this help message
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ─── environment ────────────────────────────────────────────────────────────

.PHONY: sync install dev
sync:  ## Install runtime + dev deps via uv (alias: install, dev)
	$(UV) sync --all-groups
install: sync
dev: sync

# ─── quality gates ──────────────────────────────────────────────────────────

.PHONY: lint format type contracts check
lint:  ## Run ruff + mypy + import-linter
	$(UV) run ruff check src tests
	$(UV) run ruff format --check src tests
	$(UV) run mypy src/$(PROJECT)
	$(UV) run lint-imports

format:  ## Auto-fix lint + reformat
	$(UV) run ruff check --fix src tests
	$(UV) run ruff format src tests

type:  ## Run mypy --strict only
	$(UV) run mypy src/$(PROJECT)

contracts:  ## Run import-linter only
	$(UV) run lint-imports

check: lint test  ## Run lint + unit tests (no integration)

# ─── docs ───────────────────────────────────────────────────────────────────

.PHONY: docs-schema docs-screenshots docs docs-build
docs-schema:  ## Regenerate docs/grove.schema.json from the Pydantic model
	$(UV) run grove config schema --stdout > docs/grove.schema.json

docs-screenshots:  ## Regenerate docs/img/screenshots/*.svg from the live TUI
	$(UV) run python -m tools.screenshots.capture

docs: docs-schema  ## Serve the docs site locally (live reload)
	$(UV) run --group docs mkdocs serve

docs-build: docs-schema  ## Build the docs site (CI parity, --strict)
	$(UV) run --group docs mkdocs build --strict

# ─── tests ──────────────────────────────────────────────────────────────────

.PHONY: test integration test-all
test:  ## Run unit + Pilot tests (excludes integration)
	$(UV) run pytest -m "not integration"

integration:  ## Run real-tmux + real-git integration tests
	$(UV) run pytest -m integration

test-all: test integration  ## Run every test we have

# ─── build artifacts ────────────────────────────────────────────────────────

.PHONY: build clean uvx-smoke
build:  ## Build sdist + wheel into $(DIST)/  (PyPI-ready)
	$(UV) build

uvx-smoke:  ## Verify the package works under uvx from the local checkout
	$(UV) tool run --from . --no-cache grove version

clean:  ## Remove build artifacts and caches
	rm -rf $(DIST) $(BUILD) *.egg-info
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov

# ─── release dry-run ────────────────────────────────────────────────────────

.PHONY: release-check
release-check: lint test build uvx-smoke  ## Full pre-release gauntlet
	@echo
	@echo "release-check OK — wheel + sdist in $(DIST)/"

# ─── webapp (Next.js read-only dashboard) ───────────────────────────────────
#
# webapp/ is a Node project with its own package.json. These targets are
# convenience wrappers — see webapp/CLAUDE.md for the full engineering
# contract. Default GROVE_DAEMON_URL points at the daemon defaults below.

WEBAPP_DIR ?= $(CURDIR)/webapp

.PHONY: webapp-install webapp-build webapp-dev webapp-test
webapp-install:  ## Install webapp Node deps (npm ci)
	cd $(WEBAPP_DIR) && npm ci

webapp-build: webapp-install  ## Build webapp for production (required before `systemd` w/ webapp)
	cd $(WEBAPP_DIR) && npm run build

webapp-dev:  ## Run webapp dev server (LAN-reachable on :3000)
	cd $(WEBAPP_DIR) && npm run dev

webapp-test:  ## Run webapp unit + component tests
	cd $(WEBAPP_DIR) && npm test

# ─── systemd (Linux user-scope) ─────────────────────────────────────────────
#
# Installs ~/.config/systemd/user/grove-daemon.service (always) and
# grove-webapp.service (only when WITH_WEBAPP=1). Templates live in
# packaging/systemd/*.service.in; @PLACEHOLDER@ tokens are substituted
# by sed at install time.
#
# Quick reference:
#   make systemd                    # daemon only
#   WITH_WEBAPP=1 make systemd      # daemon + webapp
#   make systemd-enable             # daemon-reload + enable --now (matches WITH_WEBAPP)
#   make systemd-disable            # stop + disable
#   make systemd-uninstall          # remove unit files
#   make systemd-status             # systemctl --user status
#
# See packaging/systemd/README.md for the full reference.

SYSTEMD_USER_DIR ?= $(HOME)/.config/systemd/user
SYSTEMD_TEMPLATE_DIR := $(CURDIR)/packaging/systemd

# Auto-detect grove + npm binaries; user can override for nvm/asdf setups.
GROVE_BIN ?= $(shell command -v grove 2>/dev/null)
NPM_BIN   ?= $(shell command -v npm 2>/dev/null)
NODE_BIN_DIR := $(if $(NPM_BIN),$(dir $(NPM_BIN)),)

DAEMON_HOST ?= 127.0.0.1
DAEMON_PORT ?= 7421
WEBAPP_HOST ?= 0.0.0.0
WEBAPP_PORT ?= 3000
DAEMON_URL  := http://$(DAEMON_HOST):$(DAEMON_PORT)

# WITH_WEBAPP=1 to also install/enable/disable the webapp unit.
WITH_WEBAPP ?=

# Internal: list the webapp unit basename only when WITH_WEBAPP=1.
_WEBAPP_UNIT_NAME := $(if $(WITH_WEBAPP),grove-webapp.service,)
_UNITS := grove-daemon.service $(_WEBAPP_UNIT_NAME)

# When WITH_WEBAPP is set, `systemd` depends on the webapp install recipe too.
# This is a Make-level (not shell) conditional so the per-unit recipes stay
# single-purpose and don't hide control flow inside shell heredocs.
_WEBAPP_INSTALL_DEP := $(if $(WITH_WEBAPP),_systemd-install-webapp,)

.PHONY: systemd systemd-enable systemd-disable systemd-uninstall systemd-status systemd-print \
        _systemd-precheck _systemd-install-daemon _systemd-install-webapp

# Precondition: required binaries discoverable. Run before any install/enable.
_systemd-precheck:
	@if [ -z "$(GROVE_BIN)" ]; then \
	  echo "✗ grove binary not on PATH. Install first: 'curl -fsSL https://raw.githubusercontent.com/bearlike/Grove/main/install.sh | bash'" >&2; \
	  exit 1; \
	fi
	@echo "✓ grove: $(GROVE_BIN)"
	@if [ -n "$(WITH_WEBAPP)" ]; then \
	  if [ -z "$(NPM_BIN)" ]; then \
	    echo "✗ npm not on PATH (required for WITH_WEBAPP=1)." >&2; exit 1; \
	  fi; \
	  if [ ! -d "$(WEBAPP_DIR)" ]; then \
	    echo "✗ webapp dir not found at $(WEBAPP_DIR) — set WEBAPP_DIR=<path>." >&2; exit 1; \
	  fi; \
	  if [ ! -d "$(WEBAPP_DIR)/.next" ]; then \
	    echo "⚠  $(WEBAPP_DIR)/.next not found — run 'make webapp-build' before 'make systemd-enable'."; \
	  fi; \
	  echo "✓ npm: $(NPM_BIN)"; \
	  echo "✓ webapp dir: $(WEBAPP_DIR)"; \
	fi

# Common sed substitution applied to a template. Uses ',' as the delimiter
# so file paths don't need escaping.
_SED_SUBST := sed \
	-e 's,@GROVE_BIN@,$(GROVE_BIN),g' \
	-e 's,@DAEMON_HOST@,$(DAEMON_HOST),g' \
	-e 's,@DAEMON_PORT@,$(DAEMON_PORT),g' \
	-e 's,@WEBAPP_DIR@,$(WEBAPP_DIR),g' \
	-e 's,@NPM_BIN@,$(NPM_BIN),g' \
	-e 's,@NODE_BIN_DIR@,$(NODE_BIN_DIR:/=),g' \
	-e 's,@WEBAPP_HOST@,$(WEBAPP_HOST),g' \
	-e 's,@WEBAPP_PORT@,$(WEBAPP_PORT),g' \
	-e 's,@DAEMON_URL@,$(DAEMON_URL),g'

_systemd-install-daemon: _systemd-precheck
	@mkdir -p "$(SYSTEMD_USER_DIR)"
	@$(_SED_SUBST) "$(SYSTEMD_TEMPLATE_DIR)/grove-daemon.service.in" > "$(SYSTEMD_USER_DIR)/grove-daemon.service.tmp"
	@mv -f "$(SYSTEMD_USER_DIR)/grove-daemon.service.tmp" "$(SYSTEMD_USER_DIR)/grove-daemon.service"
	@echo "✓ wrote $(SYSTEMD_USER_DIR)/grove-daemon.service"

_systemd-install-webapp: _systemd-precheck
	@mkdir -p "$(SYSTEMD_USER_DIR)"
	@$(_SED_SUBST) "$(SYSTEMD_TEMPLATE_DIR)/grove-webapp.service.in" > "$(SYSTEMD_USER_DIR)/grove-webapp.service.tmp"
	@mv -f "$(SYSTEMD_USER_DIR)/grove-webapp.service.tmp" "$(SYSTEMD_USER_DIR)/grove-webapp.service"
	@echo "✓ wrote $(SYSTEMD_USER_DIR)/grove-webapp.service"

systemd: _systemd-install-daemon $(_WEBAPP_INSTALL_DEP)  ## Install user systemd units (WITH_WEBAPP=1 to also install webapp)
	systemctl --user daemon-reload
	@echo
	@echo "next: 'make systemd-enable'$(if $(WITH_WEBAPP), (will enable both),)"

systemd-print: _systemd-precheck  ## Print the rendered unit file(s) without writing
	@echo "─── grove-daemon.service ───"
	@$(_SED_SUBST) "$(SYSTEMD_TEMPLATE_DIR)/grove-daemon.service.in"
	@if [ -n "$(WITH_WEBAPP)" ]; then \
	  echo; echo "─── grove-webapp.service ───"; \
	  $(_SED_SUBST) "$(SYSTEMD_TEMPLATE_DIR)/grove-webapp.service.in"; \
	fi

systemd-enable: systemd  ## Enable + start now (matches WITH_WEBAPP scope)
	systemctl --user enable --now $(_UNITS)
	@echo
	@for u in $(_UNITS); do systemctl --user --no-pager status $$u | head -3; done

systemd-disable:  ## Stop + disable user units (matches WITH_WEBAPP scope)
	-systemctl --user disable --now $(_UNITS)

systemd-uninstall: systemd-disable  ## Stop, disable, and remove unit files
	rm -f $(SYSTEMD_USER_DIR)/grove-daemon.service
	@if [ -n "$(WITH_WEBAPP)" ]; then rm -f $(SYSTEMD_USER_DIR)/grove-webapp.service; fi
	systemctl --user daemon-reload
	@echo "✓ removed unit files"

systemd-status:  ## Show status of installed grove user units
	@for u in $(_UNITS); do \
	  echo "─── $$u ───"; \
	  systemctl --user --no-pager status $$u 2>&1 | head -10 || true; \
	  echo; \
	done
