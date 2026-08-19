---
name: gitlab-code-review
description: "Review a GitLab merge request with evidence, not vibes."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [gitlab, code-review, merge-request, qa, kanban]
    category: software-development
    related_skills: [gitlab-kanban, gitlab-development, gitlab-scrum-master]
---

# GitLab Code Review Skill

The reviewer and QA role's playbook for a bridged merge-request task: read the
diff, verify the claim, and leave a review that is specific and actionable. It
does not implement the fix and does not merge unless explicitly asked.

## When to Use

You are working a kanban task whose body carries a
`gitlab-link: <host> merge_request ...` marker, or you were asked to review a
GitLab merge request.

## Prerequisites

- The `gitlab-kanban` plugin enabled and its host authenticating.
- Read access to the project; approval rights only if you will approve.
- A local clone when the review needs the tests actually run.

## How to Run

```
gitlab_kanban_task     action=show    task_id=<id>
gitlab_merge_request   action=get     iid=<n>
gitlab_merge_request   action=changes iid=<n>     # the diff, file by file
```

## Quick Reference

| Step | Call |
|---|---|
| MR metadata + merge status | `gitlab_merge_request action=get iid=<n>` |
| Changed files and diffs | `gitlab_merge_request action=changes iid=<n>` |
| The issue it claims to fix | `gitlab_issue action=get iid=<issue-iid>` |
| Leave the review | `gitlab_merge_request action=comment iid=<n> body="..."` |
| Approve | `gitlab_merge_request action=approve iid=<n>` |
| Merge (only when asked) | `gitlab_merge_request action=merge iid=<n> [squash=true]` |
| Finish the task | `gitlab_kanban_task action=complete task_id=<id> body="<verdict>"` |

## Procedure

1. **Establish the claim.** Read the MR description and the linked issue. What
   is this change supposed to do? A review without a claim to check is just
   style commentary.
2. **Read the whole diff.** `action=changes` returns per-file diffs. For a large
   MR, read every file — a bug hides in the file nobody looked at.
3. **Check the claim is actually met.** Does the diff do what the description
   says? Anything extra is scope creep worth flagging.
4. **Look for the bug class, not one instance.** When you find a flaw, check
   sibling call paths for the same flaw and say so.
5. **Check the things reviewers skip:** error handling, null/empty cases,
   concurrency on shared state, resource cleanup, backward compatibility of
   public signatures, and whether new dependencies are pinned.
6. **Check the tests.** Are there any? Do they assert behaviour, or do they
   snapshot current values (a change-detector)? Does any test read source text
   instead of calling code? Both are defects.
7. **Run it when the verdict depends on it.** Check out the branch and run the
   project's tests. Say in the review whether you ran them or only read.
8. **Write the review.** Order findings by severity: blocking, then should-fix,
   then nit. Every finding names `path:line` and states the consequence. No
   praise padding, no vague "consider refactoring".
9. **Verdict.** Approve only if you would ship it. Otherwise request changes
   with the blocking list. Then complete the kanban task with the verdict —
   that text goes back onto the MR.

## Review Comment Shape

```
**Blocking**
- `src/foo.py:42` — `parse()` raises on empty input; the webhook path calls it
  with an unvalidated body, so a malformed payload 500s the gateway.

**Should fix**
- `src/foo.py:88` — same missing guard on the sibling `parse_batch()` path.

**Nit**
- `tests/test_foo.py:12` — asserts the exact model list; it will break on the
  next catalog update. Assert the invariant instead.

Verified: ran `pytest tests/foo` (green), did not run the integration suite.
```

## Pitfalls

- **Approving is a team-visible act with consequences.** Do not approve to be
  agreeable. If you did not read the whole diff, say so instead.
- **Never merge unless the user asked.** Merge is irreversible in practice and
  can trigger deploys. Reviewing ≠ merging.
- **Do not push commits to someone else's MR branch.** Describe the fix; let the
  author apply it.
- **A green pipeline is not a review.** CI proves the tests that exist passed,
  nothing about the tests that should exist.
- **Do not review the author.** Findings are about code and consequences.
- **Do not leak internals into a public note.** Reviews on a public project are
  public — no paths from your machine, no logs with tokens.
- **`iid` not `id`** for both MRs (`!17`) and issues (`#42`).
- **Draft MRs are not ready for a verdict.** If it is still `Draft:`, say what
  you saw and leave it open rather than requesting changes on unfinished work.

## Verification

Before completing the task, be able to answer:

- Which files did I read? (all of them, or which subset and why)
- Did I run the tests, or only read them?
- Does every blocking finding cite `path:line` and a consequence?
- Is my verdict consistent with the findings I wrote?

```
gitlab_merge_request action=get iid=<n>    # confirm the note landed and the state
```
