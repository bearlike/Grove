"""``UsageStore`` mechanics: the concurrency PRAGMA and the tuple-row read path."""

from __future__ import annotations

from pathlib import Path

from grove.core.usage._store import UsageStore


def test_busy_timeout_pragma_defaults_to_five_seconds(tmp_path: Path) -> None:
    """SQLite's own default busy timeout is 0 — an immediate ``database is
    locked`` on a genuine cross-process write collision. Grove's default of
    5000ms must actually reach the connection's PRAGMA, not just the config
    model, or a real writer collision (the TUI's Usage screen refreshing
    while ``grove usage backfill`` runs, say) still raises instantly."""
    store = UsageStore(tmp_path / "usage.db")
    conn = store.connect()
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    store.close()


def test_busy_timeout_pragma_is_configurable(tmp_path: Path) -> None:
    store = UsageStore(tmp_path / "usage.db", busy_timeout_ms=12_345)
    conn = store.connect()
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 12_345
    store.close()


def test_busy_timeout_zero_restores_sqlites_immediate_raise_default(tmp_path: Path) -> None:
    store = UsageStore(tmp_path / "usage.db", busy_timeout_ms=0)
    conn = store.connect()
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 0
    store.close()


def test_query_tuples_matches_named_query_in_content_and_order(tmp_path: Path) -> None:
    """``query_tuples`` exists only to skip ``sqlite3.Row``'s per-column name
    lookup on a hot per-event loop — it must return exactly the same data as
    ``query()``, just unwrapped, or the read path silently reorders columns
    the caller destructures positionally."""
    store = UsageStore(tmp_path / "usage.db")
    store.set_meta("a", "1")
    store.set_meta("b", "2")

    # Scoped to the two keys this test wrote: `meta` is NOT empty on a fresh
    # store — `_open` stamps `schema_version` into it, which is the mechanism
    # that decides whether the cache rebuilds. An unscoped `SELECT` here
    # asserts the store's own bookkeeping is absent, which it never is.
    sql = "SELECT key, value FROM meta WHERE key IN ('a','b') ORDER BY key"
    named = store.query(sql)
    tupled = store.query_tuples(sql)

    assert [tuple(row) for row in named] == tupled
    assert tupled == [("a", "1"), ("b", "2")]
    # A second query() after query_tuples() must still return named rows —
    # the row_factory swap inside query_tuples must not leak past its call.
    assert store.query(sql)[0]["key"] == "a"
    store.close()
