"""Remediate GitHub issues with Devin sessions.

Every POLL_SECONDS the bot runs one stateless `tick`:

  devin:remediate    -> create a Devin session, comment its URL, label devin:in-progress
  devin:in-progress  -> poll the session from that comment; when it is done, comment
                        the result and label devin:pr-open | devin:blocked. A session
                        still working after SESSION_TIMEOUT_MINUTES is terminated and
                        retried, up to MAX_ATTEMPTS sessions per issue.

Labels and comments are the only state, so the process can be restarted at any time.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

from devin_api import Devin, DevinAPI, outcome_of, pr_url_of
from github_api import GitHub, GitHubAPI

LABEL_TRIGGER = "devin:remediate"
LABEL_IN_PROGRESS = "devin:in-progress"
LABEL_PR_OPEN = "devin:pr-open"
LABEL_BLOCKED = "devin:blocked"
STARTED_COMMENT = "Devin session started"
SESSION_TIMEOUT_MINUTES = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "25"))
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "3"))
MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT", "5"))


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


def started_sessions(comments: list[str]) -> list[str]:
    """Session URLs from the bot's 'Devin session started (attempt n): <url>' comments."""
    return [body.rsplit(" ", 1)[-1] for body in comments if body.startswith(STARTED_COMMENT)]


def session_id_of(url: str) -> str:
    return "devin-" + url.rstrip("/").rsplit("/", 1)[-1]


def start(issue: dict[str, Any], attempt: int, github: GitHubAPI, devin: DevinAPI) -> None:
    number = issue["number"]
    created = devin.create_session(
        prompt=build_prompt(github.repo, issue),
        title=f"Remediate #{number}: {issue['title'][:80]}",
    )
    github.comment(
        number, f"{STARTED_COMMENT} (attempt {attempt}/{MAX_ATTEMPTS}): {created['url']}"
    )
    print(f"#{number} attempt {attempt} session {created['url']}")


def finish(number: int, label: str, body: str, github: GitHubAPI) -> None:
    github.comment(number, body)
    github.relabel(number, LABEL_IN_PROGRESS, label)


def check(issue: dict[str, Any], github: GitHubAPI, devin: DevinAPI) -> None:
    number = issue["number"]
    urls = started_sessions(github.comments(number))
    if not urls:
        print(f"#{number} no session comment, requeueing")
        github.relabel(number, LABEL_IN_PROGRESS, LABEL_TRIGGER)
        return
    url, attempt = urls[-1], len(urls)
    session = devin.get_session(session_id_of(url))
    outcome = outcome_of(session)
    if outcome is not None:
        finish(
            number,
            LABEL_PR_OPEN if pr_url_of(session) else LABEL_BLOCKED,
            result_comment(session, outcome, url),
            github,
        )
        print(f"#{number} {outcome}  pr {pr_url_of(session) or 'none'}")
        return
    if time.time() - session["created_at"] < SESSION_TIMEOUT_MINUTES * 60:
        return
    devin.terminate_session(session_id_of(url))
    print(f"#{number} attempt {attempt} timed out after {SESSION_TIMEOUT_MINUTES} min")
    if attempt < MAX_ATTEMPTS:
        start(issue, attempt + 1, github, devin)
        return
    gave_up = (
        f"**Devin session timeout** — gave up after {MAX_ATTEMPTS} attempts of "
        f"{SESSION_TIMEOUT_MINUTES} min; last session {url}"
    )
    finish(number, LABEL_BLOCKED, gave_up, github)


def tick(github: GitHubAPI, devin: DevinAPI) -> None:
    """One pass over both labels; an error on one issue is logged and does not stop the rest."""
    in_progress = github.list_issues(LABEL_IN_PROGRESS)
    for issue in in_progress:
        try:
            check(issue, github, devin)
        except Exception as exc:  # noqa: BLE001
            print(f"#{issue['number']} check failed: {exc}", file=sys.stderr)
    free = MAX_CONCURRENT - len(in_progress)
    for issue in github.list_issues(LABEL_TRIGGER)[: max(free, 0)]:
        try:
            github.relabel(issue["number"], LABEL_TRIGGER, LABEL_IN_PROGRESS)
            start(issue, 1, github, devin)
        except Exception as exc:  # noqa: BLE001
            print(f"#{issue['number']} start failed: {exc}", file=sys.stderr)


def main() -> int:
    try:
        github = GitHub(os.environ["GITHUB_REPO"], os.environ["GITHUB_TOKEN"])
        devin = Devin(os.environ["DEVIN_ORG_ID"], os.environ["DEVIN_API_KEY"])
    except KeyError as exc:
        print(f"missing environment variable {exc}", file=sys.stderr)
        return 2
    poll_seconds = int(os.environ.get("POLL_SECONDS", "60"))
    print(
        f"watching {github.repo} for issues labelled {LABEL_TRIGGER} every {poll_seconds}s "
        f"({MAX_ATTEMPTS} x {SESSION_TIMEOUT_MINUTES} min per issue, {MAX_CONCURRENT} at once)"
    )
    while True:
        tick(github, devin)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
