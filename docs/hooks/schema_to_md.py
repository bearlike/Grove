"""mkdocs hook: render `configure-reference.md` from `docs/grove.schema.json`.

Runs at the `on_files` event so the generated page lives only inside the
build, never on disk between runs. The schema file itself is regenerated
by `make docs-schema` locally and by the CI workflow before `mkdocs build`.

If the schema file is missing (fresh clone, regeneration failed), the
hook emits a degraded page rather than failing the build, mirroring
the shadcn theme's `schema_to_md` pattern (`|| true` in CI).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import json

from mkdocs.structure.files import File, Files

_SCHEMA_PATH = Path("docs/grove.schema.json")
_TARGET = "configure-reference.md"
_DEGRADED = """# Configuration reference

The configuration reference is auto-generated from Grove's Pydantic model.
This build was produced without a schema dump on disk. To render the full
table locally:

```bash
make docs-schema && make docs-build
```

The canonical source of truth is `src/grove/core/config.py`.
"""


def on_files(files: Files, config: dict[str, Any], **_: Any) -> Files:
    """Generate `configure-reference.md` and append it to the file list."""
    docs_dir = Path(config["docs_dir"])
    body = _render(_SCHEMA_PATH) if _SCHEMA_PATH.exists() and _SCHEMA_PATH.stat().st_size else _DEGRADED

    out_path = docs_dir / _TARGET
    out_path.write_text(body, encoding="utf-8")
    existing = files.get_file_from_path(_TARGET)
    if existing is not None:
        files.remove(existing)
    files.append(
        File(
            _TARGET,
            str(docs_dir),
            config["site_dir"],
            config["use_directory_urls"],
        )
    )
    return files


# ─── rendering ──────────────────────────────────────────────────────────────


def _render(schema_path: Path) -> str:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    defs = schema.get("$defs", {})
    props = schema.get("properties", {})

    lines: list[str] = [
        # The H1 must stay the FIRST block and the H2 must follow it directly:
        # from theme v1.12.0 the pair is one masthead, the H1 rendering as a
        # small label and this H2 as the page's title. Put anything above the
        # H1 (a provenance comment is the obvious temptation on a generated
        # page) and the theme stops lifting it, so the page prints its own
        # title twice. Nothing warns; the build stays clean.
        "# Configuration reference\n",
        "## Every field and its default\n",
        (
            "This page is generated from Grove's Pydantic model.  Edit "
            "`src/grove/core/config.py` and run `make docs` (or push to "
            "the default branch, which regenerates in CI) to refresh.\n"
        ),
    ]

    lines.extend(_env_var_section(props, defs))

    for name, ref in props.items():
        if name.startswith("$"):  # `$schema` alias is internal; skip
            continue
        section = _resolve(ref, defs)
        is_array_of_models = section.get("type") == "array" and "$ref" in (section.get("items") or {})
        if is_array_of_models:
            inner = _resolve(section["items"], defs)
            heading = f"## `{name}` (list of `{inner.get('title', 'item')}`)"
        else:
            heading = f"## `{name}`"
            inner = section
        lines.append(f"\n{heading}\n")
        if inner.get("description"):
            lines.append(f"{inner['description']}\n")
        section_props = inner.get("properties") or {}
        if not section_props:
            continue
        lines.append("\n| Field | Type | Default | Description |")
        lines.append("|---|---|---|---|")
        for field, info in section_props.items():
            field_info = _resolve(info, defs)
            type_str = _type_str(field_info, defs)
            default = _default_str(field_info, inner, field)
            desc = (field_info.get("description") or "").replace("\n", " ").strip()
            lines.append(f"| `{field}` | `{type_str}` | `{default}` | {desc} |")

    lines.append("")
    return "\n".join(lines)


_ENV_VAR_KEY = "x-env-var"


def _env_var_section(props: dict[str, Any], defs: dict[str, Any]) -> list[str]:
    """Table of every field that declares its own environment variable.

    The schema is the census: a field declares the name in `json_schema_extra`,
    so this walks the model tree rather than repeating a list the code already
    holds. Nested fields do not appear in the per-section tables below, so
    without this the declaration would ship invisible to every reader.
    """
    rows = sorted(_env_var_rows(props, defs, prefix=""))
    if not rows:
        return []
    lines = [
        "\n## Environment variable overrides\n",
        (
            "These fields read from a fixed environment variable when it is set "
            "and non-empty, so a deployment can supply the value without editing "
            "a config file. An unset or empty variable simply does not override. "
            "`GROVE_<SECTION>__<FIELD>` still wins over the name below, and any "
            "string value can also reference a variable you choose yourself with "
            "`${YOUR_VAR}` — see [the cascade](features-cascade.md).\n"
        ),
        "\n| Field | Variable |",
        "|---|---|",
    ]
    lines.extend(f"| `{path}` | `{variable}` |" for path, variable in rows)
    return lines


def _env_var_rows(
    props: dict[str, Any], defs: dict[str, Any], *, prefix: str
) -> list[tuple[str, str]]:
    """Recurse the schema, collecting `(dotted path, variable)` declarations."""
    rows: list[tuple[str, str]] = []
    for name, node in props.items():
        if name.startswith("$"):
            continue
        info = _resolve(node, defs)
        path = f"{prefix}{name}"
        nested = info.get("properties")
        if isinstance(nested, dict):
            rows.extend(_env_var_rows(nested, defs, prefix=f"{path}."))
            continue
        variable = info.get(_ENV_VAR_KEY)
        if isinstance(variable, str):
            rows.append((path, variable))
    return rows


def _resolve(node: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Pydantic v2 emits `$ref: '#/$defs/Name'` for nested models. Follow it."""
    if "$ref" in node:
        ref_name = node["$ref"].split("/")[-1]
        return defs.get(ref_name, node)
    return node


def _type_str(info: dict[str, Any], defs: dict[str, Any]) -> str:
    if "type" in info:
        t = info["type"]
        if t == "array":
            inner = info.get("items", {})
            return f"array<{_type_str(_resolve(inner, defs), defs)}>"
        if t == "object":
            return "object"
        return t
    if "$ref" in info:
        return info["$ref"].split("/")[-1]
    if "anyOf" in info:
        parts = [_type_str(_resolve(x, defs), defs) for x in info["anyOf"]]
        return " \\| ".join(p for p in parts if p != "null") + (
            " \\| null" if any(p == "null" for p in parts) else ""
        )
    if "enum" in info:
        return " \\| ".join(repr(v) for v in info["enum"])
    return "any"


def _default_str(info: dict[str, Any], parent: dict[str, Any], field: str) -> str:
    if "default" in info:
        return f"`{info['default']!r}`"
    if field in (parent.get("required") or []):
        return "**required**"
    return "(none)"
