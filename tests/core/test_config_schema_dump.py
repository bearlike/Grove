"""Tests for the pure JSON-Schema dump helper.

`dump_schema_json` is the seam shared by `write_schema` (filesystem write)
and `grove config schema --stdout` (docs build pipeline) so the same JSON
Schema bytes flow through both paths.  Pinning the contract here keeps the
two callers from drifting.
"""

from __future__ import annotations

import json
import re

from grove.core.config import GroveConfig, dump_schema_json, write_schema

#: A bare `#<n>` — an issue reference with no owner/repo qualification. The
#: lookbehind spares `#!`-style prefixes and anything already qualified as a
#: URL path (`.../issues/241`), which are unambiguous wherever they are read.
_BARE_ISSUE_REF = re.compile(r"(?<![\w/])#\d+")


def test_dump_schema_json_parses_as_json() -> None:
    """The dump helper returns valid JSON ending in a trailing newline."""
    payload = dump_schema_json()
    assert payload.endswith("\n"), "trailing newline keeps `printf` / `cat` clean"
    parsed = json.loads(payload)
    assert isinstance(parsed, dict)


def test_dump_schema_json_describes_every_top_level_config_section() -> None:
    """Every top-level Pydantic field on `GroveConfig` appears in the schema."""
    parsed = json.loads(dump_schema_json())

    # Pydantic v2 emits the root model's properties either inline or via $defs.
    # `schema_url` carries the `$schema` alias, which lives at root not as a section.
    sections = set(GroveConfig.model_fields.keys()) - {"schema_url"}
    body = json.dumps(parsed)
    for section in sections:
        assert section in body, f"expected schema to mention `{section}`"


def test_dump_schema_json_matches_what_write_schema_writes(tmp_path) -> None:
    """`write_schema` and `dump_schema_json` must agree byte-for-byte.

    Otherwise the user-side schema file (autocomplete) drifts from the
    docs-side schema dump (configure-reference page) and the two stories
    quietly tell different truths.
    """
    target = tmp_path / "config.schema.json"
    write_schema(target)
    assert target.read_text(encoding="utf-8") == dump_schema_json()


def test_no_schema_prose_carries_a_bare_issue_reference() -> None:
    """Nothing the published config reference prints may name a bare `#<n>`.

    `docs/hooks/schema_to_md.py` renders `configure-reference.md` from this
    schema and prints its `description` strings VERBATIM, so every model
    docstring here is public copy. The docs deploy from the GitHub mirror,
    where a bare `#<n>` resolves against a different tracker than the one the
    number came from: the link lands on an unrelated issue and the numbering of
    a private issue graph is published alongside it. Same hazard the repo
    already avoids in PR titles and bodies, through a third door.

    Asserted over the whole schema rather than over the four docstrings that
    were wrong, and over the SCHEMA rather than the rendered markdown, because
    the schema is the boundary: it catches a field description as readily as a
    class docstring, and it does not need a docs build to run.

    The fix for a failure is always in the docstring, never in the generator or
    here — describe the behaviour and leave the archaeology to `CLAUDE.md`,
    which is written for maintainers and is not published.
    """
    offenders = {
        path: _BARE_ISSUE_REF.findall(text)
        for path, text in _walk_strings(json.loads(dump_schema_json()), "")
        if _BARE_ISSUE_REF.search(text)
    }
    assert offenders == {}


def test_a_fields_own_docstring_reaches_the_published_page() -> None:
    """An attribute docstring must EXPORT, not merely exist in the source.

    Pydantic emits one only under `use_attribute_docstrings`, and while that was
    off every field's prose was written, stored and silently dropped:
    `schema_to_md.py` rendered a Description column blank for 84 of 94 fields,
    so a shipped flag like `assign_bot` was undiscoverable to anyone reading the
    published reference. Nothing failed — an absent description is structurally
    valid, the docs built clean, and the other tests here assert the dump's
    SHAPE rather than that any field says anything.

    So this pins the mechanism at its narrowest honest point: fields that DO
    carry a docstring must have it reach the schema. It deliberately does not
    demand that every field carry one — a field whose prose was never written is
    a gap to fill, not a regression, and conflating the two would make this test
    fail for a reason it cannot explain.
    """
    schema = json.loads(dump_schema_json())
    issueops = (schema.get("$defs", {}).get("IssueOpsConfig") or {}).get("properties") or {}
    for field in ("enabled", "assign_bot", "pickup_enabled", "trigger"):
        described = (issueops.get(field, {}).get("description") or "").strip()
        assert described, f"`issueops.{field}` publishes no description"
    # The flag is global, so a representative section proves it for every model:
    # this is the count moving, not one hand-picked field being special.
    total = sum(len(b.get("properties") or {}) for b in schema.get("$defs", {}).values())
    described = sum(
        1
        for b in schema.get("$defs", {}).values()
        for spec in (b.get("properties") or {}).values()
        if (spec.get("description") or "").strip()
    )
    assert described > total // 2, f"only {described}/{total} fields describe themselves"


def _walk_strings(node: object, path: str) -> list[tuple[str, str]]:
    """Every string in the schema, paired with the JSON path it sits at."""
    if isinstance(node, dict):
        return [
            found
            for key, value in node.items()
            for found in (
                [(f"{path}.{key}", value)]
                if isinstance(value, str)
                else _walk_strings(value, f"{path}.{key}")
            )
        ]
    if isinstance(node, list):
        return [
            found
            for index, value in enumerate(node)
            for found in _walk_strings(value, f"{path}[{index}]")
        ]
    return []
