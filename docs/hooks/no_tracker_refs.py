"""Fail the build when a bare tracker reference reaches a published page.

The site deploys from the public GitHub mirror, where a bare ``#<n>`` resolves
against a **different** tracker than the private one the number came from: the
reader is pointed at an unrelated issue, and the shape of a private issue graph
is published alongside it. The repo already avoids this in PR titles and bodies;
this closes the same hazard on the docs.

Asserted over the RENDERED page content rather than over any one source, because
prose reaches this site through four unrelated doors -- hand-authored markdown,
the JSON-schema config reference (``schema_to_md.py``), mkdocstrings docstrings,
and any future generator. A guard on one door is a guard on none.

**A ref inside a code span is a deliberate literal, not archaeology.** The
ticket-ref grammar is documented with ``#42`` as an example value, so an inline
``<code>`` span and a fenced block are both spared -- the latter also carries
mermaid hex colours (``#111111``), which are not references at all. Only prose
is flagged, plus a code block inside a ``<details>``: that is what mkdocstrings
emits for ``show_source``, which republishes every inline comment in a module
and is deliberately off (see ``mkdocs.yml``).

The fix for a failure is always in the prose, never here -- describe the
behaviour and leave the archaeology to ``CLAUDE.md``, which is written for
maintainers and is not published. Where the number really is the point, wrap it
in backticks so it reads as the literal it is.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from mkdocs.exceptions import PluginError

#: A bare ``#<n>`` -- an issue reference with no owner/repo qualification. The
#: lookbehind spares ``#!``-style prefixes and anything already qualified as a
#: URL path (``.../issues/241``), which are unambiguous wherever they are read.
_BARE_ISSUE_REF = re.compile(r"(?<![\w/])#\d+")

_CODE_TAGS = frozenset({"code", "pre"})


class _ProseRefScanner(HTMLParser):
    """Collect bare tracker refs from a page's prose, sparing code spans."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._code_depth = 0
        self._details_depth = 0
        self.hits: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _CODE_TAGS:
            self._code_depth += 1
        elif tag == "details":
            self._details_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _CODE_TAGS:
            self._code_depth = max(0, self._code_depth - 1)
        elif tag == "details":
            self._details_depth = max(0, self._details_depth - 1)

    def handle_data(self, data: str) -> None:
        # Code spares the ref (a deliberate literal) UNLESS it sits in a
        # <details>, which is how mkdocstrings republishes module source.
        if self._code_depth and not self._details_depth:
            return
        for ref in _BARE_ISSUE_REF.findall(data):
            self.hits.append((ref, " ".join(data.split())[:120]))


def on_page_content(html: str, *, page: Any, **kwargs: Any) -> str:
    """Scan one page's rendered content; abort the build on any bare ref.

    ``on_page_content`` rather than ``on_post_page`` so the theme's own chrome
    (nav, footer, search payload) is out of scope -- this is the page's own
    prose, after every generator has already run.
    """
    scanner = _ProseRefScanner()
    scanner.feed(html)
    scanner.close()
    if not scanner.hits:
        return html
    found = "\n".join(f"  {ref} in: {context}" for ref, context in scanner.hits)
    raise PluginError(
        f"{page.file.src_uri}: bare tracker reference(s) in published prose.\n"
        f"{found}\n"
        "The site deploys from the public mirror, where a bare #<n> links to an "
        "unrelated tracker and publishes a private issue graph. Rewrite the "
        "prose as behaviour (the archaeology belongs in CLAUDE.md), or wrap the "
        "number in backticks when the literal itself is the point."
    )
