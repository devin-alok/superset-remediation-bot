from __future__ import annotations

from typing import Any

import devin_api
import remediate

ISSUE = {
    "number": 14,
    "title": "cleanup swallows exceptions",
    "body": "Log the exception.",
    "html_url": "https://github.com/devin-alok/superset/issues/14",
}


class FakeGitHub:
    repo = "devin-alok/superset"

    def __init__(self, labels: list[str] | None = None) -> None:
        self.posted: list[str] = []
        self.labels = labels or []

    def get_issue(self, number: int) -> dict[str, Any]:
        assert number == 14
        return ISSUE

    def list_issues(self, label: str) -> list[dict[str, Any]]:
        return [ISSUE] if label in self.labels else []

    def comments(self, number: int) -> list[str]:
        return self.posted

    def comment(self, number: int, body: str) -> None:
        self.posted.append(body)

    def relabel(self, number: int, old: str, new: str) -> None:
        assert number == 14
        self.labels.remove(old)
        self.labels.append(new)


class FakeDevin:
    def __init__(self, states: list[dict[str, Any]]) -> None:
        self.states = states
        self.created: dict[str, Any] | None = None
        self.polled: list[str] = []

    def create_session(self, prompt: str, title: str) -> dict[str, Any]:
        self.created = {"prompt": prompt, "title": title}
        return {"session_id": "devin-abc", "url": "https://app.devin.ai/sessions/abc"}

    def get_session(self, session_id: str) -> dict[str, Any]:
        self.polled.append(session_id)
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
            {"status": "running", "status_detail": "working"},
            {
                "status": "exit",
                "pull_requests": [{"pr_url": "https://github.com/devin-alok/superset/pull/18"}],
                "structured_output": {"status": "fixed", "pr_url": None, "summary": "Logged."},
            },
        ]
    )

    session = remediate.remediate(14, github, devin, poll_seconds=0)

    assert devin.created is not None and "Issue #14" in devin.created["prompt"]
    assert devin_api.pr_url_of(session) == "https://github.com/devin-alok/superset/pull/18"
    assert github.posted[0] == "Devin session started: https://app.devin.ai/sessions/abc"
    assert "**Devin session fixed**" in github.posted[1]
    assert "https://github.com/devin-alok/superset/pull/18" in github.posted[1]
    assert "Logged." in github.posted[1]


def test_remediate_reports_blocked_session_without_pr() -> None:
    github = FakeGitHub()
    devin = FakeDevin([{"status": "running", "status_detail": "waiting_for_user"}])

    session = remediate.remediate(14, github, devin, poll_seconds=0)

    assert devin_api.pr_url_of(session) is None
    assert "**Devin session blocked**" in github.posted[1]
    assert "Pull request: _none_" in github.posted[1]


def test_remediate_times_out() -> None:
    github = FakeGitHub()
    devin = FakeDevin([{"status": "running", "status_detail": "working"}])

    session = remediate.remediate(14, github, devin, poll_seconds=0, timeout_seconds=0)

    assert session["outcome"] == "timeout"
    assert "**Devin session timeout**" in github.posted[1]


def test_watch_moves_labelled_issue_through_lifecycle() -> None:
    github = FakeGitHub(labels=["devin:remediate"])
    devin = FakeDevin(
        [
            {"status": "running", "status_detail": "working"},
            {"status": "exit", "pull_requests": [{"pr_url": "https://x/pull/1"}]},
        ]
    )

    remediate.watch(github, devin, poll_seconds=0, once=True)

    assert github.labels == ["devin:pr-open"]
    assert github.posted[0] == "Devin session started: https://app.devin.ai/sessions/abc"
    assert "https://x/pull/1" in github.posted[1]
    assert len(github.posted) == 2


def test_watch_labels_blocked_when_no_pr() -> None:
    github = FakeGitHub(labels=["devin:remediate"])
    devin = FakeDevin([{"status": "running", "status_detail": "waiting_for_user"}])

    remediate.watch(github, devin, once=True)

    assert github.labels == ["devin:blocked"]


def test_watch_resumes_in_progress_issue_after_restart() -> None:
    github = FakeGitHub(labels=["devin:in-progress"])
    github.posted = ["Devin session started: https://app.devin.ai/sessions/old"]
    devin = FakeDevin([{"status": "exit", "pull_requests": [{"pr_url": "https://x/pull/2"}]}])

    remediate.watch(github, devin, poll_seconds=0, once=True)

    assert devin.created is None  # no new session
    assert devin.polled == ["devin-old"]
    assert github.labels == ["devin:pr-open"]
    assert "https://x/pull/2" in github.posted[1]


def test_watch_requeues_in_progress_issue_without_session_comment() -> None:
    github = FakeGitHub(labels=["devin:in-progress"])
    devin = FakeDevin([{"status": "exit"}])

    remediate.watch(github, devin, poll_seconds=0, once=True)

    assert devin.created is not None  # picked up again via devin:remediate
    assert github.labels == ["devin:blocked"]


def test_main_rejects_bad_arguments() -> None:
    assert remediate.main(["abc"]) == 2
    assert remediate.main(["1", "2"]) == 2
