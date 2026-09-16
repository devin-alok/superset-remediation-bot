"""The core loop: issue -> Devin session -> poll/nudge -> PR -> issue update.

Devin owns the coding work; this module owns supervision. It is deliberately
boring: start at most one session per issue, record every transition, and push
the outcome back onto the issue where a reviewer will see it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .config import Settings
from .devin_client import DevinAPI, SessionState
from .github_client import GitHubAPI, Issue
from .prompt import build_prompt, session_tags, session_title
from .state import Store, new_run_id, utcnow

NUDGE = (
    "Continue with the plan. Make a decision yourself and proceed; only stop if "
    "you are genuinely blocked on credentials or repository access, and if so "
    "report the blocker via structured output."
)

LABEL_COLORS = {
    "devin:remediate": "0e8a16",
    "devin:in-progress": "fbca04",
    "devin:pr-open": "1d76db",
    "devin:blocked": "b60205",
}


@dataclass
class Outcome:
    issue: int
    session_id: str
    session_url: str
    status: str
    pr_url: str | None
    acus: float
    skipped: bool = False
    reason: str | None = None


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        devin: DevinAPI,
        github: GitHubAPI,
        store: Store,
        run_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.devin = devin
        self.github = github
        self.store = store
        self.run_id = run_id or new_run_id()

    # -- entry points ---------------------------------------------------

    def remediate(self, number: int, wait: bool = True) -> Outcome:
        """Start (or resume) remediation of one issue. Safe to call twice."""
        self.store.emit(self.run_id, number, "trigger_received")
        issue = self.github.get_issue(number)

        existing = self.store.get(number)
        if existing and existing.finished_at is None:
            # Redelivered webhook or overlapping sweep: attach, never duplicate.
            self.store.emit(
                self.run_id, number, "duplicate_trigger", session_id=existing.session_id
            )
            return (
                self._watch(issue, existing.session_id, existing.session_url or "")
                if wait
                else Outcome(
                    number,
                    existing.session_id,
                    existing.session_url or "",
                    existing.status,
                    existing.pr_url,
                    existing.acus,
                    skipped=True,
                    reason="already running",
                )
            )
        if existing and existing.finished_at is not None:
            return Outcome(
                number,
                existing.session_id,
                existing.session_url or "",
                existing.status,
                existing.pr_url,
                existing.acus,
                skipped=True,
                reason="already remediated",
            )

        if self.store.active_count() >= self.settings.max_concurrent_sessions:
            self.store.emit(self.run_id, number, "deferred_concurrency_cap")
            return Outcome(number, "", "", "deferred", None, 0.0, skipped=True,
                           reason="concurrency cap reached")

        session = self.devin.create_session(
            prompt=build_prompt(issue, self.settings.repo),
            title=session_title(issue),
            tags=session_tags(issue),
        )
        self.store.create(number, session.session_id, session.url, self.run_id)
        self.store.emit(
            self.run_id, number, "session_created", session_id=session.session_id
        )
        self._announce(issue, session)

        if not wait:
            return Outcome(number, session.session_id, session.url, "running", None, 0.0)
        return self._watch(issue, session.session_id, session.url)

    def sweep(self, wait: bool = True) -> list[Outcome]:
        """Reconcile: every labelled issue without a live session gets one.

        This is the safety net for a dropped webhook delivery, and the path the
        scheduled workflow uses.
        """
        outcomes = []
        for issue in self.github.list_labelled(self.settings.trigger_label):
            outcomes.append(self.remediate(issue.number, wait=wait))
        return outcomes

    # -- supervision ----------------------------------------------------

    def _watch(self, issue: Issue, session_id: str, session_url: str) -> Outcome:
        deadline = time.monotonic() + self.settings.session_timeout_seconds
        record = self.store.get(issue.number)
        nudges = record.nudges if record else 0
        state: SessionState | None = None

        while True:
            state = self.devin.get_session(session_id)
            self.store.update(issue.number, status=state.status, acus=state.acus)
            self.store.emit(
                self.run_id,
                issue.number,
                "session_polled",
                session_id=session_id,
                status=state.status,
                acus=state.acus,
                pr_url=state.pr_url,
            )

            if state.is_terminal:
                break

            if state.waiting_for_user and nudges < self.settings.max_nudges:
                # Cheaper than restarting: the session keeps its context.
                self.devin.send_message(session_id, NUDGE)
                nudges += 1
                self.store.update(issue.number, nudges=nudges)
                self.store.emit(
                    self.run_id, issue.number, "nudge_sent", session_id=session_id
                )

            if time.monotonic() > deadline:
                self.store.emit(
                    self.run_id, issue.number, "session_timeout", session_id=session_id
                )
                self.store.update(issue.number, outcome="timeout", finished_at=utcnow())
                self._report(issue, state, timed_out=True)
                return Outcome(
                    issue.number, session_id, session_url, "timeout", state.pr_url, state.acus
                )

            time.sleep(self.settings.poll_interval_seconds)

        return self._finish(issue, state, session_url)

    def _finish(self, issue: Issue, state: SessionState, session_url: str) -> Outcome:
        output = state.structured_output or {}
        pr_url = state.pr_url or output.get("pr_url")
        outcome = "pr_open" if pr_url else (
            "blocked" if state.status in {"blocked", "expired"} else "no_pr"
        )

        if pr_url:
            self.store.emit(
                self.run_id, issue.number, "pr_opened", session_id=state.session_id,
                pr_url=pr_url,
            )
        self.store.emit(
            self.run_id,
            issue.number,
            "session_finished" if state.status == "finished" else "session_blocked",
            session_id=state.session_id,
            status=state.status,
            acus=state.acus,
            pr_url=pr_url,
            outcome=outcome,
            detail=output.get("blocked_reason"),
        )
        self.store.update(
            issue.number,
            status=state.status,
            acus=state.acus,
            pr_url=pr_url,
            outcome=outcome,
            finished_at=utcnow(),
        )
        self._report(issue, state, pr_url=pr_url)
        return Outcome(
            issue.number, state.session_id, session_url or state.url, state.status, pr_url,
            state.acus,
        )

    # -- observable side effects ----------------------------------------

    def _announce(self, issue: Issue, session: SessionState) -> None:
        self.github.comment(
            issue.number,
            f"Devin session started: {session.url}\n\n"
            f"Run `{self.run_id}` · session `{session.session_id}` · "
            f"ACU cap {self.settings.max_acu_per_session}.",
        )
        self.github.set_labels(
            issue.number,
            add=[self.settings.label_in_progress],
            remove=[self.settings.trigger_label],
        )

    def _report(
        self,
        issue: Issue,
        state: SessionState,
        pr_url: str | None = None,
        timed_out: bool = False,
    ) -> None:
        output = state.structured_output or {}
        verdict = "timed out" if timed_out else output.get("status", state.status)
        lines = [
            f"**Devin session {verdict}** — {state.url}",
            "",
            f"- ACUs consumed: `{state.acus}`",
            f"- Pull request: {pr_url or '_none_'}",
        ]
        if output.get("summary"):
            lines += ["", "**Summary**", output["summary"]]
        if output.get("verification_output"):
            lines += [
                "",
                "<details><summary>Verification output</summary>",
                "",
                "```",
                str(output["verification_output"])[:5000],
                "```",
                "",
                "</details>",
            ]
        if output.get("blocked_reason"):
            lines += ["", f"**Blocked:** {output['blocked_reason']}"]
        self.github.comment(issue.number, "\n".join(lines))

        label = self.settings.label_pr_open if pr_url else self.settings.label_blocked
        self.github.set_labels(
            issue.number, add=[label], remove=[self.settings.label_in_progress]
        )
