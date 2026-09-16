from dataclasses import replace

from automation.config import Settings
from automation.fakes import FakeDevin, FakeGitHub
from automation.orchestrator import Orchestrator
from automation.state import Store


def make(tmp_path, **overrides):
    settings = replace(
        Settings(),
        dry_run=True,
        poll_interval_seconds=0,
        state_dir=tmp_path,
        **overrides,
    )
    devin, github = FakeDevin(), FakeGitHub()
    return Orchestrator(settings, devin, github, Store(tmp_path), run_id="test"), devin, github


def test_happy_path_opens_pr_and_reports_on_the_issue(tmp_path):
    orchestrator, devin, github = make(tmp_path)

    outcome = orchestrator.remediate(11)

    assert outcome.status == "finished"
    assert outcome.pr_url.endswith("/pull/101")
    assert outcome.acus == 1.4
    # The issue is the audit trail: start comment + result comment.
    assert [number for number, _ in github.comments] == [11, 11]
    assert "devin:pr-open" in github.issues[11].labels
    assert "devin:remediate" not in github.issues[11].labels
    # The prompt must carry the closing keyword or merging won't close the issue.
    assert "Closes #11" in devin.created[0]["prompt"]
    assert devin.created[0]["tags"] == ["superset-remediation", "issue-11"]


def test_duplicate_delivery_does_not_create_a_second_session(tmp_path):
    orchestrator, devin, _ = make(tmp_path)

    orchestrator.remediate(11)
    again = orchestrator.remediate(11)

    assert len(devin.created) == 1
    assert again.skipped and again.reason == "already remediated"


def test_waiting_session_is_nudged_rather_than_restarted(tmp_path):
    orchestrator, devin, _ = make(tmp_path)

    outcome = orchestrator.remediate(13)

    assert len(devin.messages) == 1
    assert len(devin.created) == 1
    assert outcome.pr_url.endswith("/pull/102")


def test_blocked_session_is_labelled_and_reason_surfaced(tmp_path):
    orchestrator, _, github = make(tmp_path)

    outcome = orchestrator.remediate(14)

    assert outcome.pr_url is None
    assert "devin:blocked" in github.issues[14].labels
    assert "Chromium download blocked" in github.comments[-1][1]


def test_concurrency_cap_defers_extra_issues(tmp_path):
    orchestrator, devin, _ = make(tmp_path, max_concurrent_sessions=1)

    orchestrator.remediate(11, wait=False)
    deferred = orchestrator.remediate(13, wait=False)

    assert deferred.skipped and deferred.reason == "concurrency cap reached"
    assert len(devin.created) == 1
