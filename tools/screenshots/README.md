# tools/screenshots

Captures the TUI and webapp for the documentation. Everything runs against a
synthetic demo world, never the machine it runs on: real git repos, real
worktrees and real tmux sessions, plus planted agent transcripts.

```
screenshots/
├── capture.py         the one capture entry point: seed once, shoot TUI + web
                       (make docs-capture; docs-screenshots / docs-webapp-screenshots for one half)
├── frame.py           composites webapp shots into device frames
├── slideshow.py       builds the web tour GIF from the framed stills
├── mockups.py         landing-page device mockups
├── fixture/           the demo world: repos, workspaces, transcripts (demo.json)
├── planter/           writes the fixture to disk: fleet, Claude/Codex history, usage
├── driver/            runs the apps: sandbox, TUI and webapp drivers, SVG→PNG raster
├── agents/            stub agent CLIs the demo workspaces launch
├── assets/            wallpaper for framed shots
└── device-shells/     device frame images
```

**Everything runs inside a sandbox.** `driver/sandbox.py` redirects `HOME`,
XDG dirs and the agent config dirs before any grove import, so no real profile
is ever read. Anything that finds its own install through `HOME` must be pinned
first; `PLAYWRIGHT_BROWSERS_PATH` is. `GROVE_SHOTS_ROOT` moves the sandbox,
which CI needs because its `/tmp` is RAM-backed.

On CI this runs in `.github/workflows/screenshots.yml` on every push to `main`.
Tests are in `tests/tools/screenshots/`.
