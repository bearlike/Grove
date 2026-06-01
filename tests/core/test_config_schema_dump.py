"""Tests for the pure JSON-Schema dump helper.

`dump_schema_json` is the seam shared by `write_schema` (filesystem write)
and `grove config schema --stdout` (docs build pipeline) so the same JSON
Schema bytes flow through both paths.  Pinning the contract here keeps the
two callers from drifting.
"""

from __future__ import annotations

import json

from grove.core.config import GroveConfig, dump_schema_json, write_schema


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
