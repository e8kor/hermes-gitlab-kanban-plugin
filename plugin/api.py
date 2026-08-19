"""Minimal GitLab REST v4 client — stdlib only, multi-host aware.

Every call resolves its base URL and token from the bridge config's ``hosts``
map, so gitlab.com and any number of self-managed instances work side by side.
Tokens are read from the environment or the profile ``.env``; they are never
written to the config and never printed.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import load_config, resolve_host
from .paths import read_env_value

USER_AGENT = "hermes-gitlab-kanban/1.0"
TIMEOUT = 30


class GitLabError(RuntimeError):
    """A GitLab API call failed. Carries the HTTP status and parsed body."""

    def __init__(self, status: int, message: str, body: Any = None):
        super().__init__(f"GitLab API {status}: {message}")
        self.status = status
        self.body = body


def encode_project(path_or_id: str | int) -> str:
    """URL-encode a project path (``group/sub/repo``) or pass an id through."""
    if isinstance(path_or_id, int) or str(path_or_id).isdigit():
        return str(path_or_id)
    return urllib.parse.quote(str(path_or_id).strip().strip("/"), safe="")


class GitLabClient:
    """Thin REST client for one GitLab host."""

    def __init__(
        self,
        url: str,
        token: str | None,
        *,
        verify_ssl: bool = True,
        alias: str = "",
    ):
        self.url = url.rstrip("/")
        self.token = token
        self.verify_ssl = verify_ssl
        self.alias = alias or url

    # ---- construction -------------------------------------------------

    @classmethod
    def for_host(cls, alias: str | None = None, cfg: dict[str, Any] | None = None) -> "GitLabClient":
        """Build a client for a configured host alias."""
        cfg = cfg or load_config()
        host = resolve_host(cfg, alias)
        token = read_env_value(host["token_env"])
        return cls(
            host["url"],
            token,
            verify_ssl=host["verify_ssl"],
            alias=host["alias"],
        )

    # ---- transport ----------------------------------------------------

    def _context(self) -> ssl.SSLContext | None:
        if self.verify_ssl:
            return None
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        """Perform one API call. Returns ``(status, parsed_body)``."""
        if not self.token:
            raise GitLabError(401, f"no token for host {self.alias} (set its token_env in .env)")
        query = ""
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                query = "?" + urllib.parse.urlencode(clean, doseq=True)
        url = f"{self.url}/api/v4{path}{query}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method.upper(),
            headers={
                "PRIVATE-TOKEN": self.token,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=self._context()) as resp:
                raw = resp.read()
                if not raw:
                    return resp.status, {}
                try:
                    return resp.status, json.loads(raw)
                except json.JSONDecodeError:
                    return resp.status, {"raw": raw.decode("utf-8", "replace")}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {"raw": raw.decode("utf-8", "replace")}
            message = ""
            if isinstance(body, dict):
                message = str(body.get("message") or body.get("error") or "")
            raise GitLabError(exc.code, message or exc.reason or "request failed", body) from exc
        except urllib.error.URLError as exc:
            raise GitLabError(0, f"cannot reach {self.url}: {exc.reason}") from exc

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params)[1]

    def post(self, path: str, payload: dict[str, Any] | None = None, **params: Any) -> Any:
        return self.request("POST", path, params=params, payload=payload or {})[1]

    def put(self, path: str, payload: dict[str, Any] | None = None, **params: Any) -> Any:
        return self.request("PUT", path, params=params, payload=payload or {})[1]

    def delete(self, path: str, **params: Any) -> int:
        return self.request("DELETE", path, params=params)[0]

    def paginate(self, path: str, *, limit: int = 100, **params: Any) -> list[Any]:
        """Fetch up to ``limit`` records across pages."""
        out: list[Any] = []
        page = 1
        per_page = min(100, max(1, limit))
        while len(out) < limit:
            batch = self.get(path, page=page, per_page=per_page, **params)
            if not isinstance(batch, list) or not batch:
                break
            out.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return out[:limit]

    # ---- identity -----------------------------------------------------

    def whoami(self) -> dict[str, Any]:
        return self.get("/user")

    # ---- projects -----------------------------------------------------

    def project(self, project: str) -> dict[str, Any]:
        return self.get(f"/projects/{encode_project(project)}")

    def project_labels(self, project: str, limit: int = 100) -> list[Any]:
        return self.paginate(f"/projects/{encode_project(project)}/labels", limit=limit)

    def project_members(self, project: str, limit: int = 100) -> list[Any]:
        return self.paginate(f"/projects/{encode_project(project)}/members/all", limit=limit)

    # ---- issues -------------------------------------------------------

    def list_issues(self, project: str, limit: int = 50, **filters: Any) -> list[Any]:
        return self.paginate(f"/projects/{encode_project(project)}/issues", limit=limit, **filters)

    def get_issue(self, project: str, iid: int) -> dict[str, Any]:
        return self.get(f"/projects/{encode_project(project)}/issues/{iid}")

    def create_issue(self, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post(f"/projects/{encode_project(project)}/issues", payload)

    def update_issue(self, project: str, iid: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self.put(f"/projects/{encode_project(project)}/issues/{iid}", payload)

    def delete_issue(self, project: str, iid: int) -> int:
        return self.delete(f"/projects/{encode_project(project)}/issues/{iid}")

    def comment_issue(self, project: str, iid: int, body: str) -> dict[str, Any]:
        return self.post(f"/projects/{encode_project(project)}/issues/{iid}/notes", {"body": body})

    def issue_notes(self, project: str, iid: int, limit: int = 50) -> list[Any]:
        return self.paginate(
            f"/projects/{encode_project(project)}/issues/{iid}/notes", limit=limit
        )

    # ---- merge requests ----------------------------------------------

    def list_merge_requests(self, project: str, limit: int = 50, **filters: Any) -> list[Any]:
        return self.paginate(
            f"/projects/{encode_project(project)}/merge_requests", limit=limit, **filters
        )

    def get_merge_request(self, project: str, iid: int) -> dict[str, Any]:
        return self.get(f"/projects/{encode_project(project)}/merge_requests/{iid}")

    def create_merge_request(self, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post(f"/projects/{encode_project(project)}/merge_requests", payload)

    def update_merge_request(self, project: str, iid: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self.put(f"/projects/{encode_project(project)}/merge_requests/{iid}", payload)

    def merge_merge_request(self, project: str, iid: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.put(
            f"/projects/{encode_project(project)}/merge_requests/{iid}/merge", payload or {}
        )

    def comment_merge_request(self, project: str, iid: int, body: str) -> dict[str, Any]:
        return self.post(
            f"/projects/{encode_project(project)}/merge_requests/{iid}/notes", {"body": body}
        )

    def approve_merge_request(self, project: str, iid: int) -> dict[str, Any]:
        return self.post(f"/projects/{encode_project(project)}/merge_requests/{iid}/approve")

    def merge_request_changes(self, project: str, iid: int) -> dict[str, Any]:
        return self.get(f"/projects/{encode_project(project)}/merge_requests/{iid}/changes")

    # ---- milestones ---------------------------------------------------

    def list_milestones(self, project: str, limit: int = 50, **filters: Any) -> list[Any]:
        return self.paginate(
            f"/projects/{encode_project(project)}/milestones", limit=limit, **filters
        )

    def create_milestone(self, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post(f"/projects/{encode_project(project)}/milestones", payload)

    def update_milestone(self, project: str, mid: int, payload: dict[str, Any]) -> dict[str, Any]:
        return self.put(f"/projects/{encode_project(project)}/milestones/{mid}", payload)

    def delete_milestone(self, project: str, mid: int) -> int:
        return self.delete(f"/projects/{encode_project(project)}/milestones/{mid}")

    def milestone_issues(self, project: str, mid: int, limit: int = 100) -> list[Any]:
        return self.paginate(
            f"/projects/{encode_project(project)}/milestones/{mid}/issues", limit=limit
        )

    # ---- group milestones (for cross-project sprints) -----------------

    def list_group_milestones(self, group: str, limit: int = 50, **filters: Any) -> list[Any]:
        return self.paginate(f"/groups/{encode_project(group)}/milestones", limit=limit, **filters)

    # ---- webhooks -----------------------------------------------------

    def list_hooks(self, project: str) -> list[Any]:
        return self.paginate(f"/projects/{encode_project(project)}/hooks", limit=100)

    def create_hook(self, project: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.post(f"/projects/{encode_project(project)}/hooks", payload)

    def delete_hook(self, project: str, hook_id: int) -> int:
        return self.delete(f"/projects/{encode_project(project)}/hooks/{hook_id}")
