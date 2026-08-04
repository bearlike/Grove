# Ticket providers

## Connect your issue tracker

Grove links each workspace to the ticket it serves, read off the branch
name. Turn it on with a `tickets` section, which cascades like every
other: pin providers in project config, adjust locally. See [ticket
providers](features-ticket-providers.md) for the capability itself.

All three providers default to **off**. Enable only what you use.

!!! note "Credentials are never stored in config"
    Grove stores the **name** of an environment variable, never a token.
    The secret lives in your environment under that name: config holds
    `token_env`, your shell holds the value, safe to commit.

## The `tickets` section

```json
{
  "tickets": {
    "gitea":  { "enabled": false, "base_url": "https://gitea.example.com", "owner": "your-org", "repo": "your-repo", "token_env": "GROVE_GITEA_TOKEN", "branch_prefix": "" },
    "github": { "enabled": false, "base_url": "https://api.github.com",     "owner": "your-org", "repo": "your-repo", "token_env": "GROVE_GITHUB_TOKEN", "branch_prefix": "" },
    "linear": { "enabled": false, "base_url": "https://api.linear.app/graphql", "team_key": "ENG", "token_env": "GROVE_LINEAR_TOKEN" }
  }
}
```

## Fields

| Field | Providers | Meaning |
|---|---|---|
| `enabled`       | all | Master switch. `false` skips the provider in both parsing and fetch. |
| `base_url`      | all | API root. `https://api.github.com` (GitHub.com) or `<host>/api/v3` (GitHub Enterprise); your Gitea instance root; the Linear GraphQL endpoint. |
| `token_env`     | all | The **name** of the environment variable holding the token. Not the token. |
| `owner`         | gitea, github | Repository owner or organization, used to fetch a ticket by id. |
| `repo`          | gitea, github | Repository name, used to fetch a ticket by id. |
| `team_key`      | linear | Linear team key (for example `ENG`), the key Grove matches in branch names, so `ENG-123` anywhere resolves. No `owner`/`repo`: the team key plus issue number already identify the ticket. |
| `branch_prefix` | gitea, github | Extra keyword recognized on top of the built-ins. For example `"bug"` on GitHub also matches `bug-42-...` as issue 42. Empty by default. Distinct from `worktree.branch_prefix`, which prefixes the branch names Grove generates. |
| `env_file`      | section | Optional dotenv file the tokens are read from. Repo relative or absolute. |
| `env_command`   | section | Optional command whose stdout is read as dotenv. Exclusive with `env_file`. |

## Putting the token in your environment

Export the variable named by `token_env` before launching Grove. The daemon
reads these from its own environment, so set them where it starts:

```bash
export GROVE_GITEA_TOKEN=...
export GROVE_GITHUB_TOKEN=...
export GROVE_LINEAR_TOKEN=...
```

## When the token only exists later

A long running daemon reads its own environment once, at startup, so a
credential produced later (an init script, a secret manager, a rotation)
arrives too late. Point `tickets` at a source Grove reads on demand
instead.

```json
{
  "tickets": {
    "env_file": ".grove/tickets.env",
    "gitea": { "enabled": true, "owner": "your-org", "repo": "your-repo", "token_env": "GROVE_GITEA_TOKEN" }
  }
}
```

Or resolve values without writing them to disk: any command printing
dotenv to stdout works, so Grove needs to know nothing about your secret
manager.

```json
{ "tickets": { "env_command": "my-secrets export grove" } }
```

One source serves every provider, keyed by the same `token_env` names: the
file holds the values, the config only the path.

### Resolution order

Grove resolves a token at the moment of use, never at build time, caching
nothing between. Every lookup follows the same steps, under the
`token_env` name:

1. The configured source, if any. `env_command` runs fresh. `env_file` is
   read fresh and parsed as dotenv.
2. The environment of the process running Grove.

So a token written a second ago is found immediately, a rotation needs
no restart, and `env_command` must stay cheap: it runs every lookup.

- No token anywhere: the provider reports `configured: false`, pickers
  gray it out, and a call fails with a clear error, not an
  unauthenticated request.
- A configured `env_file` that does not exist: an error, not an empty
  environment, so a missing credential never looks like a working one.

!!! warning "What a committed config may name"
    `.grove/config.json` may request a capability, never grant one: a
    committed `env_command` is ignored (cloning must not run a command
    from it), and a committed `env_file` must stay inside the repository.
    Get the full range from user config or the gitignored
    `.grove/config.local.json`. `container` follows the identical rule.

## Per-provider notes

| Provider | `base_url` | `owner`/`repo` | Recognized branch forms |
|---|---|---|---|
| Gitea | Your Gitea instance root | Required, pins the repository | bare leading number (`5-...`), `gitea-5-...`, `gtea-5-...` |
| GitHub | `https://api.github.com`, or your Enterprise `/api/v3` root | Required, pins the repository | bare leading number (`42-...`), `gh-42-...` |
| Linear | Linear GraphQL endpoint | Not used, the team key plus issue number identify it | `team_key` anywhere in the branch (for example `ENG-123`); `token_env` holds your personal API key |

### Gitea

```json
{
  "tickets": {
    "gitea": {
      "enabled": true,
      "base_url": "https://gitea.example.com",
      "owner": "your-org",
      "repo": "your-repo",
      "token_env": "GROVE_GITEA_TOKEN"
    }
  }
}
```

### GitHub

```json
{
  "tickets": {
    "github": {
      "enabled": true,
      "base_url": "https://api.github.com",
      "owner": "your-org",
      "repo": "your-repo",
      "token_env": "GROVE_GITHUB_TOKEN"
    }
  }
}
```

!!! warning "Bare numbers are ambiguous when both numeric providers run"
    Gitea and GitHub share the numeric grammar, so `123-fix` matches both
    when both are enabled. Grove marks the match ambiguous and the UI
    flags it. Prefer keyword forms (`gh-42`, `gtea-5`) or a distinct
    `branch_prefix` per provider. See [ticket
    providers](features-ticket-providers.md#the-bare-number-is-deliberately-ambiguous).

### Linear

```json
{
  "tickets": {
    "linear": {
      "enabled": true,
      "base_url": "https://api.linear.app/graphql",
      "team_key": "ENG",
      "token_env": "GROVE_LINEAR_TOKEN"
    }
  }
}
```

## Where this config lives

The `tickets` section follows the same cascade as the rest of Grove.

| Layer | Path | Use |
|---|---|---|
| **Project** | `<repo>/.grove/config.json` | Team baseline. Commit this (no secrets, just `token_env`). |
| **Project-local** | `<repo>/.grove/config.local.json` | Per-machine overrides. Gitignored. |
| **User** | `${user_config_dir}/grove/config.json` | Per-user defaults for every repo. |

## See also

- [Ticket providers](features-ticket-providers.md), how Grove derives and displays refs.
- [Project setup](configure-project.md), where the config files live.
- [Configuration cascade](features-cascade.md), the six layers and last-wins resolution.
- [Configuration reference](configure-reference.md), every field, auto-generated.
