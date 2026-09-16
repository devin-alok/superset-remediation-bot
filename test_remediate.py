from __future__ import annotations

from typing import Any

import remediate

ISSUE = {
    "number": 14,
    "title": "cleanup swallows exceptions",
    "body": "Log the exception.",
    "html_url": "https://github.com/devin-alok/superset/issues/14",
}
STARTED = "Devin session started: https://app.devin.ai/sessions/abc"


class FakeGitHub:
    repo = "devin-alok/superset"

    def __init__(self, label: str, posted: list[str] | None = None) -> None:
        self.label = label
        self.posted = posted or []

    def list_issues(self, label: str) -> list[dict[str, Any]]:
        return [ISSUE] if label == self.label else []

    def comments(self, number: int) -> list[str]:
        return self.posted

    def comment(self, number: int, body: str) -> None:
        self.posted.append(body)

    def relabel(self, number: int, old: str, new: str) -> None:
        assert (number, old) == (14, self.label)
        self.label = new


class FakeDevin:
    def __init__(self, session: dict[str, Any] | None = None) -> None:
        self.session = session
        self.created: dict[str, Any] | None = None
        self.polled: list[str] = []

    def create_session(self, prompt: str, title: str) -> dict[str, Any]:
        self.created = {"prompt": prompt, "title": title}
        return {"session_id": "devin-abc", "url": "https://app.devin.ai/sessions/abc"}

    def get_session(self, session_id: str) -> dict[str, Any]:
        self.polled.append(session_id)
        assert self.session is not None
        return self.session


def test_build_prompt_contains_issue_and_pr_contract() -> None:
    prompt = remediate.build_prompt("devin-alok/superset", ISSUE)
    assert "Issue #14: cleanup swallows exceptions" in prompt
    assert "Log the exception." in prompt
    assert "Closes #14" in prompt
    assert "devin/issue-14-" in prompt


def test_tick_starts_session_for_labelled_issue() -> None:
    github, devin = FakeGitHub("devin:remediate"), FakeDevin()

    remediate.tick(github, devin)

    assert devin.created is not None and "Issue #14" in devin.created["prompt"]
    assert github.label == "devin:in-progress"
    assert github.posted == [STARTED]


def test_tick_leaves_running_session_alone() -> None:
    github = FakeGitHub("devin:in-progress", [STARTED])
    devin = FakeDevin({"status": "running", "status_detail": "working"})

    remediate.tick(github, devin)

    assert devin.polled == ["devin-abc"]
    assert devin.created is None
    assert github.label == "devin:in-progress"
    assert github.posted == [STARTED]


def test_tick_comments_pr_and_labels_pr_open_when_finished() -> None:
    github = FakeGitHub("devin:in-progress", [STARTED])
    devin = FakeDevin(
        {
            "status": "exit",
            "pull_requests": [{"pr_url": "https://github.com/devin-alok/superset/pull/18"}],
            "structured_output": {"status": "fixed", "summary": "Logged."},
        }
    )

    remediate.tick(github, devin)

    assert github.label == "devin:pr-open"
    assert "**Devin session fixed**" in github.posted[1]
    assert "https://github.com/devin-alok/superset/pull/18" in github.posted[1]
    assert "Logged." in github.posted[1]


def test_tick_labels_blocked_when_no_pr() -> None:
    github = FakeGitHub("devin:in-progress", [STARTED])
    devin = FakeDevin({"status": "running", "status_detail": "waiting_for_user"})

    remediate.tick(github, devin)

    assert github.label == "devin:blocked"
    assert "**Devin session blocked**" in github.posted[1]
    assert "Pull request: _none_" in github.posted[1]


def test_tick_requeues_in_progress_issue_without_session_comment() -> None:
    github, devin = FakeGitHub("devin:in-progress"), FakeDevin()

    remediate.tick(github, devin)

    assert devin.polled == []
    assert devin.created is not None  # requeued and started fresh in the same tick
    assert github.label == "devin:in-progress"
    assert github.posted == [STARTED]


def test_session_url_of_uses_latest_started_comment() -> None:
    comments = [
        "Devin session started: https://x/1",
        "unrelated",
        "Devin session started: https://x/2 ",
    ]
    assert remediate.session_url_of(comments) == "https://x/2"
    assert remediate.session_url_of(["unrelated"]) is None
