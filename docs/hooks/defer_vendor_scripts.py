"""Turn a page's third-party CDN scripts and styles into idle-time prefetches.

The theme emits one fixed script tail on every page: Mermaid (3.5 MB), Swiper,
Viewer.js, Marked, Tablesort, all fetched and executed from public CDNs while
the page loads. The documentation pages use them. A page that opts out of the
docs chrome, like the landing page, uses none of them, yet paid for all of
them on first paint, on the same connection and main thread as its own scene.

A page opts in with front matter::

    defer_vendor_scripts: true

and every cross-origin ``<script src>`` and stylesheet ``<link>`` on it becomes
a ``<link rel="prefetch">``. Prefetches are fetched at idle priority and never
executed, so the landing page stops competing with them while the browser's
HTTP cache is warmed for the documentation page the visitor opens next, which
is what the landing page exists to lead to.

The theme's own companion scripts (``carousel.js``, ``lightbox.js`` and the
rest) stay in place. Each already checks that its library is present before
doing anything, so on an opted-in page they are inert rather than broken.
"""

from __future__ import annotations

import re
from typing import Any

#: A cross-origin script element, including its (empty) body.
_SCRIPT = re.compile(r'<script\b[^>]*\bsrc="(https?://[^"]+)"[^>]*>\s*</script>')
#: A cross-origin stylesheet link.
_STYLESHEET = re.compile(
    r'<link\b(?=[^>]*\brel="stylesheet")[^>]*\bhref="(https?://[^"]+)"[^>]*/?>'
)


def defer(html: str) -> str:
    """Rewrite every cross-origin script and stylesheet in ``html`` to a prefetch.

    No ``crossorigin`` attribute: the documentation pages request these without
    one, and a prefetch only warms the cache entry a later request can reuse
    when both are made in the same CORS mode.
    """
    html = _SCRIPT.sub(lambda match: f'<link rel="prefetch" href="{match[1]}">', html)
    return _STYLESHEET.sub(lambda match: f'<link rel="prefetch" href="{match[1]}">', html)


def on_post_page(output: str, page: Any, **_: Any) -> str:
    if page.meta.get("defer_vendor_scripts"):
        return defer(output)
    return output
