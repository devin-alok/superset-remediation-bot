"""In-memory Devin/GitHub doubles used by `remediate simulate` and the tests.

They implement the same protocols as the real clients, so the orchestrator
under simulation is the identical code path — only the transport changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .devin_client import SessionState
from .github_client import Issue

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeGitHub:
    def __init__(self, issues: list[dict[str, Any]] | None = None) -> None:
        data = issues if issues is not None else load_fixture("issues.json")
        self.issues = {item["number"]: Issue.from_api(item) for item in data}
        self.comments: list[tuple[int, str]] = []
        self.label_changes: list[tuple[int, list[str], list[str]]] = []

    def get_issue(self, number: int) -> Issue:
        return self.issues[number]

    def list_labelled(self, label: str) -> list[Issue]:
        return [issue for issue in self.issues.values() if label in issue.labels]

    def comment(self, number: int, body: str) -> None:
        self.comments.append((number, body))

    def set_labels(self, number: int, add: list[str], remove: list[str]) -> None:
        issue = self.issues[number]
        issue.labels = [label for label in issue.labels if label not in remove] + add
        self.label_changes.append((number, add, remove))

    def ensure_labels(self, labels: dict[str, str]) -> None:
        return None

    def pr_check_state(self, pr_url: str) -> str | None:
        return "success"


class FakeDevin:
    """Replays a scripted poll sequence per issue, including a stall + nudge."""

    def __init__(self, script: dict[str, Any] | None = None) -> None:
        self.script = script if script is not None else load_fixture("sessions.json")
        self.created: list[dict[str, Any]] = []
        self.messages: list[tuple[str, str]] = []
        self._cursor: dict[str, int] = {}
        self._by_session: dict[str, list[dict[str, Any]]] = {}

    def create_session(self, *, prompt: str, title: str, tags: list[str]) -> SessionState:
        issue_tag = next(tag for tag in tags if tag.startswith("issue-"))
        number = issue_tag.split("-", 1)[1]
        entry = self.script.get(number) or self.script["default"]
        session_id = f"devin-sim-{number}"
        self.created.append({"prompt": prompt, "title": title, "tags": tags})
        self._by_session[session_id] = entry["polls"]
        self._cursor[session_id] = 0
        return SessionState(
            session_id=session_id,
            url=f"https://app.devin.ai/sessions/sim-{number}",
            status="running",
        )

    def get_session(self, session_id: str) -> SessionState:
        polls = self._by_session[session_id]
        index = min(self._cursor[session_id], len(polls) - 1)
        self._cursor[session_id] = index + 1
        poll = polls[index]
        return SessionState(
            session_id=session_id,
            url=f"https://app.devin.ai/sessions/{session_id}",
            status=poll["status"],
            acus=poll.get("acus", 0.0),
            pr_url=poll.get("pr_url"),
            structured_output=poll.get("structured_output"),
            waiting_for_user=poll.get("waiting_for_user", False),
            raw=poll,
        )

    def send_message(self, session_id: str, message: str) -> None:
        self.messages.append((session_id, message))
        # A nudge unsticks the session: skip past the waiting poll.
        polls = self._by_session[session_id]
        cursor = self._cursor[session_id]
        while cursor < len(polls) and polls[cursor].get("waiting_for_user"):
            cursor += 1
        self._cursor[session_id] = cursor
