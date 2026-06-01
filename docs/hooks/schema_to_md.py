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
        "# Configuration reference\n",
        (
            "This page is generated from Grove's Pydantic model.  Edit "
            "`src/grove/core/config.py` and run `make docs` (or push to "
            "the default branch, which regenerates in CI) to refresh.\n"
        ),
    ]

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
        return " \\| ".join(p for p in parts if p != "null") + (" \\| null" if any(p == "null" for p in parts) else "")
    if "enum" in info:
        return " \\| ".join(repr(v) for v in info["enum"])
    return "any"


def _default_str(info: dict[str, Any], parent: dict[str, Any], field: str) -> str:
    if "default" in info:
        return f"`{info['default']!r}`"
    if field in (parent.get("required") or []):
        return "**required**"
    return "(none)"
