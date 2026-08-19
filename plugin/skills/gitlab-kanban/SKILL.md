---
name: gitlab-kanban
description: "Operate the GitLab-Kanban bridge: hosts, projects, sync."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [gitlab, kanban, bridge, webhook, sync, devops]
    category: project-management
    related_skills: [gitlab-development, gitlab-code-review, gitlab-scrum-master]
---

# GitLab-Kanban Bridge Skill

Operating guide for the bridge between GitLab (issues, merge requests,
milestones) and the Hermes kanban board. Covers wiring a host, onboarding a
project, how work gets routed to a role, and how completed work flows back.

This skill is about **running the bridge**. For doing the work a bridged task
asks for, load `gitlab-kanban:gitlab-development` (implementation),
`gitlab-kanban:gitlab-code-review` (review/QA), or
`gitlab-kanban:gitlab-scrum-master` (planning, sprints, board hygiene).

## When to Use

- Wiring a new GitLab instance (gitlab.com or self-managed) into the board.
- Onboarding or removing a project so its issues/MRs create kanban tasks.
- Diagnosing why an issue did not become a task, or why a done task did not
  sync back.
- Changing routing: which label goes to which role, which role runs on which
  Hermes profile.

## Prerequisites

- A GitLab token with `api` scope in `<hermes_home>/.env` as `GITLAB_TOKEN`
  (each extra host names its own variable, e.g. `GITLAB_WORK_TOKEN`).
- `GITLAB_WEBHOOK_SECRET` in `.env` if you want GitLab's `X-Gitlab-Token`
  verified — set it before onboarding, because it is baked into the hook.
- The webhook platform enabled (`platforms.webhook.enabled: true`) and a public
  URL (zrok, ngrok, cloudflared) if GitLab must reach this machine.
- A kanban board — the bridge creates it on first ingest if missing.

## How to Run

```bash
hermes gitlab-kanban status                    # always start here
hermes gitlab-kanban host add work https://gitlab.mycorp.io --token-env GITLAB_WORK_TOKEN
hermes gitlab-kanban project onboard mygroup/myrepo --host work --board myrepo
hermes gitlab-kanban sync --dry-run            # see what would be written
```

In chat: `/gitlab-kanban status | projects | board | issues | mrs | sprints | sync | config | webhook`

## Quick Reference

| Need | Command |
|---|---|
| Bridge health | `hermes gitlab-kanban status` |
| Add a self-managed host | `hermes gitlab-kanban host add <alias> <url> --token-env <VAR>` |
| Self-signed cert host | add `--no-verify-ssl` |
| Onboard a project | `hermes gitlab-kanban project onboard <group/repo> [--host a] [--board b]` |
| Config-only (no webhook) | add `--no-hook` |
| Route a label to a role | `hermes gitlab-kanban config label bug developer` |
| Point a role at a profile | `hermes gitlab-kanban config role developer scala-coder` |
| Choose ingested events | `hermes gitlab-kanban config ingest --issues on --merge-requests off` |
| Choose completion writes | `hermes gitlab-kanban config sync-back --comment on --close-issue on` |
| Force a sync | `hermes gitlab-kanban sync [<task_id>] [--dry-run]` |
| Verify the webhook route | `hermes gitlab-kanban webhook` |
| Fix a broken webhook route | `hermes gitlab-kanban webhook --install` |
| Install the cron sweep | `hermes gitlab-kanban install-sweep` |

Tools: `gitlab_kanban_status`, `gitlab_kanban_config`, `gitlab_kanban_project`,
`gitlab_kanban_sync`, `gitlab_kanban_task`, `gitlab_issue`,
`gitlab_merge_request`, `gitlab_milestone`.

## The webhook event-name trap

**This is the single most common reason a correctly-configured bridge produces
nothing at all.** GitLab announces the event in the `X-Gitlab-Event` header using
human-readable names — `Issue Hook`, `Merge Request Hook` — while the payload
body carries snake_case `object_kind` values (`issue`, `merge_request`). Hermes'
webhook route filters on the **header**.

Subscribe the route with the body names and every delivery is accepted at the
HTTP layer and then silently discarded: GitLab shows a `200`, the gateway logs
nothing at INFO, the ingest script never runs, and the board stays empty. There
is no error to find.

Always subscribe with the header values:

```bash
hermes webhook subscribe gitlab-to-kanban \
  --events 'Issue Hook,Merge Request Hook' \
  --script gitlab-to-kanban.py \
  --description 'GitLab issues and merge requests to kanban'
```

Or let the plugin do it and verify itself:

```bash
hermes gitlab-kanban webhook            # diagnose: names the problem + prints the fix
hermes gitlab-kanban webhook --install  # remove a bad route, re-subscribe, verify
```

`hermes gitlab-kanban status` also reports this, so a broken route shows up in
routine health checks rather than as silence.

## The Pipeline

```
GitLab issue/MR (open, reopen)
  → tunnel → webhook route gitlab-to-kanban → scripts/gitlab-to-kanban.py
  → kanban task (labels → role → Hermes profile, role skill injected)
  → kanban dispatcher → the assigned profile runs the work
  → task done → gitlab-kanban-sync-sweep.py (cron)
  → note posted on the GitLab object, issue closed / labelled per config
```

Every bridged task body carries a marker line:

```
gitlab-link: <host-alias> <issue|merge_request> <group/repo> <iid>
```

That marker is the only join between a task and its GitLab object. **Do not
strip or rewrite it** — sync-back finds nothing without it, and the task ends
up orphaned.

## Roles

The bridge speaks in roles; roles map to whatever Hermes profiles the user
actually has.

| Role | Default labels | Default profile | Skill injected |
|---|---|---|---|
| developer | bug, feature, refactor, chore | coder | gitlab-development |
| reviewer | review, code-review | reviewer | gitlab-code-review |
| qa | test, qa | reviewer | gitlab-code-review |
| scrum-master | planning, epic | orchestrator | gitlab-scrum-master |
| researcher | research, spike | researcher | — |
| writer | docs, documentation | writer | — |

First matching label wins, in label order. No match falls back to the
project's `default_role`, then the global one.

## Procedure — wiring a new instance

1. `hermes gitlab-kanban status` — confirm the config file path and which hosts
   already exist.
2. Put the token in `<hermes_home>/.env` under a distinct variable name. Never
   put it in the bridge config.
3. `hermes gitlab-kanban host add <alias> <url> --token-env <VAR>`.
4. Re-run `status` — the host must report `authenticated_as`. If it reports a
   token error, fix that before onboarding anything.
5. `hermes gitlab-kanban project onboard <group/repo> --host <alias>`. Check the
   result for `webhook_warning` — a warning means the project was allow-listed
   but GitLab will not call us.
6. Adjust routing with `config label` / `config role` so incoming work lands on
   profiles that exist.
7. `hermes gitlab-kanban install-sweep`, then `hermes cron list` to confirm.
8. Open a throwaway issue in the project and confirm a task appears with
   `hermes gitlab-kanban task list`.

## Pitfalls

- **The webhook `--events` values are header names, not `object_kind`.** See the
  trap section above — this fails completely silently. `hermes gitlab-kanban
  webhook` is the diagnostic; `--install` is the repair.
- **Sync-back writes to someone else's tracker.** Notes and closes notify
  people. Run `sync --dry-run` first, and never call sync as a side effect of
  an unrelated request.
- **The webhook secret is baked into the hook at onboard time.** Setting
  `GITLAB_WEBHOOK_SECRET` afterwards does not update existing hooks — remove and
  re-onboard the project.
- **`iid` is not `id`.** GitLab's per-project number (`#42`, `!17`) is the
  `iid`; the global `id` is different and the API paths used here want `iid`.
  Passing an `id` silently addresses the wrong object.
- **A tunnel URL changes when the tunnel restarts.** After a restart, run
  `hermes gitlab-kanban webhook` for the new URL and update the project hooks
  (remove + re-onboard, or edit them in GitLab).
- **Empty `projects` list means accept-all.** Until you onboard something, any
  repo that can reach the webhook creates tasks. Onboard deliberately.
- **Ingest is idempotent, so a redelivered webhook is safe** — it returns the
  existing task via the kanban idempotency key. Do not add a second dedup layer
  that guesses by title.
- **Group milestones need `--group`, not `--project`.** Cross-project sprints
  live at the group level.
- **Tokens are never printed.** `status` reports presence and the authenticated
  username only; keep it that way when extending it.

## Verification

```bash
hermes gitlab-kanban status                        # hosts authenticate, projects listed
hermes gitlab-kanban project list                  # allow-list + hook ids
hermes gitlab-kanban task list                     # board side
hermes gitlab-kanban sync --dry-run                # what sync-back would write
python3 scripts/gitlab-to-kanban.py < payload.json # replay a recorded payload
```

Feed the ingest script a synthesized payload rather than triggering real GitLab
events, and test sync-back against a scratch project — never one other people
watch.
