# Agent instructions — hermes-gitlab-kanban-plugin

Shared entry point for AI assistants working in this repository. Keep it
project-specific and safe to publish: no tokens, webhook secrets, private project
paths, board contents, machine paths, or local-only notes.

## Read first

1. `README.md` — what the bridge does and how to install it
2. `plugin/skills/gitlab-kanban/SKILL.md` — the registered operating skill
3. `plugin/cli.py` — the verb list is the clearest map of the surface

## What this plugin is

A bidirectional bridge between **GitLab** (issues, merge requests, milestones) and
the **Hermes Kanban** board. It ingests issues and MRs onto a board, assigns work
to profiles by label, and syncs completed tasks back to GitLab — with a cron sweep
that backfills completions missed while nothing was listening.

Three things distinguish it from its GitHub sibling:

- **Multi-host by design.** Any number of GitLab instances (gitlab.com plus
  self-managed) are registered as named host aliases, each with its own base URL
  and token. Every API call and every task carries its host.
- **A real API client.** `plugin/api.py` wraps the GitLab REST v4 API for full CRUD
  on issues, merge requests, and milestones — not just the board bridge.
- **A test suite.** `tests/` runs on stdlib + pytest, no network.

Eight tools (`gitlab_kanban_status`, `gitlab_kanban_config`,
`gitlab_kanban_project`, `gitlab_kanban_sync`, `gitlab_kanban_task`,
`gitlab_issue`, `gitlab_merge_request`, `gitlab_milestone`), a `/gitlab-kanban`
slash command, a `hermes gitlab-kanban` CLI, and four bundled skills.

## Repo layout

`plugin/` is the single source of truth. The live plugin at
`$HERMES_HOME/plugins/gitlab-kanban` is a **symlink** to it, so editing a file
here edits the running plugin.

| Path | Responsibility |
|---|---|
| `plugin/__init__.py` | `register(ctx)` — tools (from `SCHEMAS`), slash, CLI, skills |
| `plugin/schemas.py` | `SCHEMAS` dict: what the model reads when choosing a tool |
| `plugin/tools.py` | Tool handlers. JSON string in, JSON string out, never raise |
| `plugin/api.py` | GitLab REST v4 client: issues, MRs, milestones, notes |
| `plugin/config.py` | Config store: hosts, ingest toggles, sync-back toggles |
| `plugin/projects.py` | Bridged projects, webhook route wiring + diagnostics |
| `plugin/kanban.py` | Kanban board reads/writes via the `hermes kanban` CLI |
| `plugin/bridge.py` | Payload normalization, label→profile routing, idempotency |
| `plugin/paths.py` | `get_hermes_home()`-anchored paths. No hardcoded `~/.hermes` |
| `plugin/cli.py` | `hermes gitlab-kanban` verbs, incl. `webhook`, `install-sweep` |
| `plugin/slash.py` | `/gitlab-kanban` dispatch, delegating to the CLI module |
| `plugin/skills/*/SKILL.md` | Four registered skills (see below) |
| `scripts/gitlab-to-kanban.py` | Webhook payload on stdin → kanban task |
| `scripts/gitlab-project-manage.py` | Onboard / remove / list bridged projects |
| `scripts/gitlab-kanban-sync-back.py` | Completed kanban task → GitLab write |
| `scripts/gitlab-kanban-sync-sweep.py` | Periodic backfill of missed completions |
| `scripts/gitlab_kanban_loader.py` | Lets standalone scripts import `plugin/` |

Bundled skills: `gitlab-kanban` (bridge ops), `gitlab-development` (implementing
an issue), `gitlab-scrum-master` (backlog/milestone hygiene), `gitlab-code-review`
(reviewing an MR).

`register()` builds tools by looking each `SCHEMAS` key up in a name→handler dict.
A schema whose name has no matching entry is **silently skipped** — so a typo in
either place produces a tool that quietly does not exist. Check `hermes tools`
after adding one.

## The webhook event-name trap

Worth its own section because it fails with **no error anywhere** and cost real
debugging time.

GitLab announces the event in the `X-Gitlab-Event` header using human-readable
names (`Issue Hook`, `Merge Request Hook`), while the payload body carries
snake_case `object_kind` values (`issue`, `merge_request`). Hermes' webhook route
filters on the **header**.

Subscribe the route with the body names and every delivery is accepted at the HTTP
layer and then discarded: GitLab shows `200`, the gateway logs nothing at INFO,
the ingest script never runs, and the board stays empty.

- `GITLAB_EVENT_HEADERS` in `plugin/projects.py` is the mapping. It is the only
  place these names belong.
- `webhook_status()` diagnoses a mismatched route and prints a runnable fix.
- `hermes gitlab-kanban webhook --install` performs the repair and re-verifies.
- `tests/test_webhook_wiring.py` pins the contract, including an assertion that
  the expected events are *not* the `object_kind` values.

If you touch event wiring, keep that test green — it is the guard against a
silent regression.

## Hard rules

This plugin writes to a real issue tracker and dispatches autonomous work. Both
directions are visible to other people.

1. **Tool handlers return a JSON string and never raise** — success and failure
   alike. A handler that raises takes the agent turn down.
2. **Sync must be idempotent in both directions.** A webhook can be redelivered, a
   cron sweep re-runs on a schedule, and a task can be completed twice. Nothing may
   create a duplicate task, post a duplicate note, or reopen a closed issue on
   replay. Prove the guard; do not assume it.
3. **Never comment on, close, or label a GitLab issue or MR as a side effect.**
   Writes to someone's tracker notify people. If the caller did not ask for the
   write, do not perform it.
4. **The sweep is the safety net — keep it conservative.** It must tolerate seeing
   an already-synced task without acting. A sweep that acts on ambiguity turns one
   missed event into a stream of noise.
5. **Webhook payloads are untrusted input.** They arrive over the network from
   outside. The gateway verifies `X-Gitlab-Token`; the script must still validate
   the shape and never interpolate a payload field into a shell command or a path.
6. **Never print `GITLAB_WEBHOOK_SECRET` or a host token.** `hermes gitlab-kanban
   webhook` displays configuration by design — it reports whether a secret is
   *set*, never its value. There is a test for this; keep it.
7. **Host identity is part of every record.** Two GitLab instances can both have
   `group/project#1`. A task, a sync-state key, or an API call that drops the host
   will eventually act on the wrong instance.
8. **Dispatch spawns real agents that spend real money.** Label-based assignment
   decides who runs. A change that widens which issues get dispatched, or which
   profile picks them up, is a behavioural change to state out loud in the PR.
9. **Board is the hard isolation boundary.** Workers are pinned to a board via the
   environment; do not add a path that lets a task reach a board it was not
   assigned to.
10. **Paths go through `hermes_home()`** (`plugin/paths.py`, which prefers the
    framework's `get_hermes_home()`). Never a hardcoded `~/.hermes` — each Hermes
    profile owns its own board and config.
11. **Secrets in `.env`, behaviour in `config.yaml`.** Tokens and the webhook
    secret are credentials. Ingest/sync-back toggles are configuration.
12. **`gitlab_kanban_status` is the diagnostic.** It reports host, webhook, board,
    cron, and sync state, and must stay useful when the bridge is broken.

## Verification

```bash
python -m pytest tests -q
```

stdlib + pytest + `unittest.mock` only, no network. Synthesize webhook payloads;
never commit a real one (they contain user data and project internals).

Read-only checks against the live surface:

```bash
hermes gitlab-kanban status          # host + webhook + board + cron + sync state
hermes gitlab-kanban webhook         # verify the route's event names
hermes gitlab-kanban project list    # which projects are bridged
hermes gitlab-kanban task list       # board read
```

For the webhook path, feed a payload to the script on stdin rather than triggering
a real GitLab event:

```bash
python scripts/gitlab-to-kanban.py < payload.json
```

To exercise the full HTTP path, POST to the route with the correct header — this is
what catches wiring bugs the unit tests cannot see:

```bash
curl -X POST http://localhost:8644/webhooks/gitlab-to-kanban \
  -H "X-Gitlab-Token: $SECRET" -H 'X-Gitlab-Event: Issue Hook' \
  -H 'Content-Type: application/json' --data-binary @payload.json
```

Use a scratch project for anything that writes. Run `hermes gitlab-kanban sync`
only when you are willing to have it act; `--dry-run` first.

## Working on the live plugin

The live plugin is a symlink to `plugin/`, so edits here are live. Verify it,
because `install.sh` does `rm -rf` + `cp -r` and will happily replace the symlink
with a stale copy — after which your edits appear to do nothing:

```bash
ls -ld "$HERMES_HOME/plugins/gitlab-kanban"   # want: -> .../plugin
```

If it is a directory rather than a symlink, restore it:

```bash
rm -rf "$HERMES_HOME/plugins/gitlab-kanban"
ln -s "$PWD/plugin" "$HERMES_HOME/plugins/gitlab-kanban"
```

Python changes need a Hermes restart; skills and docs do not. Sweep changes need
the cron job re-installed — `hermes gitlab-kanban install-sweep`, then confirm with
`hermes cron list`. Webhook changes need the route re-subscribed —
`hermes gitlab-kanban webhook --install`.

Don't run `install.sh` in a dev checkout; it is for fresh installs.

## Contribution style

- One concern per PR. No drive-by reformatting or renames.
- For a bug fix, state the symptom, the exact `file:line` where it manifests, and
  why the change alters that line's behaviour.
- For anything touching sync, dispatch, or webhook handling, state the replay
  scenario you considered (redelivery, double completion, concurrent sweep) and how
  the change behaves under it.
- Update `README.md` and the affected SKILL.md in the same PR when a verb, tool
  parameter, or config key changes.
- Conventional Commits: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`.

## Things to know

- **Adding a model tool is expensive.** Eight tools already ship their schemas on
  every API call the agent makes, whether or not GitLab is involved that turn.
  Prefer extending an existing tool, a CLI verb, or a skill.
- **The scripts are the product; the tools are a thin front end.** Most real
  behaviour lives in `scripts/`, driven by a webhook or cron. When something
  misbehaves in production, the script and its cron entry are where to look.
- **`api.py` has no third-party dependencies.** It is `urllib`-based on purpose so
  the plugin installs with no `pip install` step. Keep it that way.
- **Sibling plugins share the tools/schemas/slash/cli shape** but not the
  internals. Do not import a pattern from a sibling without checking it exists
  here.
