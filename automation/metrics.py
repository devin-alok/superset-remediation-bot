"""Everything a reviewer wants to know, derived only from ``events.jsonl``.

Nothing here maintains its own state, so the numbers cannot drift from what
actually happened.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .state import Store


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass
class IssueMetrics:
    issue: int
    session_id: str | None = None
    state: str = "queued"
    acus: float = 0.0
    pr_url: str | None = None
    ci: str | None = None
    nudges: int = 0
    started: str | None = None
    finished: str | None = None

    @property
    def duration_seconds(self) -> float | None:
        if not (self.started and self.finished):
            return None
        return (_ts(self.finished) - _ts(self.started)).total_seconds()


@dataclass
class Summary:
    issues: list[IssueMetrics] = field(default_factory=list)
    triggers: int = 0
    duplicates_suppressed: int = 0
    errors: int = 0

    # Success is a ladder, not a boolean: "session finished" says nothing about
    # whether the issue is actually fixed.
    @property
    def sessions_created(self) -> int:
        return sum(1 for i in self.issues if i.session_id)

    @property
    def level1_completed(self) -> int:
        return sum(1 for i in self.issues if i.state in {"finished", "pr_open", "merged"})

    @property
    def level2_pr_opened(self) -> int:
        return sum(1 for i in self.issues if i.pr_url)

    @property
    def level3_ci_green(self) -> int:
        return sum(1 for i in self.issues if i.ci == "success")

    @property
    def blocked(self) -> int:
        return sum(1 for i in self.issues if i.state in {"blocked", "expired", "timeout"})

    @property
    def total_acus(self) -> float:
        return round(sum(i.acus for i in self.issues), 2)

    @property
    def acus_per_pr(self) -> float | None:
        """The cost metric that matters: spend per reviewable output."""
        if not self.level2_pr_opened:
            return None
        return round(self.total_acus / self.level2_pr_opened, 2)

    @property
    def median_minutes_to_pr(self) -> float | None:
        durations = [
            i.duration_seconds for i in self.issues if i.pr_url and i.duration_seconds
        ]
        if not durations:
            return None
        return round(statistics.median(durations) / 60, 1)

    @property
    def alerts(self) -> list[str]:
        out = []
        # Liveness: a trigger that never became a session is lost work.
        unstarted = self.triggers - self.sessions_created - self.duplicates_suppressed
        if unstarted > 0:
            out.append(f"{unstarted} trigger(s) received but no session created")
        if self.errors:
            out.append(f"{self.errors} API error(s) during the run")
        stuck = [i.issue for i in self.issues if i.state == "timeout"]
        if stuck:
            out.append(f"session(s) timed out on issue(s) {stuck}")
        finished_no_pr = [
            i.issue
            for i in self.issues
            if i.state in {"finished", "no_pr"} and not i.pr_url
        ]
        if finished_no_pr:
            out.append(f"session finished without a PR on issue(s) {finished_no_pr}")
        return out


def summarise(store: Store, ci_states: dict[int, str] | None = None) -> Summary:
    per_issue: dict[int, IssueMetrics] = {}
    summary = Summary()

    for event in store.events():
        issue = int(event["issue"])
        metrics = per_issue.setdefault(issue, IssueMetrics(issue=issue))
        name = event["event"]

        if event.get("session_id"):
            metrics.session_id = event["session_id"]
        if event.get("acus"):
            metrics.acus = max(metrics.acus, float(event["acus"]))
        if event.get("pr_url"):
            metrics.pr_url = event["pr_url"]

        if name == "trigger_received":
            summary.triggers += 1
        elif name == "duplicate_trigger":
            summary.duplicates_suppressed += 1
        elif name == "session_created":
            metrics.started = event["ts"]
            metrics.state = "running"
        elif name == "nudge_sent":
            metrics.nudges += 1
        elif name == "session_polled" and event.get("status"):
            metrics.state = str(event["status"])
        elif name in {"session_finished", "session_blocked"}:
            metrics.finished = event["ts"]
            metrics.state = str(event.get("outcome") or event.get("status") or name)
        elif name == "session_timeout":
            metrics.finished = event["ts"]
            metrics.state = "timeout"
        elif name == "session_error":
            summary.errors += 1
            metrics.state = "error"

    for issue, state in (ci_states or {}).items():
        if issue in per_issue:
            per_issue[issue].ci = state

    summary.issues = [per_issue[key] for key in sorted(per_issue)]
    return summary


def as_dict(summary: Summary) -> dict[str, Any]:
    return {
        "triggers": summary.triggers,
        "duplicates_suppressed": summary.duplicates_suppressed,
        "sessions_created": summary.sessions_created,
        "level1_session_completed": summary.level1_completed,
        "level2_pr_opened": summary.level2_pr_opened,
        "level3_ci_green": summary.level3_ci_green,
        "blocked": summary.blocked,
        "total_acus": summary.total_acus,
        "acus_per_pr": summary.acus_per_pr,
        "median_minutes_to_pr": summary.median_minutes_to_pr,
        "alerts": summary.alerts,
        "issues": [
            {
                "issue": i.issue,
                "state": i.state,
                "session_id": i.session_id,
                "pr_url": i.pr_url,
                "ci": i.ci,
                "acus": i.acus,
                "nudges": i.nudges,
                "duration_seconds": i.duration_seconds,
            }
            for i in summary.issues
        ],
    }
