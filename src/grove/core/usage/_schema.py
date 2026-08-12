"""The usage cache's SQL schema — pure DDL, no I/O, no connection.

Separate from ``_store.py`` so the shape can be read, reviewed and tested
without opening a database, and so the detectors in ``insights.py`` have one
place to learn the columns they query.

**This database is a CACHE, never a record.** Every row is derived from a
transcript Grove can re-read, so there are no migrations: a schema version
mismatch drops the file and re-indexes. That is a deliberate trade — migration
code is the most dangerous code in a persistence layer, and here it would be
protecting data that can be rebuilt in seconds from the files it came from.

Three shape decisions carry most of the weight:

**Timestamps are epoch INTEGERs, not ISO text.** Every query this cache serves
is arithmetic — range filters, day buckets, latency deltas — and text dates make
each of those a string operation with an index that only helps lexicographically.
One representation, converted at the edges.

**Day buckets are NOT stored.** A calendar day depends on the timezone the
reader asked for, so a stored ``day`` column would silently answer every request
in whatever zone the indexer happened to run in. Buckets are computed by joining
against boundaries generated per request (see ``_store.day_boundaries``), which
is exact across DST where a fixed hour offset is not.

**One event spine, not a table per chart.** ``usage_events`` holds narrow
indexed columns for everything that is queried and an additive ``attrs_json``
for everything that is merely carried. A new card is a new query, never a new
table — the discipline that keeps six surfaces reading one source of truth.
"""

from __future__ import annotations

from typing import Final

SCHEMA_VERSION: Final = 5
"""Bumped whenever any statement below changes.

A mismatch rebuilds rather than migrates. Bump this on ANY column change — a
forgotten bump is a cache serving one shape while the code reads another, which
surfaces as inexplicably empty cards rather than as an error.

**Bump it for a change in what a column MEANS, too, not only for a change in
the column list.** Version 4 added no column: it filled `usage_events.target`
for shell tool calls (with the leading executable) and `attrs_json` with the
background flag, both of which were absent from every previously indexed row.
Without the bump the shell-command ranking would read as an empty range on
every existing cache, which is indistinguishable from a quiet week.

Version 5 adds `sessions.generation_ms` / `sessions.tool_ms` — the two halves
`execution_ms` had been folding together. Here the bump is doing its ordinary
job: without it every pre-existing row would answer NULL for both, which the
wire contracts as *not measured* and which a reader cannot tell apart from a
session that genuinely ran no tools.
"""

_META: Final = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_SOURCES: Final = """
CREATE TABLE IF NOT EXISTS sources (
    source_id       TEXT PRIMARY KEY,
    provider        TEXT NOT NULL,
    root            TEXT NOT NULL,
    label           TEXT NOT NULL,
    account_id      TEXT,
    health          TEXT NOT NULL DEFAULT 'ok',
    detail          TEXT,
    last_indexed_at INTEGER
);
"""

_INGESTED_FILES: Final = """
CREATE TABLE IF NOT EXISTS ingested_files (
    path        TEXT PRIMARY KEY,
    source_id   TEXT NOT NULL,
    session_id  TEXT,
    inode       INTEGER,
    size        INTEGER NOT NULL,
    mtime_ns    INTEGER NOT NULL,
    cursor      INTEGER NOT NULL DEFAULT 0,
    ingested_at INTEGER NOT NULL
);
"""

_SESSIONS: Final = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id          TEXT NOT NULL,
    source_id           TEXT NOT NULL,
    provider            TEXT NOT NULL,
    cwd                 TEXT,
    project             TEXT,
    account_id          TEXT,
    workspace_id        TEXT,
    started_at          INTEGER,
    last_event_at       INTEGER,
    turns               INTEGER NOT NULL DEFAULT 0,
    tool_calls          INTEGER NOT NULL DEFAULT 0,
    tool_failures       INTEGER NOT NULL DEFAULT 0,
    files_changed       INTEGER NOT NULL DEFAULT 0,
    active_ms           INTEGER,
    execution_ms        INTEGER,
    -- The two halves `execution_ms` sums together: time a request was out at
    -- the model, and time a tool was running. A partition of that column, not
    -- a second measurement — both come from the same interval pass, so
    -- `generation_ms + tool_ms = execution_ms` wherever all three are
    -- measured. There is deliberately NO union counterpart: `active_ms`
    -- merges overlaps, and two merged spans cannot be added back together
    -- without double-counting a tool that ran while a sub-agent generated.
    -- NULL means that kind of work was never timed for this session, never 0.
    generation_ms       INTEGER,
    tool_ms             INTEGER,
    elapsed_span_ms     INTEGER,
    duration_confidence TEXT NOT NULL DEFAULT 'unknown',
    fresh_input         INTEGER,
    cache_read          INTEGER,
    cache_creation      INTEGER,
    reasoning           INTEGER,
    output              INTEGER,
    provider_total      INTEGER,
    -- The above five ALREADY include delegated sub-agent work (a session's
    -- messages carry root and sidechain usage together); these six are the
    -- portion of it attributable to sub-agents ALONE, so a reader can say
    -- "of the total, this much was delegated" without a second ingest path
    -- or a second parser. NULL means no sub-agent evidence was measured for
    -- this session, never a fabricated 0. `subagent_provider_total` mirrors
    -- `provider_total`'s shape for symmetry with `TOKEN_COLUMNS` / `_tokens()`
    -- but is unpopulated today (no adapter reports a per-sub-agent provider
    -- total) — kept nullable rather than omitted so a future one needs no
    -- schema bump.
    subagent_fresh_input    INTEGER,
    subagent_cache_read     INTEGER,
    subagent_cache_creation INTEGER,
    subagent_reasoning      INTEGER,
    subagent_output         INTEGER,
    subagent_provider_total INTEGER,
    cost_amount         TEXT,
    cost_currency       TEXT,
    cost_provenance     TEXT NOT NULL DEFAULT 'unknown',
    models              TEXT NOT NULL DEFAULT '[]',
    parser_health       TEXT NOT NULL DEFAULT 'ok',
    parser_detail       TEXT,
    PRIMARY KEY (session_id, source_id)
);
"""

_USAGE_EVENTS: Final = """
CREATE TABLE IF NOT EXISTS usage_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT NOT NULL,
    source_id         TEXT NOT NULL,
    seq               INTEGER NOT NULL,
    ts                INTEGER,
    kind              TEXT NOT NULL,
    model             TEXT,
    tool_name         TEXT,
    target            TEXT,
    failure_category  TEXT,
    is_error          INTEGER NOT NULL DEFAULT 0,
    duration_ms       INTEGER,
    duration_source   TEXT,
    thread_id         TEXT,
    fresh_input       INTEGER,
    cache_read        INTEGER,
    cache_creation    INTEGER,
    reasoning         INTEGER,
    output            INTEGER,
    provider_total    INTEGER,
    attrs_json        TEXT
);
"""

_ACCOUNTS: Final = """
CREATE TABLE IF NOT EXISTS accounts (
    account_id   TEXT PRIMARY KEY,
    provider     TEXT NOT NULL,
    label        TEXT NOT NULL,
    billing_mode TEXT NOT NULL DEFAULT 'unknown'
);
"""

_QUOTA_SNAPSHOTS: Final = """
CREATE TABLE IF NOT EXISTS quota_snapshots (
    account_id        TEXT NOT NULL,
    scope             TEXT NOT NULL,
    label             TEXT NOT NULL,
    window_seconds    INTEGER,
    used_percent      REAL,
    remaining_percent REAL,
    resets_at         INTEGER,
    limit_value       REAL,
    used_value        REAL,
    unit              TEXT,
    observed_at       INTEGER NOT NULL,
    evidence          TEXT NOT NULL DEFAULT 'unknown',
    status            TEXT NOT NULL DEFAULT 'ok',
    detail            TEXT,
    PRIMARY KEY (account_id, scope, label)
);
"""

_INDEXES: Final = (
    # The session table is read newest-first and filtered by every facet the
    # page exposes; these four cover the sort plus the three narrowing filters.
    "CREATE INDEX IF NOT EXISTS ix_sessions_last_event ON sessions(last_event_at DESC);",
    "CREATE INDEX IF NOT EXISTS ix_sessions_provider ON sessions(provider);",
    "CREATE INDEX IF NOT EXISTS ix_sessions_project ON sessions(project);",
    "CREATE INDEX IF NOT EXISTS ix_sessions_account ON sessions(account_id);",
    # Every aggregate is a time-range scan first and a group-by second, so the
    # range column leads and the grouping column follows it in the same index.
    "CREATE INDEX IF NOT EXISTS ix_events_ts ON usage_events(ts);",
    "CREATE INDEX IF NOT EXISTS ix_events_session ON usage_events(session_id, source_id, seq);",
    "CREATE INDEX IF NOT EXISTS ix_events_kind_ts ON usage_events(kind, ts);",
    # The recurring-failure and retry-loop detectors both group failing calls by
    # tool; a partial index keeps it the size of the failures, not the calls.
    "CREATE INDEX IF NOT EXISTS ix_events_failures ON usage_events(tool_name, ts) "
    "WHERE is_error = 1;",
    "CREATE INDEX IF NOT EXISTS ix_ingested_source ON ingested_files(source_id);",
)

DDL: Final = (
    _META,
    _SOURCES,
    _INGESTED_FILES,
    _SESSIONS,
    _USAGE_EVENTS,
    _ACCOUNTS,
    _QUOTA_SNAPSHOTS,
    *_INDEXES,
)
"""Every statement needed to bring an empty file to the current schema, in
dependency order. Applied in one transaction by ``_store.UsageStore``."""

EVENT_KINDS: Final = frozenset(
    {"generation", "tool_call", "tool_result", "file_edit", "compaction", "subagent"}
)
"""What may appear in ``usage_events.kind``.

Not a SQL ``CHECK`` constraint deliberately: a constraint here would make an
unrecognized provider record abort the whole file's ingest, where the contract
for every read in this codebase is that a strange record degrades itself. The
projector validates against this set and drops what it cannot classify, with the
drop recorded on the source's health rather than raised.
"""

TOKEN_COLUMNS: Final = (
    "fresh_input",
    "cache_read",
    "cache_creation",
    "reasoning",
    "output",
    "provider_total",
)
"""The token columns, in the order ``TokenClassesView`` declares them.

Shared by the projector (which writes them), the query service (which sums
them) and the detectors (which compare them), so the five classes cannot be
folded by one caller and kept apart by another. Every one is nullable, and
``SUM`` over all-NULL correctly yields NULL — which the wire renders as *not
reported*, never as zero.
"""
