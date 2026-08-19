# hermes-gitlab-kanban-plugin

A GitLab ↔ Hermes Kanban bridge. Issues and merge requests land on a kanban
board, get routed to a role (developer, reviewer, scrum master, QA), and their
outcome flows back to GitLab as a note plus an optional close or label.

Works against **gitlab.com and any number of self-managed instances at once** —
each host is an alias with its own base URL, its own token variable, and its own
TLS policy. Stdlib only, no third-party Python dependencies.

## Install

```bash
./install.sh
hermes plugins enable gitlab-kanban
# restart Hermes — plugin registration takes effect next session
```

Then put a GitLab token with `api` scope in `<hermes_home>/.env`:

```
GITLAB_TOKEN=glpat-...
GITLAB_WEBHOOK_SECRET=<random string>   # optional but recommended
```

Set the webhook secret **before** onboarding projects — it is baked into the
hook at creation time.

## Webhook route

Live ingest needs a Hermes webhook route. Let the plugin create it, so the event
names are right:

```bash
hermes gitlab-kanban webhook --install   # subscribe (or repair) and verify
hermes gitlab-kanban webhook             # check an existing route
```

> **The one thing that silently breaks this bridge.** GitLab sends the event in
> the `X-Gitlab-Event` header using human-readable names — `Issue Hook`,
> `Merge Request Hook` — while the payload body uses snake_case `object_kind`
> values (`issue`, `merge_request`). The Hermes route filters on the **header**.
>
> Subscribe with the body names and every delivery is accepted and then dropped:
> GitLab shows `200`, the gateway logs nothing, the ingest script never runs, and
> the board stays empty. There is no error message anywhere.
>
> If you subscribe by hand, use the header names:
>
> ```bash
> hermes webhook subscribe gitlab-to-kanban \
>   --events 'Issue Hook,Merge Request Hook' \
>   --script gitlab-to-kanban.py \
>   --description 'GitLab issues and merge requests to kanban'
> ```
>
> `hermes gitlab-kanban webhook` and `status` both detect and report a route
> subscribed the wrong way, and print the exact command to fix it.

## What it does

```
GitLab issue / merge request (open, reopen)
  → tunnel → webhook route gitlab-to-kanban → scripts/gitlab-to-kanban.py
  → kanban task: labels → role → Hermes profile, role skill injected
  → kanban dispatcher runs the assigned profile
  → task done → gitlab-kanban-sync-sweep.py (cron)
  → note posted on the GitLab object; issue closed / labelled per config
```

Both directions are idempotent. Ingest uses a deterministic kanban idempotency
key (`gitlab:<host>:<kind>:<project>:<iid>`), so a redelivered webhook returns
the existing task. Sync-back records synced task ids, so a cron sweep re-running
is a no-op — no duplicate notes, no re-closing.

Every bridged task body carries the join key:

```
gitlab-link: <host-alias> <issue|merge_request> <group/repo> <iid>
```

Strip that line and sync-back can no longer find the object.

## Roles

The bridge speaks roles; roles map to whichever Hermes profiles you actually
have. Both mappings are configurable.

| Role | Default labels | Default profile | Skill injected into the worker |
|---|---|---|---|
| developer | bug, feature, refactor, chore | coder | `gitlab-development` |
| reviewer | review, code-review | reviewer | `gitlab-code-review` |
| qa | test, qa | reviewer | `gitlab-code-review` |
| scrum-master | planning, epic | orchestrator | `gitlab-scrum-master` |
| researcher | research, spike | researcher | — |
| writer | docs, documentation | writer | — |

First matching label wins, in label order. No match falls back to the project's
`default_role`, then the global one.

```bash
hermes gitlab-kanban config label perf developer      # route a label to a role
hermes gitlab-kanban config role developer scala-coder # point a role at a profile
```

## Multiple hosts

```bash
hermes gitlab-kanban host add work https://gitlab.mycorp.io --token-env GITLAB_WORK_TOKEN
hermes gitlab-kanban host add lab  https://gitlab.lab --token-env LAB_TOKEN --no-verify-ssl
hermes gitlab-kanban host default work
hermes gitlab-kanban project onboard mygroup/myrepo --host work --board myrepo
```

Tokens live in `.env` only; the bridge config stores the *variable name*, never
the value.

## CLI

```bash
# connect
hermes gitlab-kanban status
hermes gitlab-kanban host list|add|remove|default
hermes gitlab-kanban webhook [--install] [--print-command]

# onboard
hermes gitlab-kanban project list|onboard|remove

# configure
hermes gitlab-kanban config show
hermes gitlab-kanban config board <slug> [--name ...]
hermes gitlab-kanban config role <role> <profile>
hermes gitlab-kanban config label <label> <role>
hermes gitlab-kanban config auto-label "title contains crash => bug,urgent"
hermes gitlab-kanban config ingest --issues on --merge-requests off
hermes gitlab-kanban config sync-back --comment on --close-issue on
hermes gitlab-kanban config reset

# work
hermes gitlab-kanban issue list|get|create|update|close|reopen|comment|notes|delete|to-kanban
hermes gitlab-kanban merge-request list|get|create|update|comment|approve|merge|close|reopen|changes|to-kanban
hermes gitlab-kanban milestone list|get|create|update|close|reopen|delete|issues|progress
hermes gitlab-kanban task list|show|create|assign|comment|complete

# sync
hermes gitlab-kanban sync [<task_id>] [--dry-run]
hermes gitlab-kanban install-sweep
```

`mr` aliases `merge-request`; `sprint` aliases `milestone`.

## Chat

```
/gitlab-kanban status | projects | board | issues [project] | mrs [project]
                | sprints [project] | sync [--dry-run] | config | webhook | help
```

## Tools

| Tool | Purpose |
|---|---|
| `gitlab_kanban_status` | Bridge health: hosts, tokens, projects, board, sync state |
| `gitlab_kanban_config` | Board, hosts, role/label routing, ingest and sync-back policy |
| `gitlab_kanban_project` | Onboard / remove / list bridged projects |
| `gitlab_kanban_sync` | Sync completed tasks back to GitLab (supports `dry_run`) |
| `gitlab_kanban_task` | Board-side task list/show/create/assign/comment/complete |
| `gitlab_issue` | Issue CRUD + notes + bridge-to-kanban |
| `gitlab_merge_request` | MR CRUD + approve, merge, changed-files diff, bridge-to-kanban |
| `gitlab_milestone` | Milestones as sprints, incl. issue list and progress report |

## Bundled skills

Four skills ship **inside** the plugin and register via `ctx.register_skill`, so
they install and version with it. They are namespaced, not in the flat skills
tree — load explicitly:

```
skill_view(name='gitlab-kanban:gitlab-kanban')          # bridge operations
skill_view(name='gitlab-kanban:gitlab-development')     # developer role
skill_view(name='gitlab-kanban:gitlab-code-review')     # reviewer / QA role
skill_view(name='gitlab-kanban:gitlab-scrum-master')    # sprint planning role
```

The bridge injects the matching role skill into each dispatched worker, so a
task arrives with its playbook already loaded.

## Configuration

Behaviour lives in `<hermes_home>/scripts/gitlab-kanban-bridge-config.json`
(profile-aware), secrets in `<hermes_home>/.env`. Notable sections:

- `hosts` — alias → `{url, token_env, verify_ssl}`
- `projects` — allow-list; **empty means accept-all**, so onboard deliberately
- `label_roles` / `role_profiles` — routing
- `ingest` — which object kinds and actions create tasks
- `sync_back` — `comment`, `close_issue`, `close_merge_request`, `labels_on_done`

## Tests

Stdlib + pytest, no network, isolated `HERMES_HOME`:

```bash
python -m pytest tests -q
```

94 tests cover config merging and host resolution, payload normalization,
ingest gating, both idempotency guards, sync-back policy, the tool contract
(handlers return JSON and never raise), the API client's URL/error handling,
registration of every surface, and the standalone scripts.

## Requirements

- `GITLAB_TOKEN` (or a per-host variable) with `api` scope in `.env`
- Webhook platform enabled (`platforms.webhook.enabled: true`) for live ingest
- A public URL (zrok, ngrok, cloudflared) if GitLab must reach this machine
- A cron job for the sweep (`hermes gitlab-kanban install-sweep`)

## License

MIT
