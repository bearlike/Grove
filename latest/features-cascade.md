# Configuration cascade

Grove provides mechanism and leaves policy to the user. Defaults are
sensible but held lightly. Any value a developer might reasonably want
to change is reachable from outside the code. Layers stack so a team can
pin a shared standard while an individual still owns the last word on
their own setup.

## Mechanism, not policy

Three properties distinguish the cascade from a flat config file:

- **Layered overrides.** Each later layer overrides the earlier layer.
- **No layer is mandatory.** If a layer is absent, Grove skips it and
  the next layer wins. The defaults at the bottom are enough to run.
- **Last-wins, with one exception.** Lists usually replace wholesale.
  `agents` merges by `name`, so the team's list and the individual's
  list compose without one wiping the other.

## Six layers

| # | Layer | Path or source | Purpose |
|---|---|---|---|
| 1 | **Built-in defaults** | shipped with Grove (Pydantic models) | A workable baseline. |
| 2 | **User**            | `${user_config_dir}/grove/config.json` | Per-user defaults across every repo. |
| 3 | **Project**         | `<repo>/.grove/config.json` | Team baseline. Commit this. |
| 4 | **Project-local**   | `<repo>/.grove/config.local.json` | Per-machine overrides. Gitignored. |
| 5 | **Env vars**        | `GROVE_<SECTION>__<FIELD>=value` | Quick overrides for a single shell session. |
| 6 | **CLI flags**       | (where applicable) | One-shot overrides. |

`${user_config_dir}` follows `platformdirs`: XDG on Linux, `%APPDATA%` on
Windows, `~/Library/Application Support` on macOS.

The merge runs every time Grove resolves config (once per CLI invocation,
once per TUI launch). The result is a fully validated `GroveConfig`
object. Pydantic validates once, at the single boundary, with
`extra="forbid"` so typos in any layer raise loudly.

## Lists merge by name (only `agents`)

Every other list (`ui.keybindings`, for example) replaces wholesale
across layers. `agents` is special:

```python
[
    AgentSpec(name="claude", command="claude"),
    AgentSpec(name="shell",  command="$SHELL"),
]
```

If your project layer adds:

```json
{ "agents": [{ "name": "aider", "command": "aider --model sonnet" }] }
```

The merged result is all three: the defaults `claude` and `shell` plus
the project's `aider`. Same key (`name`) replaces. New key appends in
overlay order. This is what makes the team-vs-individual story work
without forking lists.

When you need to *replace* the defaults, give every entry a custom name.
The merge cannot insert a `claude` you did not ask for.

## `${repo}` and `${repo_name}` expand at consume time

Path-shaped config fields can carry placeholders:

```json
{
  "worktree": {
    "root_template": "${repo}/.worktrees"
  }
}
```

`${repo}` is the absolute path to the repo root. `${repo_name}` is its
basename. The substitution happens at consume time, when a manager
method needs the value, not at validate time. The same global config
then serves every repo without re-validation.

A raw `~` for the user home directory works too. `Path.expanduser`
resolves it the same way.

## Environment variables

`GROVE_<SECTION>__<FIELD>=value` overrides a single field. Double
underscore separates nesting depth. Field names are lowercase:

```bash
GROVE_TMUX__HISTORY_LIMIT=100000 grove        # one-shot, this invocation only
GROVE_UI__THEME=light grove
GROVE_INIT_SCRIPT__ENABLED=true grove
```

Values stay strings until Pydantic validates them. Coercion to int,
bool, etc. happens at the boundary.

## Worked example

A team agrees that every workspace should run `uv sync` first and that
the worktree root should sit beside the repo.

`<repo>/.grove/config.json` (committed):

```json
{
  "worktree": { "root_template": "${repo}/.worktrees" },
  "init_script": {
    "enabled": true,
    "inline": "uv sync",
    "timeout_seconds": 180
  }
}
```

A teammate prefers the dark theme. They drop:

`<repo>/.grove/config.local.json` (gitignored):

```json
{ "ui": { "theme": "dark" } }
```

Another teammate wants Aider for one workspace without disturbing the
team's `claude` default. They add to their user layer:

`~/.config/grove/config.json` (per-machine, all repos):

```json
{ "agents": [{ "name": "aider", "command": "aider --model sonnet" }] }
```

The merged config carries all three pieces. No coordination needed. No
list got wiped. The team baseline is intact.

## See also

- [Project setup](configure-project.md): where each file goes.
- [Agents](configure-agents.md): the merge-by-name rule in action.
- [Configuration reference](configure-reference.md): every field, auto-generated from the model.
