# Configuration cascade

## How the layers resolve

Grove provides mechanism and leaves policy to you. Any value worth
changing is reachable from outside the code, and layers stack so a team
pins a shared standard while you still own the last word on your setup.

## Mechanism, not policy

Three properties set the cascade apart:

- **Layered overrides.** Each later layer overrides the earlier one.
- **No layer is mandatory.** An absent layer is skipped for the next.
- **Last-wins, with one exception.** Lists replace wholesale, but
  `agents` merges by `name`, so a team's list and an individual's list
  compose.

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

The merge runs once per CLI invocation and once per TUI launch. Pydantic
validates the result once, with `extra="forbid"`, so a typo in any layer
raises loudly.

## Workspace defaults put the user first

`defaults` is the one exception to Grove's project-last rule. A workspace
form is a private workbench: the repository can suggest its usual setup, but
it must not replace a preference you made for this machine.

For `defaults` only, the order from lowest to highest is:

1. built-in fallback
2. project committed (`.grove/config.json`)
3. project-local (`.grove/config.local.json`)
4. user (`${user_config_dir}/grove/config.json`)
5. declared environment variables, where a field has one
6. `GROVE_<SECTION>__<FIELD>` environment variables
7. CLI flags

The inversion is field-by-field. A committed project default is a team's
suggestion, not a mandate. A project value still applies when the user layer
leaves that field unset. Environment and CLI overrides remain above user,
so an operator can still make a deployment or one-shot choice win.

Use **Save as defaults** in the new-workspace modal to write these answers.
See [new-workspace defaults](configure-project.md#new-workspace-defaults) for
the fields and storage scopes.

## Lists merge by name (only `agents`)

Every other list (`ui.keybindings`, for example) replaces wholesale.
`agents` is special:

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

The merged result is all four: the defaults `claude`, `codex`, and
`shell`, plus the project's `aider`. New names append in order.
Matching names merge field by field: overlay wins, base fills gaps, so
overriding only `claude`'s `command` still leaves `kind: "claude_code"`
for the [Activity Dashboard](features-activity.md).

To *replace* the defaults, give every entry a custom name. The merge
never inserts a `claude` you did not ask for.

## Exclusive field pairs override as a group

Field-by-field merging breaks for a mutually exclusive pair like
`init_script.inline` and `path`, where a single layer cannot set both.
The highest layer to set either field wins the whole choice, and Grove
drops the other and logs a warning. See
[init scripts](configure-init-scripts.md#inline-and-path-are-mutually-exclusive).

## Per-repo resolution

The daemon, TUI, and web dashboard each resolve every repo's cascade
independently: the user layer applies everywhere, layers 3 and 4
(project, project-local) only within that repo. An agent or init
script in `<repo>/.grove/config.json` is honored the same way
everywhere, so you never duplicate it in your user config.

## `${repo}` and `${repo_name}` expand at consume time

Path-shaped config fields can carry placeholders:

```json
{
  "worktree": {
    "root_template": "${repo}/.worktrees"
  }
}
```

`${repo}` is the repo root's absolute path, `${repo_name}` its
basename. Substitution happens at consume time, not validate time, so
one config serves every repo without re-validation. A raw `~` resolves
the same way, via `Path.expanduser`.

## Environment variables

`GROVE_<SECTION>__<FIELD>=value` overrides a single field. Double
underscore separates nesting depth, and field names are lowercase:

```bash
GROVE_TMUX__HISTORY_LIMIT=100000 grove        # one-shot, this invocation only
GROVE_UI__THEME=light grove
GROVE_INIT_SCRIPT__ENABLED=true grove
```

Values stay strings until Pydantic validates them. Coercion to int,
bool, etc. happens at the boundary.

## `${VAR}` references pull a value from a variable you name

`GROVE_<SECTION>__<FIELD>` fixes the variable name to the field's schema
position. A `${VAR}` reference lets you name the variable instead, in
any string value:

```json
{
  "notifications": {
    "gotify": { "enabled": true, "server_url": "${MY_GOTIFY_URL}" }
  }
}
```

- A value can hold one or several references, like
  `"http://${GOTIFY_HOST}:${GOTIFY_PORT}"`.
- Resolution happens after the layers merge and before validation, so
  a bad value fails the same way a literal typo would.
- **An unset or empty variable is an error.** Writing the reference is
  deliberate, so a missing variable should stop the load, not
  silently notify nowhere:

```
Invalid configuration: config: 'notifications.gotify.server_url'
references environment variable 'MY_GOTIFY_URL', which is not set
```

- **`$${VAR}` is the only escape**, resolving to a literal `${VAR}`.
  Any other `$`, bare or an unbraced `$FOO`, is left alone.
  `${repo}`/`${repo_name}` stay reserved for the consume-time
  expansion above.

Use this for values that differ per machine: a base URL, hostname, or
path. A **secret** stays on a `*_env` field instead
(`tickets.gitea.token_env`, `notifications.gotify.token_env`,
`mewbo.api_key_env`), holding only the variable's name. Grove reads it
when needed and never resolves it here.

## A few fields declare their own variable

A handful of fields name a fixed variable in Grove's schema, and setting
it fills the field with no config file or `${VAR}` needed:

```bash
export GROVE_GOTIFY_API_URL=https://gotify.example.com   # notifications.gotify.server_url
export GROVE_GITEA_BASE_URL=https://gitea.example.com    # tickets.gitea.base_url
```

The full list is generated from the schema, in the
[configuration reference](configure-reference.md#environment-variable-overrides),
with names declared per field rather than derived from its path.

**An unset or empty declared variable simply does not override**: your
config file's value stands. A `${VAR}` reference errors when missing
instead, since writing one is deliberate while a declared variable is
offered either way.

Precedence, lowest to highest:

1. the literal value in a config file
2. a `${VAR}` reference in that file, which *is* the file's value, resolved
3. the field's declared variable, e.g. `GROVE_GOTIFY_API_URL`
4. `GROVE_<SECTION>__<FIELD>`, e.g. `GROVE_NOTIFICATIONS__GOTIFY__SERVER_URL`

3 loses to 4: 4 names the exact field, 3 is only a convenience alias.

Only non-secret fields declare one. A credential still goes through a
`*_env` field naming its variable.

## Worked example

A team agrees every workspace runs `uv sync` first, worktree root
beside the repo, in `<repo>/.grove/config.json` (committed):

```json title=".grove/config.json"
{
  "worktree": { "root_template": "${repo}/.worktrees" },
  "init_script": {
    "enabled": true,
    "inline": "uv sync",
    "timeout_seconds": 180
  }
}
```

A teammate prefers dark theme, gitignored in
`<repo>/.grove/config.local.json`:

```json title=".grove/config.local.json"
{ "ui": { "theme": "dark" } }
```

Another wants Aider for one workspace without disturbing the team's
`claude` default, added per machine (all repos) in
`~/.config/grove/config.json`:

```json title="~/.config/grove/config.json"
{ "agents": [{ "name": "aider", "command": "aider --model sonnet" }] }
```

The merged config carries all three pieces, with no coordination needed
and no list wiped.

## See also

- [Project setup](configure-project.md): where each file goes.
- [Agents](configure-agents.md): the merge-by-name rule in action.
- [Configuration reference](configure-reference.md): every field, auto-generated from the model.
