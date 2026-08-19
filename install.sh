#!/usr/bin/env bash
# Install the gitlab-kanban plugin into Hermes.
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_HOME/plugins/gitlab-kanban"
SCRIPT_DIR="$HERMES_HOME/scripts"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -L "$PLUGIN_DIR" ]; then
  echo "error: $PLUGIN_DIR is a symlink (dev checkout) — nothing to install."
  echo "       Edits in $here/plugin are already live."
  exit 1
fi

echo "Installing gitlab-kanban plugin -> $PLUGIN_DIR"
mkdir -p "$PLUGIN_DIR" "$SCRIPT_DIR"

# The plugin bundles its four skills under plugin/skills/, registered via
# ctx.register_skill — copying plugin/ carries them along.
rm -rf "$PLUGIN_DIR"
cp -r "$here/plugin" "$PLUGIN_DIR"
find "$PLUGIN_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "Installing scripts -> $SCRIPT_DIR"
cp "$here"/scripts/*.py "$SCRIPT_DIR/"
chmod +x "$SCRIPT_DIR"/gitlab-to-kanban.py \
         "$SCRIPT_DIR"/gitlab-kanban-sync-sweep.py \
         "$SCRIPT_DIR"/gitlab-kanban-sync-back.py \
         "$SCRIPT_DIR"/gitlab-project-manage.py

cat <<EOF

Installed. No third-party Python dependencies — stdlib only.

Next steps:

  1. Put a GitLab token (api scope) in $HERMES_HOME/.env:
       GITLAB_TOKEN=glpat-...
     Optionally a webhook secret (set it BEFORE onboarding projects):
       GITLAB_WEBHOOK_SECRET=<random string>

  2. hermes plugins enable gitlab-kanban        # takes effect next session

  3. Restart Hermes, then:
       hermes gitlab-kanban status

  4. For a self-managed instance:
       hermes gitlab-kanban host add work https://gitlab.mycorp.io --token-env GITLAB_WORK_TOKEN

  5. Subscribe the webhook route. Easiest and safest:
       hermes gitlab-kanban webhook --install

     That subscribes with the correct X-Gitlab-Event header names and verifies
     the result. GitLab announces the event using human-readable names ("Issue
     Hook"), NOT the object_kind from the payload body ("issue") — a route
     subscribed with the body names accepts every delivery and silently drops
     it. To do it by hand:

       hermes webhook subscribe gitlab-to-kanban \\
         --events 'Issue Hook,Merge Request Hook' \\
         --script gitlab-to-kanban.py \\
         --secret "\$GITLAB_WEBHOOK_SECRET" \\
         --description 'GitLab issues and merge requests to kanban'

     Verify afterwards — this is the one step that fails silently:
       hermes gitlab-kanban webhook

  6. Expose it publicly (zrok / ngrok / cloudflared), then:
       hermes gitlab-kanban project onboard mygroup/myrepo

  7. Install the periodic sync sweep:
       hermes cron add --name gitlab-kanban-sync --schedule 'every 5m' \\
         --script gitlab-kanban-sync-sweep.py --no-agent

EOF
