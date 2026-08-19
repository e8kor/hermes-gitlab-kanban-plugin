#!/usr/bin/env python3
"""GitLab webhook -> kanban task bridge.

Reads a GitLab webhook payload (JSON) on stdin. If it is an issue or merge
request event the bridge is configured to ingest, creates a kanban task on the
configured board assigned to the role the labels imply.

Exit 0 + ``[SILENT]`` on stdout -> webhook returns 200, no agent run (the kanban
dispatcher picks the task up). Diagnostics go to stderr so they never leak into
the agent surface. Exit 0 with empty stdout means "ignored".

Webhook payloads are untrusted network input: nothing from the payload is ever
interpolated into a shell command or a filesystem path, and ingest is idempotent
via a deterministic kanban idempotency key, so a redelivery cannot duplicate a
task.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gitlab_kanban_loader import load_module  # noqa: E402


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        print("ignored: payload is not JSON", file=sys.stderr)
        return 0

    try:
        bridge = load_module("bridge")
        result = bridge.ingest_event(payload)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 0
    except Exception as exc:  # noqa: BLE001 - a webhook must never 500 the gateway
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 0

    print(json.dumps(result, default=str), file=sys.stderr)
    if result.get("created"):
        print("[SILENT]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
