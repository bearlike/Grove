"""The two-badge footer Grove upserts into a ticket's own DESCRIPTION.

Pure render plus a marker-delimited splice, and nothing else — the caller reads
the body, splices, and writes the whole result back through the provider's
``read_body``/``update_body`` pair, only when :func:`footer_needs_update` says
the bytes actually moved.

**The footer is a whole-region REPLACE, never an append.** Both forges model a
description as one blind whole-body write, so keeping what a human wrote means
reading, splicing and passing the WHOLE result back — and the region has to be
findable again or every update leaves another copy behind. HTML comments are
invisible in rendered markdown and trivially substring-matched, the same
mechanism (and the same reasoning) the sticky status comment's markers use.

**Why the delimiters live here rather than in** :mod:`grove.core.issueops.marker`.
That module exists for constants BOTH issue-ops faces import while neither may
depend on the other: the engine's anti-loop guard and the publisher's cold-start
recovery share one comment signature, so it has to sit somewhere neither owns.
This pair is the opposite case — one producer and one consumer, both in this
file, and nothing else in the tree scans a body for them. A constant with a
single owner belongs with its owner; promoting it into the shared module would
claim "another face reads this", which is false, and would grow the one module
whose whole justification is that it holds only what is genuinely shared.

The two vocabularies are nevertheless kept DISJOINT as substrings, so a scan for
``SIGNATURE_MARKER`` or ``STICKY_MARKER`` can never match a footer and a scan
for a footer can never match a Grove comment. A description is not a comment and
reaches neither scan today; disjointness is what keeps that true the day one of
them is pointed at a body.
"""

from __future__ import annotations

FOOTER_BEGIN = "<!-- grove:issue-ops:footer -->"
"""Opens the region Grove owns inside somebody else's description.

Paired rather than single because the footer is REPLACED in place: a lone
signature says "Grove wrote this body", which is never true — Grove wrote the
last few lines of a body a human owns.
"""

FOOTER_END = "<!-- /grove:issue-ops:footer -->"
"""Closes the region. Everything outside the pair is the human's, byte for byte."""

WORKSPACE_BADGE_URL = "https://cdn.thekrishna.in/img/badges/open_in-grove_workspace-grey.svg"
"""Hosted badge for the workspace deep link. Referenced, never vendored."""

STATUS_BADGE_URL = "https://cdn.thekrishna.in/img/badges/scroll_to-grove_status-grey.svg"
"""Hosted badge for the sticky status comment's anchor. Referenced, never vendored."""


def render_footer(*, workspace_url: str | None = None, status_url: str | None = None) -> str:
    """The footer's visible markdown: a horizontal rule, then a row of badges.

    ``workspace_url`` is the Grove deep link for the workspace. ``status_url`` is
    the anchor of the sticky status COMMENT — ``<ticket_url>#issuecomment-<id>``,
    one grammar for issues and pull requests alike, since ``/issues/<n>``
    redirects to ``/pulls/<n>``. Both are composed by the caller, which is the
    only layer holding a ticket url and a comment id.

    **A badge with no destination is not rendered at all.** The repo's rule is
    that a link landing nowhere is worse than the plain text it replaced, and a
    badge has no plain-text reading to degrade to: its whole content is a call to
    action ("Open In: …", "Scroll to: …") drawn as a button, so unlinked it is a
    button that does nothing — the reader spends a click to learn that. So an
    unconfigured deep-link base drops the workspace badge, a ticket with no
    sticky comment yet drops the status one, and neither destination returns
    ``""``, which :func:`splice_footer` reads as "remove the region". That is
    what makes the degraded cases self-healing in both directions: a footer
    retracts rather than going stale, and the next splice restores it whole.

    **One line, not two.** The badges are two halves of one affordance — go to
    the work — so a row reads as one control where a stack reads as a list of
    unrelated links and takes twice the vertical space out of a description
    somebody else wrote. It also degrades without a seam: one badge missing
    makes the row shorter, where a stack would leave a gap.
    """
    badges = [
        f"[![{alt}]({badge})]({target})"
        for alt, badge, target in (
            ("Open In: Grove Workspace", WORKSPACE_BADGE_URL, workspace_url),
            ("Scroll to: Grove Status", STATUS_BADGE_URL, status_url),
        )
        if target
    ]
    if not badges:
        return ""
    return "---\n\n" + " ".join(badges)


def splice_footer(body: str, footer: str) -> str:
    """``body`` with the footer region inserted, replaced, or removed.

    Returns the WHOLE new body, because that is what a description write takes.
    Idempotent by construction — splicing the same footer twice yields identical
    bytes, so a caller may safely re-run it over its own output. An empty
    ``footer`` removes the region entirely rather than leaving an empty one.

    Everything outside the region survives verbatim, with one deliberate
    exception: whitespace at the very end of the human's text is normalized,
    because a fixed separator is exactly what makes the second splice reproduce
    the first. That separator is load-bearing rather than cosmetic — markdown
    reads ``text\\n---`` as a setext heading, so the blank line before the rule
    is what keeps the human's last paragraph a paragraph instead of silently
    promoting it to an ``<h2>``.

    The region is located by scanning for :data:`FOOTER_END` from the RIGHT and
    then the nearest :data:`FOOTER_BEGIN` before it. Scanning from the left would
    let one stray marker — a human's edit, a truncated write, an example quoted
    in a code fence — pair with Grove's own closing marker and swallow every line
    between them, which is precisely the text this function exists to protect.
    Grove always writes its region last, so the rightmost well-formed pair is
    always the real one, and a stray marker is left where it lies rather than
    guessed at.
    """
    head, tail = _outside(body)
    block = f"{FOOTER_BEGIN}\n\n{footer.strip()}\n\n{FOOTER_END}" if footer.strip() else ""
    return "\n\n".join(part for part in (head, block, tail) if part)


def footer_needs_update(body: str, footer: str) -> bool:
    """Would splicing ``footer`` into ``body`` change anything?

    The skip a caller needs before a network write: a forge rate-limits, and a
    rewrite that produces the bytes already stored still spends that budget and
    still registers as an edit on a description somebody else owns.

    Deliberately defined AS the splice rather than by scanning the body for the
    markers or for the urls. Any independent implementation is a second
    definition of "changed", and both drift directions are silent — one writes on
    every flush, the other stops updating a footer whose link went stale. The
    splice is pure string work, so computing it twice costs nothing next to the
    request it is deciding about.
    """
    return splice_footer(body, footer) != body


def _outside(body: str) -> tuple[str, str]:
    """The human's text either side of the footer region, region excluded.

    ``head`` is right-stripped and ``tail`` stripped so the caller's fixed
    ``\\n\\n`` join is reproducible; a marker pair that is not well formed yields
    the whole body as ``head``, which appends a fresh region and leaves the stray
    marker untouched.
    """
    end = body.rfind(FOOTER_END)
    begin = body.rfind(FOOTER_BEGIN, 0, end) if end != -1 else -1
    if begin == -1:
        return body.rstrip(), ""
    return body[:begin].rstrip(), body[end + len(FOOTER_END) :].strip()


__all__ = [
    "FOOTER_BEGIN",
    "FOOTER_END",
    "STATUS_BADGE_URL",
    "WORKSPACE_BADGE_URL",
    "footer_needs_update",
    "render_footer",
    "splice_footer",
]
