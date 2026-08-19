"""GitLab <-> Hermes Kanban bridge plugin.

Bridges GitLab issues and merge requests onto a Hermes kanban board, assigns
work by role, and syncs completed tasks back to GitLab. Multi-host: gitlab.com
and any number of self-managed instances live side by side.
"""

from __future__ import annotations

from pathlib import Path

__version__ = "1.0.0"

TOOLSET = "gitlab-kanban"

SKILLS = (
    "gitlab-kanban",
    "gitlab-development",
    "gitlab-code-review",
    "gitlab-scrum-master",
)


def register(ctx) -> None:
    """Wire tools, slash command, CLI subcommands, and skills into Hermes."""
    from .schemas import SCHEMAS
    from .tools import HANDLERS
    from .cli import handle_cli, setup_cli
    from .slash import handle_slash

    for name, schema in SCHEMAS.items():
        handler = HANDLERS.get(name)
        if handler is None:
            # A schema with no handler would be a tool that silently does not
            # exist. Skip it loudly rather than registering a broken surface.
            continue
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=schema,
            handler=handler,
        )

    ctx.register_command(
        name="gitlab-kanban",
        handler=handle_slash,
        description="GitLab-Kanban bridge: status, projects, board, issues, mrs, sprints, sync",
        args_hint="<status|projects|board|issues|mrs|sprints|sync|config|webhook|help>",
    )

    ctx.register_cli_command(
        name="gitlab-kanban",
        help="GitLab-Kanban bridge: hosts, projects, issues, merge requests, milestones, sync",
        setup_fn=setup_cli,
        handler_fn=handle_cli,
    )

    skills_root = Path(__file__).parent / "skills"
    for skill in SKILLS:
        path = skills_root / skill / "SKILL.md"
        if path.exists():
            ctx.register_skill(skill, path)
