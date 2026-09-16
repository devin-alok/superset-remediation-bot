from dataclasses import replace

from automation.config import Settings
from automation.fakes import FakeDevin, FakeGitHub
from automation.metrics import summarise
from automation.orchestrator import Orchestrator
from automation.report import render_markdown
from automation.state import Store


def run_all(tmp_path):
    settings = replace(Settings(), dry_run=True, poll_interval_seconds=0, state_dir=tmp_path)
    store = Store(tmp_path)
    orchestrator = Orchestrator(settings, FakeDevin(), FakeGitHub(), store, run_id="test")
    orchestrator.sweep()
    return store


def test_success_ladder_separates_finished_from_pr_opened(tmp_path):
    summary = summarise(run_all(tmp_path), ci_states={11: "success", 13: "failure"})

    assert summary.sessions_created == 3
    assert summary.level2_pr_opened == 2
    assert summary.level3_ci_green == 1  # a finished session is not a green PR
    assert summary.blocked == 1


def test_cost_is_reported_per_pr_not_per_session(tmp_path):
    summary = summarise(run_all(tmp_path))

    assert summary.total_acus == 3.2
    assert summary.acus_per_pr == 1.6


def test_finished_without_pr_raises_an_alert(tmp_path):
    store = run_all(tmp_path)
    store.emit("test", 99, "trigger_received")
    store.emit("test", 99, "session_created", session_id="devin-x")
    store.emit("test", 99, "session_finished", status="finished", outcome="no_pr")

    summary = summarise(store)

    assert any("without a PR" in alert for alert in summary.alerts)


def test_report_renders_every_issue_row(tmp_path):
    markdown = render_markdown(summarise(run_all(tmp_path)), "devin-alok/superset")

    for number in (11, 13, 14):
        assert f"issues/{number}" in markdown
