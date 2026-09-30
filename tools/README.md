# tools

Developer and documentation tooling: scripts that produce committed artifacts.
None of it ships in the `grove` package. CI component: **tooling** (tests in
`tests/tools/`).

```
tools/
├── screenshots/            capture the TUI and webapp for the docs (see screenshots/README.md)
├── app_icons.py            render every app icon from the logo source
├── support_icons.py        render the support icon set
├── refine_support_icons.py post-process those icons
└── landing_aura.py         generate the landing page's background aura
```

The Makefile is the entry point: `make docs-capture` (every screenshot,
from one seeded demo world), `make app-icons`. On CI, screenshots run in
their own workflow, `.github/workflows/screenshots.yml`.
