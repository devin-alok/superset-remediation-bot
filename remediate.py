"""Remediate GitHub issues with Devin sessions.

remediate                 watch the repo: every issue labelled `devin:remediate`
                          gets a Devin session (label -> devin:in-progress ->
                          devin:pr-open | devin:blocked, result commented)
remediate <issue-number>  remediate one issue and exit
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

from devin_api import TERMINAL_STATES, Devin, DevinAPI, pr_url_of
from github_api import GitHub, GitHubAPI

LABEL_TRIGGER = "devin:remediate"
LABEL_IN_PROGRESS = "devin:in-progress"
LABEL_PR_OPEN = "devin:pr-open"
LABEL_BLOCKED = "devin:blocked"
STARTED_COMMENT = "Devin session started: "


def build_prompt(repo: str, issue: dict[str, Any]) -> str:
    return f"""\
Fix the following issue in the GitHub repository `{repo}`.

## Issue #{issue["number"]}: {issue["title"]}
{issue["html_url"]}

{issue.get("body") or ""}

## Instructions
1. Clone `{repo}`, branch off `master` as `devin/issue-{issue["number"]}-<short-slug>`.
2. Implement the minimal fix described in the issue.
3. Run the verification steps from the issue and `pre-commit run --files` on every
   file you changed.
4. Open a pull request against `master` whose body contains `Closes #{issue["number"]}`.
5. If you cannot complete the fix, do not open a PR; report what blocked you.

Report the result via structured output: status ("fixed" or "blocked"),
pr_url (or null) and a 2-3 sentence summary.
"""


def result_comment(session: dict[str, Any], session_url: str) -> str:
    output = session.get("structured_output") or {}
    pr_url = pr_url_of(session)
    status = output.get("status") or session.get("status_enum")
    lines = [
        f"**Devin session {status}** — {session_url}",
        "",
        f"Pull request: {pr_url or '_none_'}",
    ]
    if output.get("summary"):
        lines += ["", output["summary"]]
    return "\n".join(lines)


class Run:
    """One Devin session working on one issue."""

    def __init__(
        self, number: int, session_url: str, github: GitHubAPI, devin: DevinAPI
    ) -> None:
        self.number, self.session_url = number, session_url
        self.session_id = "devin-" + session_url.rstrip("/").rsplit("/", 1)[-1]
        self.github, self.devin = github, devin
        self.started = time.monotonic()

    @classmethod
    def start(cls, issue: dict[str, Any], github: GitHubAPI, devin: DevinAPI) -> Run:
        created = devin.create_session(
            prompt=build_prompt(github.repo, issue),
            title=f"Remediate #{issue['number']}: {issue['title'][:80]}",
        )
        github.comment(issue["number"], f"{STARTED_COMMENT}{created['url']}")
        print(f"#{issue['number']} session {created['url']}")
        return cls(issue["number"], created["url"], github, devin)

    @classmethod
    def resume(cls, issue: dict[str, Any], github: GitHubAPI, devin: DevinAPI) -> Run | None:
        """Rebuild a run from the 'session started' comment left by a previous process."""
        for body in reversed(github.comments(issue["number"])):
            if body.startswith(STARTED_COMMENT):
                url = body[len(STARTED_COMMENT) :].strip()
                print(f"#{issue['number']} resuming {url}")
                return cls(issue["number"], url, github, devin)
        return None

    def poll(self, timeout_seconds: float) -> dict[str, Any] | None:
        """Return the session once it is terminal (or timed out), else None."""
        session = self.devin.get_session(self.session_id)
        if session.get("status_enum") in TERMINAL_STATES:
            return session
        if time.monotonic() - self.started > timeout_seconds:
            session["status_enum"] = "timeout"
            return session
        return None

    def finish(self, session: dict[str, Any]) -> None:
        self.github.comment(self.number, result_comment(session, self.session_url))
        print(f"#{self.number} {session.get('status_enum')}  pr {pr_url_of(session) or 'none'}")


def remediate(
    issue_number: int,
    github: GitHubAPI,
    devin: DevinAPI,
    poll_seconds: float = 30,
    timeout_seconds: float = 5400,
) -> dict[str, Any]:
    run = Run.start(github.get_issue(issue_number), github, devin)
    while (session := run.poll(timeout_seconds)) is None:
        time.sleep(poll_seconds)
    run.finish(session)
    return session


def watch(
    github: GitHubAPI,
    devin: DevinAPI,
    poll_seconds: float = 60,
    timeout_seconds: float = 5400,
    once: bool = False,
) -> None:
    """Start a session for every `devin:remediate` issue; label + comment the outcome.

    Issues left `devin:in-progress` by a previous process are resumed from their
    'session started' comment. With ``once`` the loop returns as soon as no session is
    in flight.
    """
    runs: dict[int, Run] = {}
    for issue in github.list_issues(LABEL_IN_PROGRESS):
        run = Run.resume(issue, github, devin)
        if run is None:
            github.relabel(issue["number"], LABEL_IN_PROGRESS, LABEL_TRIGGER)
        else:
            runs[issue["number"]] = run
    while True:
        for issue in github.list_issues(LABEL_TRIGGER):
            if issue["number"] in runs:
                continue
            github.relabel(issue["number"], LABEL_TRIGGER, LABEL_IN_PROGRESS)
            runs[issue["number"]] = Run.start(issue, github, devin)
        for number, run in list(runs.items()):
            session = run.poll(timeout_seconds)
            if session is None:
                continue
            run.finish(session)
            outcome = LABEL_PR_OPEN if pr_url_of(session) else LABEL_BLOCKED
            github.relabel(number, LABEL_IN_PROGRESS, outcome)
            del runs[number]
        if once and not runs:
            return
        time.sleep(poll_seconds)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) > 1 or (args and not args[0].isdigit()):
        print(__doc__, file=sys.stderr)
        return 2
    try:
        github = GitHub(os.environ["GITHUB_REPO"], os.environ["GITHUB_TOKEN"])
        devin = Devin(os.environ["DEVIN_API_KEY"])
    except KeyError as exc:
        print(f"missing environment variable {exc}", file=sys.stderr)
        return 2
    if args:
        session = remediate(int(args[0]), github, devin)
        return 0 if pr_url_of(session) else 1
    print(f"watching {github.repo} for issues labelled {LABEL_TRIGGER}")
    watch(github, devin)
    return 0


if __name__ == "__main__":
    sys.exit(main())
