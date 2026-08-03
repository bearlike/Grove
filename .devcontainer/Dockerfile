# Tier 0 image for developing Grove itself inside a container workspace.
# See README.md for the full tier model. Anything that changes more often
# than monthly, or is project-specific rather than toolchain-level, belongs
# in bootstrap.sh / init.d (Tier 2/3), NOT here — a dependency bump must
# never force an image rebuild.

FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

# iptables + iproute2 are hard requirements of Grove's OWN egress firewall
# script, which runs as part of postStartCommand: it shells out to both and
# verifies the final DROP rule by asserting 1.1.1.1:443 is unreachable. If
# either binary is missing, container start fails outright. tmux and git are
# Grove's own runtime prerequisites (it shells out to both; a missing tmux
# raises TmuxError). curl/ca-certificates/jq/ripgrep/build-essential are
# general dev-loop tooling.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        iptables iproute2 \
        tmux git curl ca-certificates jq ripgrep build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8

# uv — same COPY-from-ghcr pattern as packaging/docker/Dockerfile.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Pre-fetch managed CPython 3.12 as `vscode` (the remote user) so the first
# `uv sync` in bootstrap.sh never pays the interpreter-download cost. This
# mirrors the same deliberate speed step in packaging/docker/Dockerfile.
USER vscode
ENV PATH="/home/vscode/.local/bin:${PATH}"
RUN uv python install 3.12
USER root

# --- Playwright browsers: baked into the image, not the shared cache volume ---
#
# webapp/playwright.config.ts only exercises Chromium (mobile-chrome +
# desktop-chrome), so that's the only browser fetched.
#
# PLAYWRIGHT_VERSION must track the version package-lock.json actually RESOLVES
# (currently 1.59.1), not the `^1.48.0` range in package.json — Playwright keys
# its browser directory off the exact installed playwright-core, so a stale
# value here bakes a build the test runner will never look for. This ARG is a
# hand-maintained default: the build context is .devcontainer/, so the
# Dockerfile cannot read webapp/package-lock.json to derive it automatically.
# Drift is not fatal — init.d/46-playwright-browsers.sh always re-runs
# `playwright install`, which self-heals a mismatch at container create time.
# Bump this when the lockfile's @playwright/test major/minor moves, to keep the
# fast path a cache hit rather than a download.
#
# Resolution of the bake-vs-volume conflict (PLAYWRIGHT_BROWSERS_PATH is also
# a permitted shared-cache mount target per the devcontainer contract):
#   1. Downloading Chromium needs the Playwright CDN, which is NOT on Grove's
#      runtime egress allowlist. Image builds run outside that firewall
#      entirely, so baking here avoids ever having to widen the always-on
#      runtime allowlist for a one-time asset fetch.
#   2. A volume mounted at PLAYWRIGHT_BROWSERS_PATH would MASK whatever is
#      baked into this layer. Every workspace already gets its own copy for
#      free from the image (no per-workspace download, no cross-workspace
#      contention), so a shared volume would add nothing but that masking
#      risk. devcontainer.json therefore intentionally does NOT mount
#      devc-grove-playwright — only the uv and npm caches are volumes.
#
# The Node feature (Tier 1) is applied by the devcontainers CLI AFTER this
# Dockerfile builds, so it isn't available yet in this RUN. We install a
# throwaway apt nodejs/npm just to run the Playwright CLI once, then remove
# them again — the OS deps `--with-deps` installs for Chromium itself are
# separate apt packages and stay behind. The real Node 20 runtime that the
# webapp actually builds/tests with comes from the `node:2` feature.
ARG PLAYWRIGHT_VERSION=1.59.1
ENV PLAYWRIGHT_BROWSERS_PATH=/caches/playwright
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        nodejs npm \
    && mkdir -p /caches/playwright \
    && npm install -g "playwright@${PLAYWRIGHT_VERSION}" \
    && npx --yes "playwright@${PLAYWRIGHT_VERSION}" install --with-deps chromium \
    && npm uninstall -g playwright \
    && apt-get purge -y nodejs npm \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Cache volume mount points (uv + npm are shared, concurrency-safe volumes —
# see README.md). Create + chown before the volume attaches, or Docker
# creates the mountpoint root-owned on first mount and `vscode` can't write.
RUN mkdir -p /caches/uv /caches/npm /caches/playwright \
    && chown -R vscode:vscode /caches

USER vscode
