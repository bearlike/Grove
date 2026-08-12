"""The two-badge description footer — pure render plus a marker-delimited splice.

Zero I/O by construction (the module has none): every test here is a string in,
a string out, matching the publisher's pure-render conventions. What the suite
pins is the two properties a body rewrite lives or dies by — the human's text
survives byte for byte, and re-splicing reproduces the previous bytes exactly.
"""

from __future__ import annotations

from grove.core.issueops.footer import (
    FOOTER_BEGIN,
    FOOTER_END,
    STATUS_BADGE_URL,
    WORKSPACE_BADGE_URL,
    footer_needs_update,
    render_footer,
    splice_footer,
)
from grove.core.issueops.marker import SIGNATURE_MARKER, STICKY_MARKER

_WS = "https://grove.example.com/w/ws1"
_STATUS = "https://git.example.com/acme/proj/issues/42#issuecomment-1234"
_HUMAN = "The auth flow drops the refresh token.\n\n- [ ] reproduce\n- [ ] fix"


def _footer(*, workspace_url: str | None = _WS, status_url: str | None = _STATUS) -> str:
    return render_footer(workspace_url=workspace_url, status_url=status_url)


# ─── pure render ─────────────────────────────────────────────────────────────


def test_render_links_both_hosted_badges_on_one_line() -> None:
    """Two halves of one affordance — go to the work — so they read as one
    control. The badges are referenced at their hosted urls, never vendored."""
    footer = _footer()
    assert footer == (
        "---\n\n"
        f"[![Open In: Grove Workspace]({WORKSPACE_BADGE_URL})]({_WS}) "
        f"[![Scroll to: Grove Status]({STATUS_BADGE_URL})]({_STATUS})"
    )
    assert footer.count("\n\n") == 1


def test_the_alt_text_matches_each_badges_own_aria_label() -> None:
    """The image is the whole content, so its alt text is what a screen reader
    and a broken-image render have to work from. It says what the badge says."""
    assert "[![Open In: Grove Workspace]" in _footer()
    assert "[![Scroll to: Grove Status]" in _footer()


def test_a_badge_with_no_destination_is_not_rendered_at_all() -> None:
    """A badge is a button drawn as an image: unlinked it has no plain-text
    reading to degrade to, so it is a control that does nothing. Dropping it is
    the same rule the sticky comment's `_link` follows one step further."""
    no_workspace = _footer(workspace_url=None)
    assert WORKSPACE_BADGE_URL not in no_workspace
    assert STATUS_BADGE_URL in no_workspace

    no_status = _footer(status_url=None)
    assert STATUS_BADGE_URL not in no_status
    assert WORKSPACE_BADGE_URL in no_status


def test_neither_destination_renders_nothing_at_all() -> None:
    """Not a rule, a horizontal rule, or an empty region — a footer with no
    destinations has nothing to say, and `splice_footer` reads "" as "remove"."""
    assert _footer(workspace_url=None, status_url=None) == ""


# ─── splice: the human's text is not ours ────────────────────────────────────


def test_splice_appends_the_region_and_preserves_the_human_text() -> None:
    spliced = splice_footer(_HUMAN, _footer())
    assert spliced.startswith(_HUMAN)
    assert FOOTER_BEGIN in spliced and FOOTER_END in spliced
    assert WORKSPACE_BADGE_URL in spliced


def test_the_human_text_survives_a_splice_byte_for_byte() -> None:
    """Everything outside the markers is somebody else's writing. Whatever the
    footer does, the text above it comes back out unchanged."""
    body = "# Bug\n\nA `|` pipe, a <!-- comment -->, a ```fence```, and an emoji 🌿."
    outside = splice_footer(body, _footer()).split(FOOTER_BEGIN)[0]
    assert outside.rstrip() == body


def test_splicing_twice_yields_identical_bytes() -> None:
    once = splice_footer(_HUMAN, _footer())
    assert splice_footer(once, _footer()) == once


def test_splicing_a_body_that_is_already_only_a_footer_is_stable() -> None:
    """The degenerate body — a description a human left empty. No leading blank
    lines, and still idempotent."""
    once = splice_footer("", _footer())
    assert once.startswith(FOOTER_BEGIN)
    assert splice_footer(once, _footer()) == once


def test_a_stale_link_is_rewritten_in_place_and_never_duplicated() -> None:
    """The reason the region is delimited at all: a workspace is recreated, the
    deep link moves, and the footer has to REPLACE rather than accumulate."""
    old = splice_footer(_HUMAN, _footer(workspace_url="https://grove.example.com/w/OLD"))
    new = splice_footer(old, _footer())

    assert new.count(FOOTER_BEGIN) == 1
    assert new.count(WORKSPACE_BADGE_URL) == 1
    assert "/w/OLD" not in new
    assert _WS in new
    assert new.startswith(_HUMAN)


def test_an_empty_footer_removes_the_region_and_leaves_the_body() -> None:
    """Retract rather than go stale: a workspace whose deep link and sticky
    comment are both gone leaves the description as the human wrote it."""
    with_footer = splice_footer(_HUMAN, _footer())
    assert splice_footer(with_footer, "") == _HUMAN


def test_the_human_text_below_a_footer_is_preserved_too() -> None:
    """A human editing the description can write below Grove's region. The
    splice replaces only the region, so their text keeps its position."""
    body = f"{_HUMAN}\n\n{FOOTER_BEGIN}\n\nold\n\n{FOOTER_END}\n\nAdded later by a human."
    spliced = splice_footer(body, _footer())
    assert spliced.startswith(_HUMAN)
    assert spliced.endswith("Added later by a human.")
    assert "old" not in spliced


def test_a_blank_line_separates_the_rule_so_it_stays_a_rule() -> None:
    """Markdown reads `text\\n---` as a SETEXT HEADING, so a missing blank line
    would silently promote the human's last paragraph to an <h2> rather than
    draw the divider. The separator is correctness, not spacing."""
    spliced = splice_footer("Just one line.", _footer())
    assert "Just one line.\n\n" in spliced
    assert "Just one line.\n---" not in spliced


# ─── splice: markdown that could collide with the markers ────────────────────


def test_a_body_quoting_the_marker_in_a_code_fence_is_not_swallowed() -> None:
    """The right-anchored scan is what protects this. A left-anchored one would
    pair the quoted opening marker with Grove's real closing marker and eat every
    line between — including the documentation that quoted it."""
    doc = f"How it works:\n\n```html\n{FOOTER_BEGIN}\n```\n\nEnd of explanation."
    spliced = splice_footer(doc, _footer())

    assert "End of explanation." in spliced
    assert "```html" in spliced
    assert spliced.count(FOOTER_END) == 1
    # The quoted marker is left exactly where the human put it; Grove's own
    # region is appended after it rather than growing backwards to reach it.
    assert spliced.index("End of explanation.") < spliced.rindex(FOOTER_BEGIN)


def test_an_unpaired_closing_marker_appends_rather_than_guessing() -> None:
    """A half-written region (a truncated write, a hand edit) is not a region.
    Grove appends a well-formed one and leaves the stray marker alone."""
    body = f"{_HUMAN}\n\n{FOOTER_END}"
    spliced = splice_footer(body, _footer())
    assert spliced.count(FOOTER_BEGIN) == 1
    assert spliced.count(FOOTER_END) == 2
    assert spliced.startswith(_HUMAN)


def test_other_markdown_that_looks_like_our_delimiters_is_untouched() -> None:
    """A horizontal rule, an HTML comment and a badge of the human's own are all
    ordinary body text — nothing here is matched by anything but the markers."""
    body = "---\n\n<!-- note to self -->\n\n[![CI](https://img.example/ci.svg)](https://ci)"
    spliced = splice_footer(body, _footer())
    assert spliced.startswith(body)
    assert splice_footer(spliced, _footer()) == spliced


def test_the_footer_markers_cannot_collide_with_the_comment_markers() -> None:
    """Disjoint as SUBSTRINGS in both directions, so a body scan and a comment
    scan can never match each other's artifact. A description reaches neither
    scan today; this is what keeps that true if one is ever pointed at a body."""
    for comment_marker in (SIGNATURE_MARKER, STICKY_MARKER):
        for footer_marker in (FOOTER_BEGIN, FOOTER_END):
            assert comment_marker not in footer_marker
            assert footer_marker not in comment_marker


# ─── the no-op predicate (skip the network write) ────────────────────────────


def test_a_body_already_carrying_this_footer_needs_no_update() -> None:
    spliced = splice_footer(_HUMAN, _footer())
    assert footer_needs_update(spliced, _footer()) is False


def test_a_missing_or_stale_footer_needs_an_update() -> None:
    assert footer_needs_update(_HUMAN, _footer()) is True

    stale = splice_footer(_HUMAN, _footer(status_url="https://old/#issuecomment-1"))
    assert footer_needs_update(stale, _footer()) is True


def test_a_body_with_no_footer_and_nothing_to_write_needs_no_update() -> None:
    """Both degraded cases at once: no deep link and no sticky comment on a
    description that never had a footer is not a write."""
    assert footer_needs_update(_HUMAN, "") is False


def test_removing_a_footer_counts_as_an_update() -> None:
    """The retraction has to reach the forge, or a dead link stands forever."""
    spliced = splice_footer(_HUMAN, _footer())
    assert footer_needs_update(spliced, "") is True
