"""On-disk path resolution for Grove.

Wraps `platformdirs` so the rest of the codebase never thinks about
%APPDATA%, ~/Library/Application Support, or $XDG_CONFIG_HOME directly.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from loguru import logger
from platformdirs import user_config_dir, user_state_dir

# POSIX-only stdlib: a bare top-level import raises ModuleNotFoundError on
# Windows during test COLLECTION, taking every module that transitively imports
# this one with it (the rule `grove.client` already follows for pty/termios).
if sys.platform != "win32":
    import fcntl
else:  # pragma: no cover - not exercised on the Linux-only CI
    fcntl = None  # type: ignore[assignment]

_APP_NAME = "grove"
_PROJECT_DIR_NAME = ".grove"
_CONFIG_FILE = "config.json"
_LOCAL_CONFIG_FILE = "config.local.json"
_SCHEMA_FILE = "config.schema.json"
_STATE_FILE = "state.json"
_AUTH_FILE = "auth.json"
_WEBAPP_SESSIONS_FILE = "webapp-sessions.json"
_LOGS_DIR = "logs"
_THEMES_DIR = "themes"
_SIDECAR_DIR = "agent-sidecars"
_HOOK_SPOOL_DIR = "spool"
_CONTAINER_SECRETS_DIR = "container-secrets"
_AGENT_CONFIG_DIR = "agent-config"
_AGENT_EXIT_DIR = "agent-exits"
_CONTAINER_TMUX_DIR = "container-tmux"
_CONTAINER_NETFILTER_DIR = "container-netfilter"
_HOOKS_SETTINGS_FILE = "claude-hooks-settings.json"
_CONTAINER_SETTINGS_FILE = "claude-container-settings.json"
_BRIEF_FILE = "agent-brief.md"
_HANDOVER_FILE = "handovers.json"
_USAGE_DB_FILE = "usage.sqlite3"
_QUOTA_STATE_FILE = "quota-state.json"
_TELEMETRY_LEDGER_FILE = "telemetry-exports.sqlite3"
_SESSION_TURNS_FILE = "session-turns.json"


def ensure_dir(path: Path) -> Path:
    """Create *path* (with parents) if missing, logging the first creation.

    The single seam every lazy first-write funnels through, so a fresh
    install reports exactly which directories Grove initialized and where.
    An existing directory is a silent no-op; the log line is
    INFO, visible under ``GROVE_DEBUG=1``.
    """
    if path.is_dir():
        return path
    path.mkdir(parents=True, exist_ok=True)
    logger.info("initialized {}", path)
    return path


def write_atomic(path: Path, text: str, *, mode: int = 0o600) -> Path:
    """Replace *path* with *text* through a temp file no other writer can name.

    The writer counterpart of :func:`ensure_dir`, and the single seam for every
    file Grove rewrites wholesale. `os.replace` is atomic, but a **fixed** stage
    name (`<file>.tmp`, the shape this replaced) is not: Grove is several
    processes over one state directory (daemon, TUI, CLI, client), so two of
    them truncate and fill the SAME temp file and `os.replace` moves the
    interleaved result into place — permanent corruption of a file with no
    backup, no quarantine and no repair verb, where a lost update would have
    been merely annoying. `mkstemp` in the target's
    own directory gives each writer a private name, so the worst concurrent
    outcome is back to last-writer-wins.

    The temp file is created 0600 and chmod'd BEFORE the replace, so a
    restricted file is never briefly world-readable at its final path — the
    directory itself is world-traversable under a default umask, so the mode on
    the file is the only thing protecting it. Defaults to 0600
    because everything routed here lives in the user's own config/state dir and
    is read only by Grove processes running as that user; a file a *foreign*
    reader must open (a container agent under another uid) has to say so.
    """
    ensure_dir(path.parent)
    fd, staged = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(staged)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


@contextmanager
def exclusive_lock(path: Path) -> Iterator[None]:
    """Hold a cross-process exclusive lock covering a read-modify-write of *path*.

    The other half of :func:`write_atomic`. Atomic
    publication closed corruption but left last-writer-wins: two processes both
    read the file, each adds its own record, and whichever renames second
    publishes a state missing the other's change. Grove's normal operation has
    several writers over one state directory — the daemon, a `grove` CLI verb
    and the TUI — so the overlap is ordinary use, not an edge case.

    **The lock is a sidecar file, never *path* itself**, and that is forced by
    `write_atomic`: publication is a rename, so the file a second writer opens
    by name after it is a DIFFERENT inode from the one the first writer locked,
    and the two locks would not exclude each other at all. The sidecar is never
    replaced, so every writer converges on one inode. It is created 0600 and
    left behind deliberately — it holds no data, and unlinking it would
    reintroduce exactly the rename race it exists to avoid.

    Blocking, with no timeout: every holder does a bounded read + rewrite of a
    small file, and a holder that dies has its lock released by the kernel when
    its fd closes, so there is nothing a timeout could rescue.

    On native Windows this is a documented no-op — `fcntl` does not exist there,
    and Grove already requires WSL2 on Windows for tmux, so a lock-less native
    run is not a supported configuration in the first place (the same degrade
    rule the POSIX-only client modules follow).
    """
    lock_path = path.with_name(path.name + ".lock")
    if fcntl is None:  # pragma: no cover - native Windows, unsupported anyway
        yield
        return
    ensure_dir(lock_path.parent)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def user_config_path() -> Path:
    """User-scope config file path (XDG / AppData / ~/Library)."""
    return Path(user_config_dir(_APP_NAME)) / _CONFIG_FILE


def user_schema_path() -> Path:
    """Where we write the auto-generated JSON Schema next to the user config."""
    return Path(user_config_dir(_APP_NAME)) / _SCHEMA_FILE


def user_state_path() -> Path:
    """Single global workspace-state file. Keyed internally by repo_root."""
    return Path(user_state_dir(_APP_NAME)) / _STATE_FILE


def usage_db_path() -> Path:
    """The historical usage audit's SQLite cache.

    Under the STATE dir, beside the workspace state, because nobody authors it:
    it is derived wholly from transcripts Grove can already read, and deleting
    it is always safe — the next refresh rebuilds it. That is also why it takes
    no lock discipline from ``exclusive_lock``: SQLite in WAL mode owns its own
    concurrency, and the file has exactly one writer path.
    """
    return Path(user_state_dir(_APP_NAME)) / _USAGE_DB_FILE


def quota_state_path() -> Path:
    """Durable last-known-good quota readings and per-account probe cool-offs.

    Beside the workspace state rather than inside ``usage.sqlite3`` because it is
    the opposite kind of file: that cache is derived wholly from transcripts and
    may be deleted at any time, while a quota reading can never be recomputed —
    once a provider stops answering, the last one Grove saw is the only one that
    will ever exist. Holding it here is also what lets the daemon, the TUI and a
    `grove` CLI run share one probe budget instead of each contacting a provider
    to learn the same number. Contains no credential: account ids, operator
    labels, percentages and timestamps only.
    """
    return Path(user_state_dir(_APP_NAME)) / _QUOTA_STATE_FILE


def session_turns_path() -> Path:
    """Durable human-turn count per agent session, keyed by transcript identity.

    Beside the workspace state rather than inside ``usage.sqlite3`` even though
    both are rebuildable transcript projections: that cache prunes by
    ``usage.retention_days`` and is gated on ``usage.enabled``, so a column
    there would go blank for exactly the old sessions a browse view still
    lists, under a knob that belongs to a different feature. It is a
    cross-process cache by design — the daemon is the only writer, and every
    ``grove`` CLI run and TUI screen reads the counts it already paid for.
    Contains ids, transcript fingerprints and integers; no prompt, no content.
    """
    return Path(user_state_dir(_APP_NAME)) / _SESSION_TURNS_FILE


def telemetry_ledger_path() -> Path:
    """Durable record of external telemetry observations already accepted.

    Unlike ``usage.sqlite3`` this is not a rebuildable cache: deleting it can
    cause an external backend to receive the same historical observation
    again. It therefore has its own file and schema lifecycle.
    """
    return Path(user_state_dir(_APP_NAME)) / _TELEMETRY_LEDGER_FILE


def user_handover_path() -> Path:
    """Every ticket Grove has ever been handed, as a durable JSON log.

    Beside the workspace state file, and under the STATE dir rather than the
    config dir, because nobody authors it — it is a record Grove writes about
    what it has done. It has to be durable and it has to be independent of
    current state: the assignee pickup ASSIGNS the bot, so anything derived from
    the tracker's live assignees would re-trigger on Grove's own write, forever.
    Nothing here is a credential; entries are one small row per ticket ever
    handed over and are never evicted, because an evicted row is a ticket that
    can be picked up a second time.
    """
    return Path(user_state_dir(_APP_NAME)) / _HANDOVER_FILE


def init_log_path(workspace_id: str) -> Path:
    """Per-workspace init-script log; lives next to the state file.

    Lets a failed init be diagnosed from the rail (`see {path}`) without
    grepping loguru output. Best-effort — write failures don't block create.
    """
    return Path(user_state_dir(_APP_NAME)) / _LOGS_DIR / f"{workspace_id}-init.log"


def provision_log_path(workspace_id: str) -> Path:
    """Per-workspace container-provisioning log; sibling of the init log.

    Written AS the provision runs, not after it: a cold `devcontainer up` that
    hits its timeout is exactly the failure that most needs an artifact, and a
    log flushed only on success would leave none. Survives the
    create rollback for the same reason the init log does.
    """
    return Path(user_state_dir(_APP_NAME)) / _LOGS_DIR / f"{workspace_id}-provision.log"


def container_secrets_path(workspace_id: str) -> Path:
    """Per-workspace `devcontainer up --secrets-file` payload.

    Under Grove's state root like the logs above, and therefore **outside every
    worktree by construction** — a worktree is a git checkout bind-mounted into
    the container, so a secrets file written there is one ``git add -A`` from
    being committed. Per workspace so two concurrent provisions cannot read
    each other's file. Path-only: the writer
    (:meth:`grove.core.env_source.EnvSource.secrets_file`) creates it 0600
    and deletes it as soon as the CLI is done with it.
    """
    return Path(user_state_dir(_APP_NAME)) / _CONTAINER_SECRETS_DIR / f"{workspace_id}.json"


def agent_exit_path(workspace_id: str) -> Path:
    """Where the agent command's exit status is recorded for one workspace.

    Sibling of the init/provision logs, and per workspace for the same reason:
    two concurrent launches must not read each other's record. Path-only — the
    *shell in the agent pane* is the writer (see
    :class:`grove.core.launch.AgentExit`), which is what makes the fact
    observable at all: a dead agent leaves a live fallback shell behind, so
    there is nothing for Grove itself to notice at launch time.
    """
    return Path(user_state_dir(_APP_NAME)) / _AGENT_EXIT_DIR / f"{workspace_id}.exit"


def agent_sidecar_dir() -> Path:
    """Directory of per-session agent status sidecars (push-status).

    Lives next to the state file. Each `<session_id>.json` is written by the
    Grove-managed Claude Code hook on a lifecycle event and read by the
    ActivityService to override the polled status with a push signal. Path-only;
    the hook creates the directory on first write.
    """
    return Path(user_state_dir(_APP_NAME)) / _SIDECAR_DIR


def agent_hook_spool_dir(sidecar_dir: Path | None = None) -> Path:
    """Where a hook that cannot fold its own event drops the raw payload.

    A container's agent has no ``grove-agent-hook`` — it is a Python console
    script of a package the project's image never installed — so the hook there
    writes the payload verbatim into this directory and the HOST folds it into a
    sidecar on the next read (:meth:`grove.core.agents.hook.ClaudeHook.drain`).
    It is a child of :func:`agent_sidecar_dir` rather than a sibling so the one
    argument every sidecar reader already passes locates it too: adding a second
    path to thread through four read sites is exactly the shape this tree keeps
    finding half-wired.

    *sidecar_dir* derives the spool of an INJECTED sidecar directory (the test
    seam, and the only reason the parameter exists); the default answers for the
    real one. Path-only — the create path creates it, because it is a bind
    SOURCE and Docker materializes a missing one as a root-owned directory.
    """
    return (agent_sidecar_dir() if sidecar_dir is None else sidecar_dir) / _HOOK_SPOOL_DIR


def agent_workspace_config_dir(workspace_id: str) -> Path:
    """Per-workspace, Grove-owned agent config dir mounted into a container.

    The host side of the container's ``CLAUDE_CONFIG_DIR``/``CODEX_HOME``: shared
    host paths are bind-mounted *into* it individually and the per-workspace
    seeded ``.claude.json`` copy lives here, so no container ever writes into the
    user's real ``~/.claude`` — an in-container config rewrite dies with the
    workspace instead of following the user into every future host session.
    Path-only; the create path creates it.
    """
    return Path(user_state_dir(_APP_NAME)) / _AGENT_CONFIG_DIR / workspace_id


def container_tmux_payload_dir(version: str) -> Path:
    """Grove's built static-tmux bundle for one tmux *version*.

    Under the state dir rather than the config dir: it is a *derived artifact*
    Grove can rebuild at will, not something a user authored. Keyed by version
    alone — the ARCHITECTURE split lives inside (``bin/<arch>/tmux`` beside a
    shared ``terminfo/``), because the binary is machine code and the terminfo
    database is not, and because one container mount has to serve whatever
    platform the container turns out to run.

    Path-only; the builder creates it and moves finished artifacts into place.
    """
    return Path(user_state_dir(_APP_NAME)) / _CONTAINER_TMUX_DIR / version


def container_netfilter_payload_dir(version: str) -> Path:
    """Grove's built static ``iptables``/``ip6tables`` bundle, per version.

    Same reasoning and same shape as :func:`container_tmux_payload_dir` — a
    derived artifact under the state dir, keyed by the upstream version so a
    bump rebuilds rather than serving a stale binary out of an identically-named
    directory, with the architecture split inside (``bin/<machine>/iptables``).

    Path-only; the builder creates it and moves finished artifacts into place.
    """
    return Path(user_state_dir(_APP_NAME)) / _CONTAINER_NETFILTER_DIR / version


def agent_hooks_settings_path() -> Path:
    """Grove-owned, hook-only Claude Code settings file.

    Passed to `claude --settings <path>` so Grove's status hook is added as an
    extra settings layer without ever mutating the user's own
    `.claude/settings.json` — uninstall is just not passing the flag.
    """
    return Path(user_config_dir(_APP_NAME)) / _HOOKS_SETTINGS_FILE


def agent_brief_path() -> Path:
    """Grove-owned first-turn brief, rendered for the ``UserPromptSubmit`` hook.

    Beside the hook settings file, because it has the same lifecycle: Grove owns
    it, every launch rewrites it, and it is only ever read by the hook process.
    Host-global like that file — the text is identical for every workspace, so
    the per-workspace choice is carried by whether the launch env names this path
    at all (:attr:`grove.core.agents.brief.AgentBrief.PATH_ENV`), never by a
    second copy per workspace.
    """
    return Path(user_config_dir(_APP_NAME)) / _BRIEF_FILE


def agent_container_settings_path() -> Path:
    """The CONTAINER-only variant of the file above: hooks plus container decor.

    Two files rather than one because ``--settings`` is single-valued — the CLI
    keeps the last occurrence — so Grove cannot pass a container-only layer
    alongside the hook layer and must merge them into one payload. And the merge
    cannot happen in the hook file itself: that one is deliberately
    namespace-agnostic (its hook command probes for its entry point and spools
    when absent), which is what lets ONE rendered file serve a host launch and a
    container launch alike. A ``statusLine`` naming a path under ``/grove`` has
    no such property — it is meaningless on the host and would silently replace
    whatever statusline the user configured for themselves.

    So the host file stays byte-identical to what it always was, and only a
    container launch reaches for this one.
    """
    return Path(user_config_dir(_APP_NAME)) / _CONTAINER_SETTINGS_FILE


def project_config_path(repo_root: Path) -> Path:
    """Committed project config: <repo>/.grove/config.json."""
    return repo_root / _PROJECT_DIR_NAME / _CONFIG_FILE


def project_local_config_path(repo_root: Path) -> Path:
    """Gitignored machine-specific overrides: <repo>/.grove/config.local.json."""
    return repo_root / _PROJECT_DIR_NAME / _LOCAL_CONFIG_FILE


def project_grove_dir(repo_root: Path) -> Path:
    """The `.grove/` directory inside a repo."""
    return repo_root / _PROJECT_DIR_NAME


def user_auth_path() -> Path:
    """Single-file persistent store for pairing challenges + sessions.

    Lives next to the user config so platformdirs places it in the right
    OS-specific directory (Linux: ``~/.config/grove/auth.json``; macOS:
    ``~/Library/Application Support/grove/auth.json``; Windows:
    ``%APPDATA%/grove/auth.json``). Same atomic-write pattern as the
    workspace state store. Holds metadata only — never plaintext tokens
    nor password equivalents.
    """
    return Path(user_config_dir(_APP_NAME)) / _AUTH_FILE


def user_webapp_sessions_path() -> Path:
    """Webapp BFF cookie ↔ daemon-token mapping.

    Persisted server-side so a Next.js process restart doesn't log every
    browser out. Same directory as ``user_auth_path``; daemon process
    never reads this file (different concern, different writer).
    """
    return Path(user_config_dir(_APP_NAME)) / _WEBAPP_SESSIONS_FILE


def user_themes_dir() -> Path:
    """User-scope theme override directory.

    Each `*.toml` file inside is registered as an additional Textual theme
    by `grove.tui.theme.register_themes`. Path-only — never created here;
    the loader treats a missing directory as "no overrides".
    """
    return Path(user_config_dir(_APP_NAME)) / _THEMES_DIR
