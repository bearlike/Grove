"""Inject ``og:site_name`` until the theme renders it itself.

``mkdocs-shadcn-mewbo`` 1.2.1 guards the tag with a bare ``{% if site_name %}``
(undefined in the mkdocs template context; should be ``config.site_name``), so
the meta never renders. The theme's ``webmcp.js`` derives its tool name from
this tag, falling back to ``search_docs`` instead of ``search_grove_docs``.
This hook adds the tag right after ``<title>`` only when it is missing, so it
becomes a silent no-op (and can be deleted) once the theme fix ships.
"""

from __future__ import annotations

from typing import Any

_MARK = 'property="og:site_name"'


def on_post_page(output: str, *, config: Any, **kwargs: Any) -> str:
    if _MARK in output:
        return output
    tag = f'<meta property="og:site_name" content="{config["site_name"]}">'
    head, sep, tail = output.partition("</title>")
    if not sep:
        return output
    return f"{head}{sep}\n{tag}{tail}"
