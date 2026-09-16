"""Devin API (v3) calls the bot needs: create, poll and terminate a session.

https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions
"""

from __future__ import annotations

from typing import Any, Protocol

import requests

DEVIN_API = "https://api.devin.ai/v3"

STRUCTURED_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["fixed", "blocked"]},
        "pr_url": {"type": ["string", "null"]},
        "summary": {"type": "string"},
    },
    "required": ["status", "summary"],
}


class DevinAPI(Protocol):
    def create_session(self, prompt: str, title: str) -> dict[str, Any]: ...
    def get_session(self, session_id: str) -> dict[str, Any]: ...
    def terminate_session(self, session_id: str) -> None: ...


class Devin:
    def __init__(self, org_id: str, api_key: str, max_acu_limit: int = 5) -> None:
        self.max_acu_limit = max_acu_limit
        self.sessions_url = f"{DEVIN_API}/organizations/{org_id}/sessions"
        self.http = requests.Session()
        self.http.headers["Authorization"] = f"Bearer {api_key}"

    def create_session(self, prompt: str, title: str) -> dict[str, Any]:
        response = self.http.post(
            self.sessions_url,
            json={
                "prompt": prompt,
                "title": title,
                "structured_output_schema": STRUCTURED_OUTPUT_SCHEMA,
                "max_acu_limit": self.max_acu_limit,
            },
            timeout=30,
        )
        response.raise_for_status()
        return dict(response.json())

    def get_session(self, session_id: str) -> dict[str, Any]:
        response = self.http.get(f"{self.sessions_url}/{session_id}", timeout=30)
        response.raise_for_status()
        return dict(response.json())

    def terminate_session(self, session_id: str) -> None:
        response = self.http.delete(f"{self.sessions_url}/{session_id}", timeout=30)
        response.raise_for_status()


def outcome_of(session: dict[str, Any]) -> str | None:
    """'finished' / 'blocked' / 'expired' once the session has stopped working, else None.

    v3 reports `status` (new|claimed|running|exit|error|suspended|resuming) plus
    `status_detail` (working|waiting_for_user|finished|...); 'blocked' means Devin is
    waiting on a human.
    """
    status, detail = session.get("status"), session.get("status_detail")
    if detail == "finished" or status == "exit":
        return "finished"
    if detail in {"waiting_for_user", "waiting_for_approval"}:
        return "blocked"
    if status in {"error", "suspended"}:
        return "expired"
    return None


def pr_url_of(session: dict[str, Any]) -> str | None:
    for pr in session.get("pull_requests") or []:
        if pr.get("pr_url"):
            return str(pr["pr_url"])
    output = session.get("structured_output") or {}
    return output.get("pr_url")
