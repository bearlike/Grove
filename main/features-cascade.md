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

## Seven layers

| # | Layer | Path or source | Purpose |
|---|---|---|---|
| 1 | **Built-in defaults** | shipped with Grove (Pydantic models) | A workable baseline. |
| 2 | **User**            | `${user_config_dir}/grove/config.json` | Per-user defaults across every repo. |
| 3 | **Project**         | `<repo>/.grove/config.json` | Team baseline. Commit this. |
| 4 | **Project-local**   | `<repo>/.grove/config.local.json` | Per-machine overrides. Gitignored. |
| 5 | **Declared env vars** | a fixed name per field, e.g. `GROVE_GOTIFY_API_URL` | Deployment values for the few fields that name one. |
| 6 | **Env vars**        | `GROVE_<SECTION>__<FIELD>=value` | Quick overrides for a single shell session. |
| 7 | **CLI flags**       | (where applicable) | One-shot overrides. |

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
    AgentSpec(name="claude", command="claude", kind="claude_code"),
    AgentSpec(name="codex",  command="codex",  kind="codex"),
    AgentSpec(name="shell",  command="$SHELL"),
]
```

If your project layer adds:

```json
{ "agents": [{ "name": "aider", "command": "aider --model sonnet" }] }
```

The merged result is all four: the defaults `claude`, `codex`, and `shell`
plus the project's `aider`. New names append in overlay order. Matching names
merge field by field: the overlay's fields win, and the base entry fills
the gaps. Override only the `claude` agent's `command` and its
`kind: "claude_code"` survives, so the [Activity
Dashboard](features-activity.md) keeps tracking it. This is the cascade
principle applied one level deeper, value by value instead of list by
list, and it is what makes the team-vs-individual story work without
forking lists.

When you need to *replace* the defaults, give every entry a custom name.
The merge cannot insert a `claude` you did not ask for.

## Exclusive field pairs override as a group

Plain field-by-field merging works until two fields are mutually
exclusive. `init_script.inline` and `path` are one such pair: a single
layer cannot set both, and the cascade cannot let one layer's `path`
survive next to another layer's `inline` either. For this pair, the
highest layer to set either field wins the whole choice. Grove drops the
other field rather than merging it in, and logs a warning when it does.
See [init scripts](configure-init-scripts.md#inline-and-path-are-mutually-exclusive)
for the full rule.

## Per-repo resolution

The daemon, the TUI, and the web dashboard all resolve each repo's full
cascade independently. The user layer applies to every repo; layers 3 and 4
(project and project-local) apply only for workspaces in that specific repo.
A project-scoped agent or a project-enabled init script defined in
`<repo>/.grove/config.json` is honored consistently across every surface for
that repo. You do not need to duplicate project settings in your user config.

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

## `${VAR}` references pull a value from a variable you name

`GROVE_<SECTION>__<FIELD>` above is an override layer, and the variable
name is fixed by the field's position in the schema. When you want the
value to come from a variable *you* choose, reference it from the config
itself. Any string value can:

```json
{
  "notifications": {
    "gotify": { "enabled": true, "server_url": "${MY_GOTIFY_URL}" }
  }
}
```

A value can be exactly one reference or embed several:
`"http://${GOTIFY_HOST}:${GOTIFY_PORT}"`. References resolve after the
layers merge and before validation, so a resolved value is checked
exactly like a literal one — `"kind": "${MY_AGENT_KIND}"` resolving to
`not-a-kind` fails the same way typing `not-a-kind` would.

**A reference to an unset or empty variable is an error**, and it names
both the variable and the field:

```
Invalid configuration: config: 'notifications.gotify.server_url'
references environment variable 'MY_GOTIFY_URL', which is not set
```

That is deliberate. Writing the reference is how you opt in, so a
missing variable is a mistake worth stopping for. The alternative —
treating it as "not set, carry on" — gives you a config that loads
cleanly with an empty base URL and then sends every notification
nowhere.

Three rules round it out:

- **`$${VAR}` is the escape.** It resolves to a literal `${VAR}` and is
  the only escape.
- **Anything else with a `$` is left alone.** A bare `$`, or `$FOO`
  without braces, is an ordinary string. This is a reference mechanism,
  not shell interpolation.
- **`${repo}` and `${repo_name}` are reserved** for the consume-time
  expansion described above, and pass through untouched.

Use this for non-secret values that differ per machine — a base URL, a
hostname, a path. A **secret** stays on the dedicated `*_env` fields
(`tickets.gitea.token_env`, `notifications.gotify.token_env`,
`mewbo.api_key_env`, and friends). Those fields already hold the *name*
of a variable so the config never holds the credential, and Grove reads
them at the moment it needs the value rather than folding it into the
config object. They are not references and are never resolved here.

## A few fields declare their own variable

A handful of fields name a fixed environment variable in Grove's own
schema. Set it and the field takes that value, with no config file and no
`${VAR}` written anywhere:

```bash
export GROVE_GOTIFY_API_URL=https://gotify.example.com   # notifications.gotify.server_url
export GROVE_GITEA_BASE_URL=https://gitea.example.com    # tickets.gitea.base_url
```

The full list is generated from the schema and lives in the
[configuration reference](configure-reference.md#environment-variable-overrides).
The names are declared per field, not derived from the field's path — read
the exact string off that table rather than reconstructing it.

**An unset or empty declared variable simply does not override.** Nobody
asked for it; it is always on, so silence is the only correct answer and
your config file's value stands. That is the opposite of a `${VAR}`
reference, which errors when its variable is missing — and both are right,
because a reference is something you typed on purpose and a declared
variable is one Grove offers whether you use it or not.

Precedence for one of these fields, lowest to highest:

1. the literal value in a config file
2. a `${VAR}` reference in that file — it *is* the file's value, resolved
3. the field's declared variable, e.g. `GROVE_GOTIFY_API_URL`
4. `GROVE_<SECTION>__<FIELD>`, e.g. `GROVE_NOTIFICATIONS__GOTIFY__SERVER_URL`

3 loses to 4 because 4 names the exact field it fills and is unambiguous
by construction, while 3 is a convenience alias. If you have exported both,
the one that says what it means wins.

Only non-secret fields declare a variable. A credential still goes through
a `*_env` field, which names the variable holding it.

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
