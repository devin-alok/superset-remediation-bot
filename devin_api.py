"""Devin API calls the bot needs: create a session and poll it."""

from __future__ import annotations

from typing import Any, Protocol

import requests

DEVIN_API = "https://api.devin.ai/v1"
TERMINAL_STATES = {"finished", "blocked", "expired"}

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


class Devin:
    def __init__(self, api_key: str) -> None:
        self.http = requests.Session()
        self.http.headers["Authorization"] = f"Bearer {api_key}"

    def create_session(self, prompt: str, title: str) -> dict[str, Any]:
        response = self.http.post(
            f"{DEVIN_API}/sessions",
            json={
                "prompt": prompt,
                "title": title,
                "structured_output_schema": STRUCTURED_OUTPUT_SCHEMA,
            },
            timeout=30,
        )
        response.raise_for_status()
        return dict(response.json())

    def get_session(self, session_id: str) -> dict[str, Any]:
        response = self.http.get(f"{DEVIN_API}/sessions/{session_id}", timeout=30)
        response.raise_for_status()
        return dict(response.json())


def pr_url_of(session: dict[str, Any]) -> str | None:
    pr = session.get("pull_request")
    if isinstance(pr, dict) and pr.get("url"):
        return str(pr["url"])
    output = session.get("structured_output") or {}
    return output.get("pr_url")
