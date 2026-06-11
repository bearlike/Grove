# docs — the published mkdocs site (product + contributor docs)

> ↑ [root](../CLAUDE.md)

The mkdocs site under `docs/`, deployed to the Pages site. It sells Grove and routes readers to depth. Note: `docs/design-system.md` is the TUI **visual contract**, owned by [tui](../src/grove/tui/CLAUDE.md) — cross-link it, never document it here (it is also `exclude_docs`'d from the built site).

## Two audiences, physically separated

**Split product docs from contributor docs; never blur them on one page.** The site nav has two halves: **User Guide** (product/user docs) and **Developer Reference**. Operational/contributor detail for a component — the dashboard dev server (`npm run dev`), wire-type codegen, the test pyramid, BFF route internals — lives in that component's README (e.g. `webapp/README.md`), **not** in the site's User Guide. The site links *out* to the README for contributor work; the README banners *back* to the site for product usage.

User-facing webapp / auth / daemon docs live under `docs/` *Use* (`use-webapp.md`, `use-auth.md`, plus the `grove daemon` / `grove auth` blocks in `use-cli.md`). When a feature spans both audiences, write each half in its own surface and cross-link. Internal flags (`--print-port`-style) get a user-facing gloss on the site (what it does); the implementation rationale (LocalTransport auto-discovery) stays in code comments. This is the doc analogue of the engine's "public surface small, internals private" rule.

## Presentation: screenshots are signal, chrome is noise

**Real product screenshots carry the message; cut decorative chrome.** A skimming reader reads the headline, glances at one product screenshot, then scans section headers — they do not read paragraphs first. So icon-tile galleries, status-chip walls, pill rows, and numbered-lifecycle strips are noise; remove them. The landing page sells and routes; depth lives in the User Guide. Erring toward "comprehensive" on the home page reads as bloat — restraint is the quality signal.

## Writing style for every line on the site

(Learned from peer products like superconductor.com and direct user feedback. Applies to published prose; `docs/design-system.md` is `exclude_docs`'d and exempt.)

- **No em dashes.** Use a period, comma, colon, or parentheses instead.
- **Short, well-decomposed sentences with narrative continuity.** Not choppy fragments, and not long dash-chained clauses either.
- **Explain with an analogy a developer already knows.** A workspace is a private workbench; the BFF is a receptionist at a front desk; the config cascade is a team `.editorconfig`.
- **Seed a memorable, repeatable phrase** that recurs to aid recall: "one agent, one worktree, one window"; "your git stays yours".

## Theme: reuse the landing-page kit, never hand-roll marketing CSS

The `mkdocs-shadcn-mewbo` theme (≥ 1.2) ships a full landing-page kit. Build pages from its primitives, not bespoke markup:

- `.ms-hero` (`__eyebrow`/`__title`/`__lede`), `.ms-cta-row` + `.ms-btn--primary|secondary|ghost`
- `.ms-pills`/`.ms-pill`, `.ms-grid--3|4|5` + `.ms-card` (`__icon`/`__title`/`__body`; `<a class="ms-card">` for clickable cards)
- `.ms-lifecycle`/`.ms-step` (auto-numbered via CSS counter), `.ms-chips`/`.ms-chip`
- `.ms-shot` captioned screenshot figure: `<figure class="ms-shot"><div class="ms-shot__frame"><img …></div><figcaption class="ms-shot__body">…</figcaption></figure>` (frame tint via `--ms-shot-frame`)
- `.ms-devices` side-by-side device pair (widths via `--ms-devices-primary|secondary`; caption is a `.ms-devices__caption` child)

**Icons:** the theme loads the Iconify runtime from a CDN in its `main.html`, so `<iconify-icon icon="lucide:NAME"></iconify-icon>` renders anywhere in content. Section headings get an auto-injected icon via `## Heading { .ms-h2-icon data-icon="KEY" }`, where `icon-inject.js` maps KEY ∈ {target, flow, plug, grid, route, book, star} → a lucide glyph (other keys fall back to a circle).

**Grove-only bespoke bits**: only the status chip gallery (`.grove-status-grid`/`.grove-status-chip`) survives in `docs/stylesheets/grove.css` (wired via `extra_css`) — it mirrors the TUI status palette and was deliberately not generalized into the theme. Everything else was upstreamed in theme v1.2 (issue #8); do not re-fork.

**Figures use raw HTML `<img>` with hand-written relative paths** (`../img/…` from any sub-page, `img/…` from `index.md`), because mkdocs only rewrites paths in markdown image syntax. Keep other component blocks raw HTML WITHOUT the `md_in_html` `markdown` attribute: adding `markdown` makes python-markdown wrap loose inline children in `<p>`, which breaks the flex/grid child structure the kit's CSS expects.

**Validate with `make docs-build`** (CI-parity `--strict`). For a real visual check, headless-Chrome-screenshot the built `site/`: `python3 -m http.server` in `site/`, then `chrome --headless=new --virtual-time-budget=12000 --screenshot=… http://127.0.0.1:PORT/index.html`. The Playwright MCP needs an X server and won't launch headless here.

## Branding is `site_name`-driven; no template overrides

Since theme v1.2, the tab `<title>` (`"<Page> · Grove"`), the Ask AI greeting and avatar, and the WebMCP tool name all derive from `site_name` — the old `overrides/` forks are gone and `theme.custom_dir` is unset. Do not reintroduce template copies for branding. The favicon and header logo both resolve to `docs/logos/grove-logo.png` via `theme.favicon` and `theme.icon`. One temporary shim remains: `docs/hooks/og_site_name.py` injects `og:site_name` (theme 1.2.1 guards it with a bare `site_name`, which never renders — upstream bug mewbo-com/mkdocs-shadcn#1); the hook no-ops and can be deleted once the fix ships.

## The docs pipeline rides the mirror split

`.github/workflows/docs.yml` deploys from `main` on every push and picks its target at runtime from `github.server_url`: github.com publishes versioned GitHub Pages via mike (`main` + `latest` aliases), anything else uploads a static build to the internal docs host named by the repo variables `DOCS_HOST`/`DOCS_RESOLVE_IP` (set per-mirror, never committed). PR previews deploy as `pr-<N>` on both sides; `docs-cleanup.yml` removes them on github.com, the `cleanup` job in `docs.yml` removes them on the internal host. The theme wheel's `latest-master` URL embeds the version in the FILENAME — when it 404s, the asset rotated: read the current name off the release page, update `pyproject.toml`, re-lock.

## Session lessons

- Lessons specific to this site land here. Cross-cutting facts go to [root](../CLAUDE.md); TUI visual contract to `docs/design-system.md` (owned by [tui](../src/grove/tui/CLAUDE.md)); webapp to [webapp](../webapp/CLAUDE.md).
- **Webapp doc screenshots come from a fully synthetic demo fleet, never the live daemon (2026-06-11).** The live daemon serves real (private) repos, so screenshot against a sandbox: `XDG_CONFIG_HOME`/`XDG_STATE_HOME`/`CLAUDE_CONFIG_DIR` pointed at a temp dir, fictional repos under `/tmp`, workspaces created via `grove.core.build(...).create(...)` (no auth needed in-process), and Claude Code transcripts hand-planted under `$CLAUDE_CONFIG_DIR/projects/<encoded-cwd>/<minted-session-id>.jsonl` (format: `tests/core/agents/fixtures/basic.jsonl`; tail `stop_reason: tool_use` reads as working, `end_turn` as waiting). A second `grove daemon serve --port <alt>` plus `next start -p <alt>` with `GROVE_DAEMON_URL` reuses the existing `.next` build; pair a headless Playwright (`webapp/node_modules/@playwright/test`) through the real `/login` flow and approve with `grove auth approve` under the sandbox env. Engine `mgr.kill()` per workspace plus removing the temp dir tears it all down; the tmux server is shared with the host, so verify `tmux ls` is clean.
- **`exclude_docs` must list `CLAUDE.md`** — strict builds break on its relative `../` links otherwise (bit us when the CLAUDE.md tree split landed without it).
