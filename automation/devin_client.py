"""Devin API client.

Speaks v1 by default (flat ``apk_`` key, no org path) and v3 when
``DEVIN_ORG_ID`` is set. Both are normalised into one :class:`SessionState` so
the orchestrator never branches on API version.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import requests

from .config import Settings

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
TERMINAL = {"finished", "blocked", "expired"}


@dataclass
class SessionState:
    """Version-independent view of a session."""

    session_id: str
    url: str
    status: str
    acus: float = 0.0
    pr_url: str | None = None
    structured_output: dict[str, Any] | None = None
    waiting_for_user: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL


class DevinAPI(Protocol):
    def create_session(self, *, prompt: str, title: str, tags: list[str]) -> SessionState: ...
    def get_session(self, session_id: str) -> SessionState: ...
    def send_message(self, session_id: str, message: str) -> None: ...


class DevinClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.http = session or requests.Session()
        self.http.headers.update(
            {
                "Authorization": f"Bearer {settings.devin_api_key}",
                "Content-Type": "application/json",
            }
        )

    # -- transport ------------------------------------------------------

    def _url(self, path: str) -> str:
        base = self.settings.devin_base_url.rstrip("/")
        if self.settings.uses_v3:
            return f"{base}/v3/organizations/{self.settings.devin_org_id}{path}"
        return f"{base}/v1{path}"

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = self._url(path)
        last: Exception | None = None
        for attempt in range(4):
            try:
                response = self.http.request(method, url, timeout=30, **kwargs)
                if response.status_code in RETRYABLE_STATUS:
                    raise requests.HTTPError(
                        f"{response.status_code} from {path}", response=response
                    )
                response.raise_for_status()
                return response.json() if response.content else {}
            except (requests.RequestException, ValueError) as exc:
                last = exc
                time.sleep(2**attempt)
        raise RuntimeError(f"Devin API {method} {path} failed after retries: {last}")

    # -- operations -----------------------------------------------------

    def create_session(self, *, prompt: str, title: str, tags: list[str]) -> SessionState:
        body: dict[str, Any] = {
            "prompt": prompt,
            "title": title,
            "tags": tags,
            "idempotent": True,
            "max_acu_limit": int(self.settings.max_acu_per_session),
        }
        data = self._request("POST", "/sessions", json=body)
        return SessionState(
            session_id=data["session_id"],
            url=data.get("url", ""),
            status="running",
            raw=data,
        )

    def get_session(self, session_id: str) -> SessionState:
        data = self._request("GET", f"/sessions/{session_id}")
        return self._normalise(session_id, data)

    def send_message(self, session_id: str, message: str) -> None:
        self._request("POST", f"/sessions/{session_id}/message", json={"message": message})

    def find_by_tag(self, tag: str) -> list[dict[str, Any]]:
        """Recover issue -> session mappings when local state is lost."""
        data = self._request("GET", "/sessions", params={"tags": tag, "limit": 50})
        return data.get("sessions", [])

    # -- normalisation --------------------------------------------------

    @staticmethod
    def _normalise(session_id: str, data: dict[str, Any]) -> SessionState:
        # v1 exposes status_enum + a single pull_request; v3 exposes
        # status/status_detail + a pull_requests array and acus_consumed.
        status = data.get("status_enum") or data.get("status") or "working"
        detail = str(data.get("status_detail") or data.get("status") or "")

        pr_url = None
        if isinstance(data.get("pull_request"), dict):
            pr_url = data["pull_request"].get("url")
        prs = data.get("pull_requests") or []
        if not pr_url and prs:
            first = prs[0]
            pr_url = first.get("url") if isinstance(first, dict) else first

        if status == "working":
            status = "running"

        return SessionState(
            session_id=session_id,
            url=data.get("url", f"https://app.devin.ai/sessions/{session_id.removeprefix('devin-')}"),
            status=status,
            acus=float(data.get("acus_consumed") or 0.0),
            pr_url=pr_url,
            structured_output=data.get("structured_output"),
            waiting_for_user="waiting_for_user" in detail or "blocked" in detail,
            raw=data,
        )
