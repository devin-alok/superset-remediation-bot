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
from typing import Any, Protocol

import requests

GITHUB_API = "https://api.github.com"
DEVIN_API = "https://api.devin.ai/v1"
TERMINAL_STATES = {"finished", "blocked", "expired"}
LABEL_TRIGGER = "devin:remediate"
LABEL_IN_PROGRESS = "devin:in-progress"
LABEL_PR_OPEN = "devin:pr-open"
LABEL_BLOCKED = "devin:blocked"
STARTED_COMMENT = "Devin session started: "

STRUCTURED_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["fixed", "blocked"]},
        "pr_url": {"type": ["string", "null"]},
        "summary": {"type": "string"},
    },
    "required": ["status", "summary"],
}


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


class GitHubAPI(Protocol):
    repo: str

    def get_issue(self, number: int) -> dict[str, Any]: ...
    def list_issues(self, label: str) -> list[dict[str, Any]]: ...
    def comments(self, number: int) -> list[str]: ...
    def comment(self, number: int, body: str) -> None: ...
    def relabel(self, number: int, old: str, new: str) -> None: ...


class DevinAPI(Protocol):
    def create_session(self, prompt: str, title: str) -> dict[str, Any]: ...
    def get_session(self, session_id: str) -> dict[str, Any]: ...


class GitHub:
    def __init__(self, repo: str, token: str) -> None:
        self.repo = repo
        self.http = requests.Session()
        self.http.headers["Authorization"] = f"Bearer {token}"
        self.http.headers["Accept"] = "application/vnd.github+json"

    def get_issue(self, number: int) -> dict[str, Any]:
        response = self.http.get(f"{GITHUB_API}/repos/{self.repo}/issues/{number}", timeout=30)
        response.raise_for_status()
        return dict(response.json())

    def list_issues(self, label: str) -> list[dict[str, Any]]:
        response = self.http.get(
            f"{GITHUB_API}/repos/{self.repo}/issues",
            params={"labels": label, "state": "open", "per_page": "100"},
            timeout=30,
        )
        response.raise_for_status()
        return [issue for issue in response.json() if "pull_request" not in issue]

    def comments(self, number: int) -> list[str]:
        response = self.http.get(
            f"{GITHUB_API}/repos/{self.repo}/issues/{number}/comments",
            params={"per_page": "100"},
            timeout=30,
        )
        response.raise_for_status()
        return [str(comment["body"]) for comment in response.json()]

    def comment(self, number: int, body: str) -> None:
        response = self.http.post(
            f"{GITHUB_API}/repos/{self.repo}/issues/{number}/comments",
            json={"body": body},
            timeout=30,
        )
        response.raise_for_status()

    def relabel(self, number: int, old: str, new: str) -> None:
        issue_url = f"{GITHUB_API}/repos/{self.repo}/issues/{number}"
        response = self.http.delete(f"{issue_url}/labels/{old}", timeout=30)
        if response.status_code != 404:
            response.raise_for_status()
        response = self.http.post(f"{issue_url}/labels", json={"labels": [new]}, timeout=30)
        response.raise_for_status()


class Devin:
    def __init__(self, api_key: str) -> None:
        self.http = requests.Session()
        self.http.headers["Authorization"] = f"Bearer {api_key}"

    def create_session(self, prompt: str, title: str) -> dict[str, Any]:
        response = self.http.post(
            f"{DEVIN_API}/sessions",
            json={
                "prompt": prompt,
                "title": title,
                "structured_output_schema": STRUCTURED_OUTPUT_SCHEMA,
            },
            timeout=30,
        )
        response.raise_for_status()
        return dict(response.json())

    def get_session(self, session_id: str) -> dict[str, Any]:
        response = self.http.get(f"{DEVIN_API}/sessions/{session_id}", timeout=30)
        response.raise_for_status()
        return dict(response.json())


def pr_url_of(session: dict[str, Any]) -> str | None:
    pr = session.get("pull_request")
    if isinstance(pr, dict) and pr.get("url"):
        return str(pr["url"])
    output = session.get("structured_output") or {}
    return output.get("pr_url")


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
