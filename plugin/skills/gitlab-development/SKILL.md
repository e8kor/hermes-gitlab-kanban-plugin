---
name: gitlab-development
description: "Implement a bridged GitLab task: branch, MR, verified done."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [gitlab, development, merge-request, kanban, workflow]
    category: software-development
    related_skills: [gitlab-kanban, gitlab-code-review, gitlab-scrum-master]
---

# GitLab Development Skill

The developer role's playbook for a kanban task bridged from a GitLab issue:
understand the issue, implement on a branch, open a merge request, and complete
the task so the bridge can report back. It does not review other people's code
(that is `gitlab-kanban:gitlab-code-review`) and it does not plan sprints.

## When to Use

You are working a kanban task whose body carries a `gitlab-link:` marker for an
issue, or you were asked to implement a GitLab issue and open a merge request.

## Prerequisites

- The `gitlab-kanban` plugin enabled and its host authenticating
  (`hermes gitlab-kanban status`).
- A local clone of the project, or a workspace the kanban task provides.
- Push rights on the project for the branch you intend to create.

## How to Run

Read the task, then the issue, then work. The GitLab side is the source of
truth for *what* is wanted; the kanban task is the unit of *work*.

```
gitlab_kanban_task   action=show    task_id=<id>
gitlab_issue         action=get     iid=<n>
gitlab_issue         action=notes   iid=<n>     # discussion often changes the ask
```

## Quick Reference

| Step | Call |
|---|---|
| Read the task | `gitlab_kanban_task action=show task_id=<id>` |
| Read the issue | `gitlab_issue action=get iid=<n>` |
| Read the discussion | `gitlab_issue action=notes iid=<n>` |
| Ask a question on the issue | `gitlab_issue action=comment iid=<n> body="..."` |
| Open a draft MR | `gitlab_merge_request action=create source_branch=<b> title="Draft: ..."` |
| Mark ready | `gitlab_merge_request action=update iid=<n> title="<no Draft: prefix>"` |
| Request review | `gitlab_merge_request action=update iid=<n> reviewer=<user>` |
| Comment progress | `gitlab_merge_request action=comment iid=<n> body="..."` |
| Complete the task | `gitlab_kanban_task action=complete task_id=<id> body="<result>"` |

Git itself goes through `terminal`; use `read_file`, `search_files`, and
`patch` for the code.

## Procedure

1. **Read before touching anything.** Task body, then the issue, then its
   notes. The notes frequently narrow or contradict the description.
2. **Restate the ask in one sentence.** If you cannot, the issue is
   underspecified — comment on the issue asking the specific question, and
   block the task rather than guessing.
3. **Locate the code.** `search_files` for the symbols the issue names; trace to
   definitions and call sites. Never invent a file, symbol, or import.
4. **Branch from the default branch.** Name it after the issue:
   `git checkout -b <issue-iid>-<short-slug>`. Confirm you branched from an
   up-to-date base.
5. **Implement the smallest change that satisfies the issue.** Match existing
   style. No drive-by refactors, renames, or reformatting.
6. **Test.** Run the project's real test command and linter. A change is not
   done because it compiles.
7. **Commit** with a message that names the issue: `fix: <what> (#<iid>)`.
   Conventional Commits.
8. **Push and open a merge request.** Draft first if the work is incomplete.
   The description states what changed, why, and how it was verified — and
   references `Closes #<iid>` when the MR fully resolves the issue.
9. **Complete the kanban task** with a result summary that includes the MR URL
   and the verification you actually ran. That summary is what the bridge posts
   back to GitLab, so write it for the issue's audience.

## Pitfalls

- **Do not `git push` to a protected or default branch.** Work on a branch and
  let a merge request carry it. If the branch is protected and you cannot push,
  say so — do not force anything.
- **Do not close the GitLab issue yourself.** Completing the kanban task makes
  the bridge do it (when configured). Closing manually double-notifies and
  desyncs the state file.
- **`Closes #<iid>` in an MR description auto-closes on merge.** Only include it
  when the MR really finishes the issue, otherwise the issue vanishes while work
  remains.
- **`iid` not `id`.** `#42` is the iid. The global id addresses a different
  object.
- **Do not strip the `gitlab-link:` marker** from the task body — sync-back
  needs it.
- **A failing test is the answer, not an obstacle.** If the fix cannot be
  verified, complete the task honestly describing what passed and what did not,
  or block it. Never report a verification you did not run.
- **Merging is not the developer's call by default.** Open the MR, request
  review, stop. Merge only when the user explicitly asks.
- **Secrets stay out of commits, MR descriptions, and notes.** Never paste a
  token or `.env` content into GitLab.

## Verification

Before completing the task:

```bash
git status                 # nothing unintended staged
git log --oneline -3       # message references the issue
<project test command>     # actually run, output seen
<project linter>
```

Then confirm the MR exists and points at the right branches:

```
gitlab_merge_request action=get iid=<mr-iid>
```

The kanban result summary must contain the MR URL and the real test outcome.
