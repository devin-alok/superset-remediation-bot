from __future__ import annotations

from typing import Any

import remediate

ISSUE = {
    "number": 14,
    "title": "cleanup swallows exceptions",
    "body": "Log the exception.",
    "html_url": "https://github.com/devin-alok/superset/issues/14",
}


class FakeGitHub:
    repo = "devin-alok/superset"

    def __init__(self) -> None:
        self.comments: list[str] = []

    def get_issue(self, number: int) -> dict[str, Any]:
        assert number == 14
        return ISSUE

    def comment(self, number: int, body: str) -> None:
        self.comments.append(body)


class FakeDevin:
    def __init__(self, states: list[dict[str, Any]]) -> None:
        self.states = states
        self.created: dict[str, Any] | None = None

    def create_session(self, prompt: str, title: str) -> dict[str, Any]:
        self.created = {"prompt": prompt, "title": title}
        return {"session_id": "devin-abc", "url": "https://app.devin.ai/sessions/abc"}

    def get_session(self, session_id: str) -> dict[str, Any]:
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]


def test_build_prompt_contains_issue_and_pr_contract() -> None:
    prompt = remediate.build_prompt("devin-alok/superset", ISSUE)
    assert "Issue #14: cleanup swallows exceptions" in prompt
    assert "Log the exception." in prompt
    assert "Closes #14" in prompt
    assert "devin/issue-14-" in prompt


def test_remediate_polls_until_finished_and_comments_pr() -> None:
    github = FakeGitHub()
    devin = FakeDevin(
        [
            {"status_enum": "working"},
            {
                "status_enum": "finished",
                "pull_request": {"url": "https://github.com/devin-alok/superset/pull/18"},
                "structured_output": {"status": "fixed", "pr_url": None, "summary": "Logged."},
            },
        ]
    )

    session = remediate.remediate(14, github, devin, poll_seconds=0)

    assert devin.created is not None and "Issue #14" in devin.created["prompt"]
    assert remediate.pr_url_of(session) == "https://github.com/devin-alok/superset/pull/18"
    assert github.comments[0] == "Devin session started: https://app.devin.ai/sessions/abc"
    assert "**Devin session fixed**" in github.comments[1]
    assert "https://github.com/devin-alok/superset/pull/18" in github.comments[1]
    assert "Logged." in github.comments[1]


def test_remediate_reports_blocked_session_without_pr() -> None:
    github = FakeGitHub()
    devin = FakeDevin([{"status_enum": "blocked"}])

    session = remediate.remediate(14, github, devin, poll_seconds=0)

    assert remediate.pr_url_of(session) is None
    assert "**Devin session blocked**" in github.comments[1]
    assert "Pull request: _none_" in github.comments[1]


def test_remediate_times_out() -> None:
    github = FakeGitHub()
    devin = FakeDevin([{"status_enum": "working"}])

    session = remediate.remediate(14, github, devin, poll_seconds=0, timeout_seconds=0)

    assert session["status_enum"] == "timeout"
    assert "**Devin session timeout**" in github.comments[1]


def test_main_rejects_bad_arguments() -> None:
    assert remediate.main([]) == 2
    assert remediate.main(["abc"]) == 2
