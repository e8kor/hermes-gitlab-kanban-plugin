"""Tool schemas for the gitlab-kanban plugin — what the model reads.

Eight tools, grouped by surface: bridge operation (status, config, project,
sync), the kanban side (task), and GitLab CRUD (issue, merge_request,
milestone). Each CRUD tool is action-dispatched so one schema covers a whole
object type instead of shipping six near-identical schemas on every API call.
"""

_PROJECT = {
    "type": "string",
    "description": "GitLab project path 'group/subgroup/repo' (or numeric id). Defaults to the single onboarded project when only one exists.",
}
_HOST = {
    "type": "string",
    "description": "Configured GitLab host alias (e.g. 'gitlab.com' or 'work-selfhosted'). Omit to use the default host.",
}

SCHEMAS = {
    "gitlab_kanban_status": {
        "name": "gitlab_kanban_status",
        "description": (
            "Show GitLab-Kanban bridge health: configured hosts and whether their tokens "
            "authenticate, onboarded projects, board task counts, webhook route/URL, and "
            "sync-back state. Use first when the bridge misbehaves or before changing config."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    "gitlab_kanban_config": {
        "name": "gitlab_kanban_config",
        "description": (
            "Read or change bridge configuration: the kanban board, GitLab hosts (custom/"
            "self-managed instances), label-to-role and role-to-profile mappings, auto-label "
            "rules, which GitLab events are ingested, and what happens on completion "
            "(comment/close/label). Use action='show' to inspect before changing anything."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "show", "set-board", "add-host", "remove-host", "set-default-host",
                        "set-role-profile", "set-label-role", "add-auto-label",
                        "set-ingest", "set-sync-back", "set-style", "reset",
                    ],
                    "description": "What to do.",
                },
                "board_slug": {"type": "string", "description": "Kanban board slug (set-board)."},
                "board_name": {"type": "string", "description": "Kanban board display name (set-board)."},
                "host": {"type": "string", "description": "Host alias (add-host / remove-host / set-default-host)."},
                "url": {"type": "string", "description": "GitLab base URL for the host, e.g. https://gitlab.mycorp.io (add-host)."},
                "token_env": {"type": "string", "description": "Name of the .env variable holding this host's token, e.g. GITLAB_WORK_TOKEN (add-host)."},
                "verify_ssl": {"type": "boolean", "description": "Verify TLS for this host (add-host). Default true."},
                "role": {"type": "string", "description": "Role name: developer, reviewer, scrum-master, qa, researcher, writer (set-role-profile / set-label-role)."},
                "profile": {"type": "string", "description": "Hermes profile that executes the role (set-role-profile)."},
                "label": {"type": "string", "description": "GitLab label to map to a role (set-label-role)."},
                "rule": {"type": "string", "description": "Auto-label rule: '<title|body|label> contains <text> => label1,label2' (add-auto-label)."},
                "issues": {"type": "boolean", "description": "Ingest issue events (set-ingest)."},
                "merge_requests": {"type": "boolean", "description": "Ingest merge request events (set-ingest)."},
                "comment": {"type": "boolean", "description": "Post a note on the GitLab object when a task completes (set-sync-back)."},
                "close_issue": {"type": "boolean", "description": "Close the issue when its task completes (set-sync-back)."},
                "close_merge_request": {"type": "boolean", "description": "Close the merge request when its task completes (set-sync-back)."},
                "labels_on_done": {"type": "string", "description": "Comma-separated labels to add on completion (set-sync-back)."},
                "tone": {"type": "string", "description": "Style tone for generated descriptions and notes (set-style)."},
            },
            "required": ["action"],
        },
    },
    "gitlab_kanban_project": {
        "name": "gitlab_kanban_project",
        "description": (
            "Onboard, remove, or list GitLab projects bridged into the kanban board. Onboarding "
            "registers a project webhook on the GitLab side and adds the project to the "
            "allow-list so its issues and merge requests create kanban tasks. Use to control "
            "which repositories feed the board."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["onboard", "remove", "list"], "description": "What to do."},
                "project": _PROJECT,
                "host": _HOST,
                "board_slug": {"type": "string", "description": "Dedicated board for this project (onboard). Omit to use the global board."},
                "default_role": {"type": "string", "description": "Role for this project's unlabelled work (onboard)."},
                "url": {"type": "string", "description": "Public webhook URL override (onboard). Auto-detected when omitted."},
                "register_hook": {"type": "boolean", "description": "Register the GitLab webhook (onboard). Default true; set false for a config-only entry."},
            },
            "required": ["action"],
        },
    },
    "gitlab_kanban_sync": {
        "name": "gitlab_kanban_sync",
        "description": (
            "Sync completed kanban tasks back to GitLab: post the result as a note and close or "
            "label the issue/merge request per config. Idempotent — an already-synced task is "
            "skipped. Use dry_run=true first to see what would be written; these are public "
            "writes to someone's tracker."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Sync a single kanban task instead of sweeping the board."},
                "board": {"type": "string", "description": "Board slug. Defaults to the configured bridge board."},
                "dry_run": {"type": "boolean", "description": "Report the writes that would happen without performing them."},
            },
            "required": [],
        },
    },
    "gitlab_kanban_task": {
        "name": "gitlab_kanban_task",
        "description": (
            "Work with kanban tasks on the bridge board: list, show, create, assign to a role, "
            "comment, or complete. Use this for board-side work; the GitLab side has its own "
            "issue and merge_request tools."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "show", "create", "assign", "comment", "complete"], "description": "What to do."},
                "board": {"type": "string", "description": "Board slug. Defaults to the configured bridge board."},
                "task_id": {"type": "string", "description": "Kanban task id (show/assign/comment/complete)."},
                "title": {"type": "string", "description": "Task title (create)."},
                "body": {"type": "string", "description": "Task body (create) or comment text (comment) or result summary (complete)."},
                "role": {"type": "string", "description": "Role to assign; resolved to a profile via config (create/assign)."},
                "assignee": {"type": "string", "description": "Explicit Hermes profile, bypassing role mapping (create/assign)."},
                "status": {"type": "string", "description": "Filter by status: todo, ready, running, review, blocked, done (list)."},
                "priority": {"type": "integer", "description": "Priority tiebreaker (create)."},
            },
            "required": ["action"],
        },
    },
    "gitlab_issue": {
        "name": "gitlab_issue",
        "description": (
            "Full CRUD on GitLab issues: list, get, create, update, close, reopen, comment, "
            "delete. Also links an issue to a kanban task so completion syncs back. Writes are "
            "visible to everyone watching the project — only write when asked."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "get", "create", "update", "close", "reopen", "comment", "delete", "notes", "to-kanban"],
                    "description": "What to do. 'to-kanban' creates a bridged kanban task for an existing issue.",
                },
                "project": _PROJECT,
                "host": _HOST,
                "iid": {"type": "integer", "description": "Issue iid — the per-project number shown as #N."},
                "title": {"type": "string", "description": "Issue title (create/update)."},
                "description": {"type": "string", "description": "Issue description (create/update)."},
                "labels": {"type": "string", "description": "Comma-separated labels (create/update)."},
                "assignee": {"type": "string", "description": "GitLab username to assign (create/update)."},
                "milestone_id": {"type": "integer", "description": "Milestone id to attach (create/update)."},
                "due_date": {"type": "string", "description": "Due date YYYY-MM-DD (create/update)."},
                "weight": {"type": "integer", "description": "Story-point weight (create/update)."},
                "body": {"type": "string", "description": "Note body (comment)."},
                "state": {"type": "string", "enum": ["opened", "closed", "all"], "description": "State filter (list). Default opened."},
                "search": {"type": "string", "description": "Text search (list)."},
                "limit": {"type": "integer", "description": "Max results (list/notes). Default 20."},
                "role": {"type": "string", "description": "Role for the created kanban task (to-kanban)."},
            },
            "required": ["action"],
        },
    },
    "gitlab_merge_request": {
        "name": "gitlab_merge_request",
        "description": (
            "Full CRUD on GitLab merge requests: list, get, create, update, comment, approve, "
            "merge, close, read the changed-files diff, and bridge one into a kanban review "
            "task. Merging and approving are irreversible team-visible actions — confirm intent "
            "before calling them."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "get", "create", "update", "comment", "approve", "merge", "close", "reopen", "changes", "to-kanban"],
                    "description": "What to do.",
                },
                "project": _PROJECT,
                "host": _HOST,
                "iid": {"type": "integer", "description": "Merge request iid — the per-project number shown as !N."},
                "title": {"type": "string", "description": "MR title (create/update)."},
                "description": {"type": "string", "description": "MR description (create/update)."},
                "source_branch": {"type": "string", "description": "Source branch (create)."},
                "target_branch": {"type": "string", "description": "Target branch (create/update). Defaults to the project default branch."},
                "labels": {"type": "string", "description": "Comma-separated labels (create/update)."},
                "assignee": {"type": "string", "description": "GitLab username to assign (create/update)."},
                "reviewer": {"type": "string", "description": "GitLab username to request review from (create/update)."},
                "milestone_id": {"type": "integer", "description": "Milestone id (create/update)."},
                "draft": {"type": "boolean", "description": "Mark as draft (create/update)."},
                "remove_source_branch": {"type": "boolean", "description": "Delete the source branch on merge (create/merge)."},
                "squash": {"type": "boolean", "description": "Squash commits on merge (merge)."},
                "merge_commit_message": {"type": "string", "description": "Merge commit message (merge)."},
                "body": {"type": "string", "description": "Note body (comment)."},
                "state": {"type": "string", "enum": ["opened", "closed", "merged", "all"], "description": "State filter (list). Default opened."},
                "limit": {"type": "integer", "description": "Max results (list). Default 20."},
                "role": {"type": "string", "description": "Role for the created kanban task (to-kanban). Default reviewer."},
            },
            "required": ["action"],
        },
    },
    "gitlab_milestone": {
        "name": "gitlab_milestone",
        "description": (
            "Manage GitLab milestones as sprints: list, get, create, update, close, reopen, "
            "delete, list a milestone's issues, and report sprint progress (open vs closed "
            "issues, weight, overdue). Use for scrum planning, sprint opening and closing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "get", "create", "update", "close", "reopen", "delete", "issues", "progress"],
                    "description": "What to do.",
                },
                "project": _PROJECT,
                "host": _HOST,
                "group": {"type": "string", "description": "Group path for group-level (cross-project) milestones (list)."},
                "milestone_id": {"type": "integer", "description": "Milestone id (get/update/close/reopen/delete/issues/progress)."},
                "title": {"type": "string", "description": "Milestone title (create/update)."},
                "description": {"type": "string", "description": "Milestone description (create/update)."},
                "start_date": {"type": "string", "description": "Sprint start YYYY-MM-DD (create/update)."},
                "due_date": {"type": "string", "description": "Sprint end YYYY-MM-DD (create/update)."},
                "state": {"type": "string", "enum": ["active", "closed"], "description": "State filter (list)."},
                "limit": {"type": "integer", "description": "Max results (list/issues). Default 20."},
            },
            "required": ["action"],
        },
    },
}
