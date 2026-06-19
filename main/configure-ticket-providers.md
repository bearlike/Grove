# Ticket providers

Grove can link each workspace to the ticket it serves, reading the link
off the branch name. To turn that on, add a `tickets` section to your
config. It cascades per repo and globally like every other section, so a
team can pin providers in the committed project config and a developer can
adjust them locally. The capability itself is described on
[ticket providers](features-ticket-providers.md); this page is the setup.

All three providers default to **off**. Enable only what you use.

!!! note "Credentials are never stored in config"
    Grove stores the **name** of an environment variable, never a token.
    Put the secret in your environment under that name. The config file
    holds `token_env`, your shell holds the value. Safe to commit, safe to
    review.

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
| `enabled`       | all | Master switch. `false` skips the provider in both branch parsing and fetch. |
| `base_url`      | all | API root. See the per-provider notes below for the exact value. |
| `token_env`     | all | The **name** of the environment variable holding the API token. Not the token. |
| `owner`         | gitea, github | The repository owner or organization, used to fetch a ticket by id. |
| `repo`          | gitea, github | The repository name, used to fetch a ticket by id. |
| `team_key`      | linear | The Linear team key (for example `ENG`). This is the key Grove matches in branch names (`ENG-123`). |
| `branch_prefix` | gitea, github | Optional extra keyword the numeric providers also recognize in a branch name. Empty by default. |

## Putting the token in your environment

Export the variable named by `token_env` before launching Grove:

```bash
export GROVE_GITEA_TOKEN=...
export GROVE_GITHUB_TOKEN=...
export GROVE_LINEAR_TOKEN=...
```

The daemon reads these from its own environment, so set them where the
daemon starts. The config file only ever names the variable, so it stays
free of secrets and safe to commit.

## Per-provider notes

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

`base_url` is your Gitea instance root. `owner` and `repo` pin the
repository Grove fetches issues from. Gitea recognizes a bare leading
number in a branch name (`5-short-description`) plus the built-in
keywords `gitea-` and `gtea-` (`gtea-5-...`).

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

For GitHub.com, leave `base_url` as `https://api.github.com`. For GitHub
Enterprise, point it at your instance's `/api/v3` root (for example
`https://github.example.com/api/v3`). GitHub recognizes a bare leading
number (`42-short-description`) plus the built-in keyword `gh-`
(`gh-42-...`).

!!! warning "Bare numbers are ambiguous when both numeric providers run"
    Gitea and GitHub share the numeric grammar, so a branch like
    `123-fix` matches both when both are enabled. Grove stores the match
    as ambiguous and the UI flags it. Prefer the keyword forms (`gh-42`,
    `gtea-5`) or a distinct `branch_prefix` per provider. See
    [ticket providers](features-ticket-providers.md#the-bare-number-is-deliberately-ambiguous).

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

`base_url` is the Linear GraphQL endpoint. `token_env` names the variable
holding your Linear personal API key. `team_key` is the team prefix Grove
matches in branch names, so a branch containing `ENG-123` (anywhere)
resolves to that ticket. Linear needs no `owner` or `repo`; the team key
plus issue number identify the ticket.

## The optional `branch_prefix`

`branch_prefix` is an extra keyword for the numeric providers, on top of
the built-ins. Set `"branch_prefix": "bug"` on GitHub and Grove also
reads `bug-42-...` as GitHub issue 42. Leave it empty to rely on the
built-in keywords (`gh-` for GitHub, `gitea-` / `gtea-` for Gitea) and
the bare leading number. This is separate from `worktree.branch_prefix`,
which prefixes the branch names Grove generates.

## Where this config lives

The `tickets` section follows the same cascade as the rest of Grove
config:

| Layer | Path | Use |
|---|---|---|
| **Project** | `<repo>/.grove/config.json` | Team baseline. Commit this (no secrets, just `token_env`). |
| **Project-local** | `<repo>/.grove/config.local.json` | Per-machine overrides. Gitignored. |
| **User** | `${user_config_dir}/grove/config.json` | Per-user defaults for every repo. |

## See also

- [Ticket providers](features-ticket-providers.md): how Grove derives and displays ticket refs.
- [Project setup](configure-project.md): where the config files live.
- [Configuration cascade](features-cascade.md): the six layers and last-wins resolution.
- [Configuration reference](configure-reference.md): every field, auto-generated from the model.
