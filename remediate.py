"""Remediate GitHub issues with Devin sessions.

Every POLL_SECONDS the bot runs one stateless `tick`:

  devin:remediate    -> create a Devin session, comment its URL, label devin:in-progress
  devin:in-progress  -> poll the session from that comment; when it is done, comment
                        the result and label devin:pr-open | devin:blocked

Labels and comments are the only state, so the process can be restarted at any time.
After each tick the pipeline metrics are logged and `report.html` is regenerated.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import report
from devin_api import Devin, DevinAPI, outcome_of, pr_url_of
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


def result_comment(session: dict[str, Any], outcome: str, session_url: str) -> str:
    output = session.get("structured_output") or {}
    lines = [
        f"**Devin session {output.get('status') or outcome}** — {session_url}",
        "",
        f"Pull request: {pr_url_of(session) or '_none_'}",
    ]
    if output.get("summary"):
        lines += ["", output["summary"]]
    return "\n".join(lines)


def session_url_of(comments: list[str]) -> str | None:
    """The URL from the latest 'session started' comment the bot left on an issue."""
    for body in reversed(comments):
        if body.startswith(STARTED_COMMENT):
            return body[len(STARTED_COMMENT) :].strip()
    return None


def start(issue: dict[str, Any], github: GitHubAPI, devin: DevinAPI) -> None:
    number = issue["number"]
    github.relabel(number, LABEL_TRIGGER, LABEL_IN_PROGRESS)
    created = devin.create_session(
        prompt=build_prompt(github.repo, issue),
        title=f"Remediate #{number}: {issue['title'][:80]}",
    )
    github.comment(number, f"{STARTED_COMMENT}{created['url']}")
    print(f"#{number} session {created['url']}")


def check(issue: dict[str, Any], github: GitHubAPI, devin: DevinAPI) -> None:
    number = issue["number"]
    url = session_url_of(github.comments(number))
    if url is None:
        print(f"#{number} no session comment, requeueing")
        github.relabel(number, LABEL_IN_PROGRESS, LABEL_TRIGGER)
        return
    session = devin.get_session("devin-" + url.rstrip("/").rsplit("/", 1)[-1])
    outcome = outcome_of(session)
    if outcome is None:
        return
    github.comment(number, result_comment(session, outcome, url))
    github.relabel(
        number, LABEL_IN_PROGRESS, LABEL_PR_OPEN if pr_url_of(session) else LABEL_BLOCKED
    )
    print(f"#{number} {outcome}  pr {pr_url_of(session) or 'none'}")


def tick(github: GitHubAPI, devin: DevinAPI) -> None:
    for issue in github.list_issues(LABEL_IN_PROGRESS):
        check(issue, github, devin)
    for issue in github.list_issues(LABEL_TRIGGER):
        start(issue, github, devin)
    path, data = report.write(github)
    print(report.metrics_line(data))
    print(f"report written to file://{path}")


def main() -> int:
    try:
        github = GitHub(os.environ["GITHUB_REPO"], os.environ["GITHUB_TOKEN"])
        devin = Devin(os.environ["DEVIN_ORG_ID"], os.environ["DEVIN_API_KEY"])
    except KeyError as exc:
        print(f"missing environment variable {exc}", file=sys.stderr)
        return 2
    poll_seconds = int(os.environ.get("POLL_SECONDS", "60"))
    print(f"watching {github.repo} for issues labelled {LABEL_TRIGGER} every {poll_seconds}s")
    while True:
        tick(github, devin)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
