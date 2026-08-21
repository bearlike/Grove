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

.PHONY: docs-schema docs-screenshots docs-webapp-screenshots docs-frame-webapp-screenshots docs-tour-gif docs-mockups docs-images docs docs-build
docs-schema:  ## Regenerate docs/grove.schema.json from the Pydantic model
	$(UV) run grove config schema --stdout > docs/grove.schema.json

# Captures as SVG, then rasterizes and frames in one process — the SVG is a
# sandbox intermediate and never reaches docs/. Needs `node` plus the webapp's
# Playwright install for the raster step; `capture.py` preflights both before it
# plants a fleet.
docs-screenshots:  ## Regenerate the framed TUI PNG screenshots from the live TUI
	$(UV) run --group dev python -m tools.screenshots.capture

# The desktop shots, and only those. Framing is not idempotent — a second
# pass frames the frame — so the set is named rather than globbed, and it is
# chained onto the capture that produces it so the two cannot drift apart.
# `webapp-home-mobile.png` is deliberately absent: it feeds the phone mockup,
# which supplies its own device shell.
FRAMED_SHOTS := $(addprefix docs/img/screenshots/,webapp-home.png webapp-composer.png webapp-sessions.png webapp-workspace.png webapp-usage.png webapp-usage-detail.png webapp-pair-device.png webapp-pair-code.png)

docs-webapp-screenshots: webapp-build  ## Regenerate the web dashboard PNG screenshots (needs webapp/.next)
	$(UV) run python -m tools.screenshots.webapp_capture
	$(MAKE) docs-frame-webapp-screenshots
	$(MAKE) docs-tour-gif

docs-frame-webapp-screenshots:  ## Composite the webapp screenshots onto a consistent 16:9 framed window
	$(UV) run --group dev python -m tools.screenshots.frame $(FRAMED_SHOTS)

# Chained onto the framing above, never run standalone against unframed shots:
# the tour's whole compression story is that every slide shares one wallpaper.
docs-tour-gif:  ## Build the looping web dashboard tour GIF from the framed shots
	$(UV) run --group dev python -m tools.screenshots.slideshow

docs-mockups:  ## Composite the landing-page device mockups from the latest screenshots
	$(UV) run python -m tools.screenshots.mockups

docs-images: docs-screenshots docs-webapp-screenshots docs-mockups  ## Regenerate every doc image (TUI + web + mockups)

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

.PHONY: build clean uvx-smoke install-smoke
build:  ## Build sdist + wheel into $(DIST)/  (PyPI-ready)
	$(UV) build

uvx-smoke:  ## Verify the package works under uvx from the local checkout
	$(UV) tool run --from . --no-cache grove version

install-smoke:  ## Clean-install + first-run smoke test in a fresh Ubuntu container (needs docker)
	docker build -t grove-install-smoke packaging/docker
	docker run --rm -v "$(CURDIR):/src:ro" -e GROVE_INSTALL_SPEC grove-install-smoke bash /src/packaging/docker/smoke.sh

clean:  ## Remove build artifacts and caches
	rm -rf $(DIST) $(BUILD) *.egg-info
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov

# ─── release dry-run ────────────────────────────────────────────────────────

.PHONY: release-check
release-check: lint test build uvx-smoke  ## Full pre-release gauntlet
	@echo
	@echo "release-check OK — wheel + sdist in $(DIST)/"

# ─── webapp (assistant-ui-native front end) ─────────────────────────────────
#
# webapp/ is a Node project with its own package.json. These targets are
# convenience wrappers — see webapp/CLAUDE.md for the full engineering
# contract. Default GROVE_DAEMON_URL points at the daemon defaults below.
# It needs Node >= 22 (Next 16), which is often newer than the shell default —
# hence its own WEBAPP_NPM_BIN rather than assuming the shell's npm resolves
# to a new enough Node.

WEBAPP_DIR ?= $(CURDIR)/webapp
WEBAPP_NPM_BIN ?= $(NPM_BIN)
WEBAPP_NODE_BIN_DIR := $(if $(WEBAPP_NPM_BIN),$(dir $(WEBAPP_NPM_BIN)),)

.PHONY: webapp-install webapp-build webapp-dev webapp-test webapp-gate
webapp-install:  ## Install webapp Node deps (npm ci)
	cd $(WEBAPP_DIR) && $(WEBAPP_NPM_BIN) ci

webapp-build: webapp-install  ## Build webapp for production (required before `systemd` w/ webapp)
	cd $(WEBAPP_DIR) && $(WEBAPP_NPM_BIN) run build

webapp-dev:  ## Run webapp dev server (LAN-reachable on :3000)
	cd $(WEBAPP_DIR) && $(WEBAPP_NPM_BIN) run dev

webapp-test:  ## Run webapp unit + component tests
	cd $(WEBAPP_DIR) && $(WEBAPP_NPM_BIN) test

webapp-gate:  ## Run webapp's full gate (typecheck, registry drift, styling, tests)
	cd $(WEBAPP_DIR) && $(WEBAPP_NPM_BIN) run gate

# ─── systemd (Linux user-scope) ─────────────────────────────────────────────
#
# Installs ~/.config/systemd/user/grove-daemon.service (always),
# grove-webapp.service (only when WITH_WEBAPP=1), and grove-mcp.service
# (only when WITH_MCP=1). Templates live in packaging/systemd/*.service.in;
# @PLACEHOLDER@ tokens are substituted by sed at install time.
#
# Quick reference:
#   make systemd                    # daemon only
#   WITH_WEBAPP=1 make systemd      # daemon + webapp
#   WITH_MCP=1 make systemd         # daemon + MCP over Streamable HTTP
#   make systemd-enable             # daemon-reload + enable --now (matches WITH_WEBAPP)
#   make systemd-disable            # stop + disable
#   make systemd-uninstall          # remove unit files
#   make systemd-status             # systemctl --user status
#
# See packaging/systemd/README.md for the full reference.

SYSTEMD_USER_DIR ?= $(HOME)/.config/systemd/user
SYSTEMD_TEMPLATE_DIR := $(CURDIR)/packaging/systemd

# Auto-detect grove + npm binaries; user can override for nvm/asdf setups.
# `grove-mcp` is a separate console script from `grove`, so it gets its own
# lookup — an install with the lean `[daemon]` extra ships one and not the other.
GROVE_BIN ?= $(shell command -v grove 2>/dev/null)
MCP_BIN   ?= $(shell command -v grove-mcp 2>/dev/null)
NPM_BIN   ?= $(shell command -v npm 2>/dev/null)
NODE_BIN_DIR := $(if $(NPM_BIN),$(dir $(NPM_BIN)),)

# PATH baked into the daemon unit. Defaults to the invoking shell's PATH so
# pyenv/nvm/asdf-managed toolchains stay visible to init scripts under
# systemd --user, which otherwise provides a bare PATH (issue #9). Snapshot
# semantics: re-run `make systemd` after toolchain moves.
DAEMON_PATH ?= $(PATH)

DAEMON_HOST ?= 127.0.0.1
DAEMON_PORT ?= 7421
WEBAPP_HOST ?= 0.0.0.0
WEBAPP_PORT ?= 3000
# Loopback by default, deliberately unlike WEBAPP_HOST: the MCP surface grants
# workspace lifecycle control (create/kill/message), so exposing it off-host is
# an explicit operator decision, taken behind a tunnel/VPN/TLS proxy.
MCP_HOST    ?= 127.0.0.1
MCP_PORT    ?= 7431
DAEMON_URL  := http://$(DAEMON_HOST):$(DAEMON_PORT)

# WITH_WEBAPP=1 to also install/enable/disable the webapp unit.
WITH_WEBAPP ?=
# WITH_MCP=1 to also install/enable/disable the MCP (Streamable HTTP) unit.
WITH_MCP ?=

# Internal: list a companion unit basename only when its gate is set.
_WEBAPP_UNIT_NAME := $(if $(WITH_WEBAPP),grove-webapp.service,)
_MCP_UNIT_NAME := $(if $(WITH_MCP),grove-mcp.service,)
_UNITS := grove-daemon.service $(_WEBAPP_UNIT_NAME) $(_MCP_UNIT_NAME)

# When a gate is set, `systemd` depends on that unit's install recipe too.
# These are Make-level (not shell) conditionals so the per-unit recipes stay
# single-purpose and don't hide control flow inside shell heredocs.
_WEBAPP_INSTALL_DEP := $(if $(WITH_WEBAPP),_systemd-install-webapp,)
_MCP_INSTALL_DEP := $(if $(WITH_MCP),_systemd-install-mcp,)

.PHONY: systemd systemd-enable systemd-disable systemd-uninstall systemd-status systemd-print \
        _systemd-precheck _systemd-install-daemon _systemd-install-webapp _systemd-install-mcp

# Precondition: required binaries discoverable. Run before any install/enable.
_systemd-precheck:
	@if [ -z "$(GROVE_BIN)" ]; then \
	  echo "✗ grove binary not on PATH. Install first: 'curl -fsSL https://raw.githubusercontent.com/bearlike/Grove/current/install.sh | bash'" >&2; \
	  exit 1; \
	fi
	@echo "✓ grove: $(GROVE_BIN)"
	@if [ -n "$(WITH_WEBAPP)" ]; then \
	  if [ -z "$(WEBAPP_NPM_BIN)" ]; then \
	    echo "✗ npm not on PATH (required for WITH_WEBAPP=1)." >&2; exit 1; \
	  fi; \
	  if [ ! -d "$(WEBAPP_DIR)" ]; then \
	    echo "✗ webapp dir not found at $(WEBAPP_DIR) — set WEBAPP_DIR=<path>." >&2; exit 1; \
	  fi; \
	  if [ ! -d "$(WEBAPP_DIR)/.next" ]; then \
	    echo "⚠  $(WEBAPP_DIR)/.next not found — run 'make webapp-build' before 'make systemd-enable'."; \
	  fi; \
	  major=$$("$(WEBAPP_NODE_BIN_DIR)node" -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo ""); \
	  if [ -z "$$major" ]; then \
	    echo "⚠  could not probe Node next to $(WEBAPP_NPM_BIN) — webapp needs Node >= 22 (Next 16)."; \
	  elif [ "$$major" -lt 22 ]; then \
	    echo "✗ webapp needs Node >= 22 (Next 16); $(WEBAPP_NPM_BIN) is Node $$major. Set WEBAPP_NPM_BIN=<path to a Node 22+ npm>." >&2; exit 1; \
	  fi; \
	  echo "✓ npm: $(WEBAPP_NPM_BIN)"; \
	  echo "✓ webapp dir: $(WEBAPP_DIR)"; \
	fi
	@if [ -n "$(WITH_MCP)" ]; then \
	  if [ -z "$(MCP_BIN)" ]; then \
	    echo "✗ grove-mcp not on PATH (required for WITH_MCP=1). Reinstall with the mcp extra: 'uv tool install --reinstall --force --editable .[all]'" >&2; exit 1; \
	  fi; \
	  if [ ! -f "$(HOME)/.config/grove/mcp.env" ]; then \
	    echo "⚠  $(HOME)/.config/grove/mcp.env not found — create it (mode 0600) with GROVE_MCP_TOKEN=<secret>; the service exits 2 without it."; \
	  fi; \
	  echo "✓ grove-mcp: $(MCP_BIN)"; \
	fi

# Common sed substitution applied to a template. Uses ',' as the delimiter
# so file paths don't need escaping.
_SED_SUBST := sed \
	-e 's,@GROVE_BIN@,$(GROVE_BIN),g' \
	-e 's,@DAEMON_PATH@,$(DAEMON_PATH),g' \
	-e 's,@DAEMON_HOST@,$(DAEMON_HOST),g' \
	-e 's,@DAEMON_PORT@,$(DAEMON_PORT),g' \
	-e 's,@WEBAPP_DIR@,$(WEBAPP_DIR),g' \
	-e 's,@NPM_BIN@,$(WEBAPP_NPM_BIN),g' \
	-e 's,@NODE_BIN_DIR@,$(WEBAPP_NODE_BIN_DIR:/=),g' \
	-e 's,@WEBAPP_HOST@,$(WEBAPP_HOST),g' \
	-e 's,@WEBAPP_PORT@,$(WEBAPP_PORT),g' \
	-e 's,@MCP_BIN@,$(MCP_BIN),g' \
	-e 's,@MCP_HOST@,$(MCP_HOST),g' \
	-e 's,@MCP_PORT@,$(MCP_PORT),g' \
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

_systemd-install-mcp: _systemd-precheck
	@mkdir -p "$(SYSTEMD_USER_DIR)"
	@$(_SED_SUBST) "$(SYSTEMD_TEMPLATE_DIR)/grove-mcp.service.in" > "$(SYSTEMD_USER_DIR)/grove-mcp.service.tmp"
	@mv -f "$(SYSTEMD_USER_DIR)/grove-mcp.service.tmp" "$(SYSTEMD_USER_DIR)/grove-mcp.service"
	@echo "✓ wrote $(SYSTEMD_USER_DIR)/grove-mcp.service"

systemd: _systemd-install-daemon $(_WEBAPP_INSTALL_DEP) $(_MCP_INSTALL_DEP)  ## Install user systemd units (WITH_WEBAPP=1 / WITH_MCP=1 to also install those)
	systemctl --user daemon-reload
	@echo
	@echo "next: 'make systemd-enable'$(if $(_WEBAPP_UNIT_NAME)$(_MCP_UNIT_NAME), (will enable all installed units),)"

systemd-print: _systemd-precheck  ## Print the rendered unit file(s) without writing
	@echo "─── grove-daemon.service ───"
	@$(_SED_SUBST) "$(SYSTEMD_TEMPLATE_DIR)/grove-daemon.service.in"
	@if [ -n "$(WITH_WEBAPP)" ]; then \
	  echo; echo "─── grove-webapp.service ───"; \
	  $(_SED_SUBST) "$(SYSTEMD_TEMPLATE_DIR)/grove-webapp.service.in"; \
	fi
	@if [ -n "$(WITH_MCP)" ]; then \
	  echo; echo "─── grove-mcp.service ───"; \
	  $(_SED_SUBST) "$(SYSTEMD_TEMPLATE_DIR)/grove-mcp.service.in"; \
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
	@if [ -n "$(WITH_MCP)" ]; then rm -f $(SYSTEMD_USER_DIR)/grove-mcp.service; fi
	systemctl --user daemon-reload
	@echo "✓ removed unit files"

systemd-status:  ## Show status of installed grove user units
	@for u in $(_UNITS); do \
	  echo "─── $$u ───"; \
	  systemctl --user --no-pager status $$u 2>&1 | head -10 || true; \
	  echo; \
	done
