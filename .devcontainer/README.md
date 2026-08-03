# Grove devcontainer

> [!IMPORTANT]
> **This is a standard Dev Container configuration, and
> [Grove](https://github.com/bearlike/Grove) drives coding agents through it.**
>
> Open the repo in VS Code with the Dev Containers extension and you get the
> environment described below. Grove reads the same configuration, runs a coding
> agent inside that container, and manages the agent runtime and lifecycle from
> there. Grove did not invent a format. It adapted the one your editor already
> reads.
>
> This repo is Grove. So the config you edit here is the config your own agent
> workspace comes up on, and a mistake lands on you first.

Develop Grove itself inside a container workspace. Run `grove create --runtime
container`, or reopen in a container from an editor that speaks the devcontainer
spec.

## Tier model

| Tier | Mechanism | Cost | What lives here |
|---|---|---|---|
| 0 | `Dockerfile` | minutes, rare | OS packages, `iptables`+`iproute2` (Grove's egress firewall needs both or container start fails), tmux, git, uv, managed CPython 3.12, Playwright's Chromium browser + OS deps |
| 1 | `features` in `devcontainer.json` | cached layers | `common-utils`, `github-cli`, Node 20, `docker-in-docker`, `claude-code` |
| 2 | `postCreateCommand` → `bootstrap.sh create` | once per container | `.devcontainer/init.d/*.sh` steps that run on create: dependency sync (`uv sync`, `npm ci`), anything expensive but project-specific |
| 3 | `postStartCommand` → `bootstrap.sh start` | every start, seconds | `init.d/*.sh` steps that run on every start: env/secret seeding, reachability checks |

`bootstrap.sh` just iterates `init.d/*.sh` in lexical order with the phase in
`GROVE_INIT_PHASE`. Each script decides for itself whether it participates
(see the numbering scheme and idempotency rules at the top of `init.d/`).

## What forces an image rebuild vs. what doesn't

**Rebuilds the image. Tier 0/1, so you edit `Dockerfile` or `features`.**
- A new OS package, a different base image, a Python/Node major version bump.
- A Playwright version bump. The browser build baked into the image is
  pinned to `webapp/package.json`'s `@playwright/test` version via the
  `PLAYWRIGHT_VERSION` build arg in `Dockerfile`. Bump both together.

**Does NOT rebuild the image. Tier 2/3, so you edit `init.d/*.sh` only.**
- A Python or npm dependency version bump (picked up by the next `uv sync` /
  `npm ci` in `bootstrap.sh create`).
- New env vars, secrets, git config, reachability checks.

Unsure which tier a change belongs in? Ask whether it changes more often than
monthly, or whether it belongs to this project rather than the toolchain. If
either is true, it is Tier 2/3.

## Adding a dependency

- **New OS package** (e.g. a native lib some Python package needs): add it to
  the `apt-get install` line in `Dockerfile`, rebuild the image.
- **New Python package**. Add it to `pyproject.toml` as usual. `uv sync` in
  `bootstrap.sh create` picks it up on the next container create, with no image
  change needed.
- **New npm package**. Add it to `webapp/package.json`, and `npm ci` in
  `bootstrap.sh create` picks it up the same way.
- **New feature** (a devcontainer Feature from `ghcr.io/...`): add it to
  `features` in `devcontainer.json`, rebuild.

## Caches

Only content-addressed, concurrency-safe caches are shared volumes, since
multiple workspaces of this repo can run containers at the same time:

| Purpose | Volume | Mount target |
|---|---|---|
| uv package cache | `devc-grove-uv` | `/caches/uv` |
| npm package cache | `devc-grove-npm` | `/caches/npm` |

Playwright's Chromium browser is baked into the image instead of a shared
volume (see the comment in `Dockerfile` for why: it needs network access to
the Playwright CDN, which isn't on Grove's runtime egress allowlist, and a
volume mounted at `PLAYWRIGHT_BROWSERS_PATH` would mask the baked copy
anyway). `node_modules` and `.venv` are never cache volumes. They live in
the worktree bind mount, already per-workspace, and are made fast by the
caches above.

## Egress

Grove's container egress firewall defaults to an allowlist. Anything this
repo's dev loop needs beyond the built-in defaults (npm/pypi/github/docker
registries, the agent plane, this repo's own git remotes) goes in the
project's `.grove/config.json` under `container.egress.allow` if it's a
public hostname, or the gitignored `.grove/config.local.json` if it's
private. Never put a private hostname in a committed file.
