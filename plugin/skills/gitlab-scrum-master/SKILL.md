---
name: gitlab-scrum-master
description: "Run sprints on GitLab milestones and the kanban board."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [gitlab, scrum, sprint, milestone, planning, kanban]
    category: project-management
    related_skills: [gitlab-kanban, gitlab-development, gitlab-code-review]
---

# GitLab Scrum Master Skill

The scrum-master role's playbook: run sprints as GitLab milestones, keep the
backlog and the kanban board honest, and report progress from evidence. It does
not write code and does not review merge requests.

## When to Use

- Opening, running, or closing a sprint.
- Grooming a backlog: labelling, weighting, assigning to a milestone.
- Producing a standup or sprint-progress report.
- Reconciling the kanban board with GitLab reality.

## Prerequisites

- The `gitlab-kanban` plugin enabled and its host authenticating.
- The project onboarded, so board tasks and GitLab objects are linked.
- Agreement on what a "sprint" means here: this skill uses one GitLab milestone
  per sprint, group-level when several projects share a cadence.

## How to Run

```
gitlab_milestone  action=list                              # what sprints exist
gitlab_milestone  action=progress   milestone_id=<id>      # the real numbers
gitlab_kanban_task action=list                             # what the agents are doing
```

## Quick Reference

| Need | Call |
|---|---|
| List sprints | `gitlab_milestone action=list [state=active]` |
| Cross-project sprints | `gitlab_milestone action=list group=<group-path>` |
| Open a sprint | `gitlab_milestone action=create title="Sprint 12" start_date=... due_date=...` |
| Sprint contents | `gitlab_milestone action=issues milestone_id=<id>` |
| Sprint progress | `gitlab_milestone action=progress milestone_id=<id>` |
| Close a sprint | `gitlab_milestone action=close milestone_id=<id>` |
| Groom the backlog | `gitlab_issue action=list state=opened` |
| Put an issue in a sprint | `gitlab_issue action=update iid=<n> milestone_id=<id>` |
| Weight an issue | `gitlab_issue action=update iid=<n> weight=<points>` |
| Label for routing | `gitlab_issue action=update iid=<n> labels="bug,backend"` |
| Bridge an issue to the board | `gitlab_issue action=to-kanban iid=<n> role=developer` |
| Reroute a task | `gitlab_kanban_task action=assign task_id=<id> role=reviewer` |
| Board state | `gitlab_kanban_task action=list [status=running]` |

## Procedure — sprint planning

1. **Close the books on the previous sprint first.**
   `gitlab_milestone action=progress` on it. Anything still open either rolls
   over (reassign to the new milestone) or goes back to the backlog — decide
   explicitly, do not let it drift.
2. **Create the sprint** with real dates:
   `gitlab_milestone action=create title="Sprint N" start_date=YYYY-MM-DD due_date=YYYY-MM-DD`.
3. **Groom the candidates.** `gitlab_issue action=list state=opened`. For each
   candidate: is the ask clear enough to implement? If not, comment asking the
   specific question rather than pulling it into the sprint.
4. **Label for routing.** The label decides which role picks the work up (see
   the `gitlab-kanban` skill's role table). An unlabelled issue lands on the
   default role, which is usually wrong.
5. **Weight what you can.** `weight` is the story-point field; progress
   reporting uses it. Unweighted issues make velocity meaningless.
6. **Attach to the milestone** and only then bridge to the board:
   `gitlab_issue action=to-kanban iid=<n> role=<role>`. Bridging is idempotent,
   so a repeat is safe.
7. **State the sprint goal in the milestone description.** One sentence. If it
   takes three, the sprint has three goals.

## Procedure — standup / progress report

1. `gitlab_milestone action=progress milestone_id=<id>` — open vs closed, weight
   burned, and which open issues have no assignee.
2. `gitlab_kanban_task action=list` — what is running, blocked, or in review.
3. `gitlab_merge_request action=list state=opened` — work waiting on review is
   the most common invisible blocker.
4. Report in this order: **done since last / in progress / blocked / at risk of
   the due date**. Name issues by iid and title. No status theatre.
5. Blocked means blocked: say what it is waiting on and who can unblock it.

## Procedure — sprint close

1. `gitlab_milestone action=progress` for the final numbers. Record them before
   moving anything, or the retro has no data.
2. Roll over or backlog every open issue explicitly.
3. `gitlab_milestone action=close milestone_id=<id>`.
4. Reconcile: any board task whose GitLab object is already closed, and any
   closed issue whose task is still open, is a desync — investigate before
   forcing either side.

## Pitfalls

- **Do not close a milestone with open issues silently.** Closing hides them
  from the sprint view while they are still unresolved. Decide each one.
- **Do not mass-update issues without being asked.** Every label, milestone, and
  assignee change notifies people and shows in the activity feed. Bulk changes
  need explicit intent.
- **`milestone_id` is the numeric id, not the title.** Get it from
  `action=list`; titles are not unique across projects and groups.
- **Group milestones need `group=`.** Passing a project path to a cross-project
  sprint silently looks in the wrong place.
- **Percent-complete by issue count lies** when issues are unweighted or wildly
  different in size. Report weight alongside count, and say when weights are
  missing.
- **The board is not the plan.** The kanban board is execution state; GitLab
  issues and milestones are the plan. When they disagree, fix the mismatch
  rather than picking whichever is convenient.
- **Do not reassign a running task without reclaiming it.** A task with a live
  worker claim needs `hermes kanban reclaim` first, or you get two workers on
  one card.
- **Velocity from one sprint is noise.** Do not build commitments on a single
  data point.

## Verification

```
gitlab_milestone   action=progress milestone_id=<id>   # numbers you will quote
gitlab_kanban_task action=list                          # board matches the report
gitlab_issue       action=list state=opened             # nothing unlabelled was pulled in
```

A report is only finished when every claim in it traces to one of those calls.
