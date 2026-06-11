---
name: reinstalling-grove
description: Use when a user wants to reinstall, update, or upgrade Grove after pulling new code, or when they report seeing the OLD UI / stale behavior after an update ("I still see the old dashboard", "the redesign isn't showing", "I reinstalled but nothing changed"). Covers the editable uv-tool install model, the three independently-updated surfaces (package/CLI/TUI, daemon, webapp), the reinstall-vs-restart decision, a required verification pass, and a troubleshooting decision tree for stale processes, stale webapp builds, and browser/proxy caching. Reach for this skill whenever an update "didn't take" even if the user doesn't say the word "reinstall".
---

# Reinstalling & updating Grove

[Grove](https://github.com/bearlike/Grove) ships as one Python package with three
surfaces that update through **three different mechanisms**. Most "I updated but
still see the old version" reports come from updating one surface and forgetting
another, or from a process/browser still holding the old code. This skill makes
the update deterministic and gives you a decision tree for the stale-UI case.

The golden rule, and the thing to repeat to the user: **new code on disk does
not change anything already running.** A live process loaded its modules at
startup; a long-lived service keeps running the old build until restarted; a
browser serves a cached page. Updating Grove is therefore never "just pull" — it
is pull, then refresh each surface that needs it.

## 1. The three surfaces and how each updates

| Surface | What it is | How it picks up new code |
|---|---|---|
| **Package — CLI + TUI** | the `grove` command and its Textual UI | a fresh `grove` invocation (editable install) OR a `uv tool install --reinstall` when deps changed |
| **Daemon** | `grove daemon serve`, the loopback HTTP + tmux service (default `127.0.0.1:7421`) | **service restart** (it is long-lived) |
| **Webapp** | the read-only Next.js dashboard (default `:3000`) | **rebuild `.next` THEN restart** the webapp service |

Two architecture facts decide where staleness comes from, so internalize them
before troubleshooting:

- **The TUI Activity Dashboard reads `ActivityService` in-process — it does NOT
  call the daemon.** So restarting the daemon does nothing for the TUI; only
  relaunching the `grove` TUI does. (The list/peek screens are also in-process.)
- **The webapp consumes the daemon** (a BFF proxies browser → daemon, plus an
  SSE `/events` stream). So a stale *webapp* can come from the browser, the
  webapp build, OR the daemon — three layers to check, in that order of
  likelihood.

## 2. Identify the install first (never assume)

Grove on a dev host is almost always an **editable** `uv tool` install pointed at
a local checkout. Editable means the package imports straight from `src/`, so
source edits and pulls are live on the *next launch* — but **dependencies are NOT
resynced when `pyproject.toml` changes** (that is the trap that makes a reinstall
necessary even on an editable install).

Confirm what you are dealing with:

```bash
which grove                                   # usually ~/.local/bin/grove (a uv shim)
readlink -f "$(which grove)"                  # -> ~/.local/share/uv/tools/grove/bin/grove
# Is it editable, and where does it point?
cat ~/.local/share/uv/tools/grove/lib/python*/site-packages/grove-*.dist-info/direct_url.json
#   {"url":"file:///path/to/checkout","dir_info":{"editable":true}}  <- editable
# Prove the running code resolves to the checkout:
~/.local/share/uv/tools/grove/bin/python3 -c "import grove,os;print(os.path.realpath(grove.__file__))"
```

If `direct_url.json` shows `editable:true`, source pulls are live on next launch.
If it is a plain (non-editable) wheel install, EVERY code change needs a
reinstall — and an editable install is the better default for any host running
Grove out of a worktree (code edits land with just a service restart).

## 3. Decide: restart-only or full reinstall

After the user pulls, check whether dependencies moved since the currently
installed code:

```bash
cd <repo>
git log --oneline -1                          # confirm HEAD is the pulled commit
git status -sb | head -1                       # confirm HEAD vs origin (0/0 = current)
git diff --stat <previously-installed-commit>..HEAD -- pyproject.toml uv.lock
```

- **No `pyproject.toml` / `uv.lock` change** → editable install already has the new
  source. Do **not** reinstall. Just **restart the services** (Section 4) and tell
  the user to relaunch the TUI / refresh the browser.
- **Deps changed** (or you cannot determine the prior commit, or the install is
  non-editable) → **reinstall** to resync the environment:

```bash
cd <repo>
uv tool install --reinstall --force --editable '.[daemon]'
```

The `[daemon]` extra (fastapi + uvicorn) is **load-bearing** — without it
`grove daemon` fails with "No such command" because the Typer mount is gated by
an optional import. The shim path (`~/.local/bin/grove`) is unchanged by a
reinstall, so the systemd unit and any PATH drop-in keep working untouched.

> Run `uv` from the user's real toolchain (e.g. pyenv/cargo), not a stale system
> `uv` — an ancient `uv` may lack `tool install` semantics. `which uv` to check.

## 4. Refresh each running surface

```bash
# Daemon — always restart after a code update that touches the daemon/engine:
systemctl --user restart grove-daemon

# Webapp — rebuild THEN restart, or the redesign is silently absent.
# `next start` serves the prebuilt .next; new components don't exist until rebuilt:
cd <repo> && make webapp-build && systemctl --user restart grove-webapp

# TUI — you cannot restart this for the user; it is a process THEY launch.
# Tell them to quit any open `grove` TUI and run `grove` again.
```

If Grove is not run under systemd on this host, restart whatever owns each
process (a tmux window, a terminal, a supervisor). The mechanism differs; the
principle ("long-lived process must be restarted") does not.

## 5. Verify — prove each surface loaded the new code

Do not tell the user "done" without evidence. Claims of success need output.

```bash
# Daemon up? Poll with a REAL delay — it races its own bind for ~1-2s after restart:
for i in $(seq 1 15); do c=$(curl -s -o /dev/null -w "%{http_code}" --max-time 2 \
  http://127.0.0.1:7421/healthz); [ "$c" = 200 ] && { echo "healthz 200 (${i}s)"; break; }; sleep 1; done

# Daemon route surface (count + a known-new endpoint, e.g. /events, /activity):
curl -s http://127.0.0.1:7421/openapi.json | python3 -c \
  "import sys,json;d=json.load(sys.stdin);print('routes',len(d['paths']),'/events' in d['paths'])"

# CLI + config sanity:
grove debug            # expect config_loaded: true
grove config show      # must parse (a ConfigError here = bad config, not a stale install)

# Webapp build freshness — BUILD_ID mtime should be AFTER your rebuild,
# and the service start AFTER that:
cat <repo>/webapp/.next/BUILD_ID ; stat -c '%y' <repo>/webapp/.next/BUILD_ID
systemctl --user show grove-webapp -p ActiveEnterTimestamp --value
curl -s -o /dev/null -w "webapp %{http_code}\n" http://127.0.0.1:3000/   # 200/302/307 = up
```

Report back exactly which surfaces you refreshed and the HEAD they now serve.

## 6. Troubleshooting: "I still see the old UI / it didn't update"

Work the tree by surface. The first question is always **which surface** —
terminal TUI, browser webapp, or daemon API behavior — because the cause and fix
diverge completely. If the user is unsure, ask; you cannot see their screen.

### A. TUI shows old UI

Almost always a `grove` TUI process that was **launched before the update** and
is still running. Editable install changed the files on disk; the live Python
process does not hot-reload.

```bash
ps -eo pid,etimes,cmd | grep '[b]in/grove' | grep -v 'daemon serve'
```

`etimes` (seconds alive) older than your update = stale. **Fix: the user quits
and relaunches `grove`.** Do **not** kill their processes for them — they are
attached to live agent/workspace sessions; that is the user's call.

### B. Webapp (browser) shows old layout/behavior

Check the layers in order — server-side first, then client:

1. **Stale `.next` build.** `next start` serves whatever was last built. If you
   restarted the webapp without rebuilding, the redesign is absent. Confirm the
   build is fresh (Section 5), and if not: `make webapp-build && systemctl --user
   restart grove-webapp`. To prove the *new feature* is actually compiled in,
   grep the built bundle for a marker unique to the new code (e.g. a new
   dependency or component name):
   ```bash
   grep -rl '<new-marker>' <repo>/webapp/.next/static | head   # e.g. 'xterm'
   ```
2. **Build is fresh but the browser still shows old.** New behavior with an old
   *layout* is the classic signature of a **cached HTML document / stylesheet**.
   Next.js content-hashes CSS, but a browser that served the whole page from
   disk/bfcache keeps pointing at the old stylesheet. Definitive test: open an
   **incognito window to the direct origin** (`http://localhost:3000` or the LAN
   IP). New there → it was the normal profile's cache (hard-reload with
   Ctrl/Cmd+Shift+R, or clear site data). Still old there → go to step 3.
3. **A reverse proxy / different host is serving old code.** In a homelab the
   user often reaches Grove through a domain fronted by nginx / Caddy / a
   Cloudflare tunnel, not `:3000` directly. That layer may cache responses or
   proxy to a stale upstream. Get the **exact URL the user opens**, then compare
   what that URL returns to the known-good `:3000` (build id, presence of the new
   marker). Reading/flushing the proxy config may need `sudo`. Rule out unrelated
   listeners first — a port serving a *different* app (its `<title>` won't say
   "Grove") is a red herring, not a stale Grove.

### C. Daemon API behaves like the old version (missing route, old response shape)

The daemon is long-lived. Restart it and re-check `/openapi.json`. If a freshly
added endpoint 404s after restart, suspect a **non-editable or stale install**
serving an old wheel — go back to Section 2, confirm `editable:true` points at
the pulled checkout, and reinstall if not. (A common cause: a plain
`uv tool install grove` pulled the published PyPI wheel instead of the local
checkout.)

## 7. Host-specific facts

Exact ports, service names, the daemon's PATH drop-in, and which unrelated
services live on nearby ports are **host-specific** — they belong in the
operator's memory/notes, not hard-coded here. Recover them live:

```bash
grove debug                                   # resolved config/state/schema paths
systemctl --user list-units 'grove-*'          # the Grove services on this host
systemctl --user cat grove-daemon grove-webapp # ExecStart, ports, Environment, drop-ins
```

The systemd units are rendered from `packaging/systemd/*.service.in` templates via
`make systemd*` targets; the daemon often needs a PATH drop-in so its init scripts
find the user's real toolchain (systemd user units do not source the shell
profile). If a per-host detail was non-obvious to recover, save it to memory so
the next session starts ahead.
