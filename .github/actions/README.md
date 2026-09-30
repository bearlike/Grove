# Shared CI actions

Composite actions the workflows use for setup, so each fix lives in one place.

```
actions/
└── setup-python-env/
    └── action.yml   install uv into the job's own tool cache, then `uv sync --frozen`
```

**Every job that needs Python goes through `setup-python-env`, never a bare
`astral-sh/setup-uv`.** The runners mount one shared tool-cache volume into every
job, and two setup-uv steps writing it at once corrupt it. That race is what
used to force CI into a chain. The action points `RUNNER_TOOL_CACHE` at the
job's own temp dir so jobs can run side by side, and
`tests/test_ci_components.py` fails on any step that skips it.

Inputs: `groups` (default `dev`), the dependency groups to sync, space separated.
