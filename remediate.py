"""Remediate one GitHub issue with a Devin session.

    remediate <issue-number>

Reads the issue, starts a Devin session that fixes it and opens a pull request,
waits for the session to finish, and comments the result back on the issue.
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
    def comment(self, number: int, body: str) -> None: ...


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

    def comment(self, number: int, body: str) -> None:
        response = self.http.post(
            f"{GITHUB_API}/repos/{self.repo}/issues/{number}/comments",
            json={"body": body},
            timeout=30,
        )
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


def remediate(
    issue_number: int,
    github: GitHubAPI,
    devin: DevinAPI,
    poll_seconds: float = 30,
    timeout_seconds: float = 5400,
) -> dict[str, Any]:
    issue = github.get_issue(issue_number)
    created = devin.create_session(
        prompt=build_prompt(github.repo, issue),
        title=f"Remediate #{issue_number}: {issue['title'][:80]}",
    )
    session_id, session_url = created["session_id"], created["url"]
    github.comment(issue_number, f"Devin session started: {session_url}")
    print(f"session {session_url}")

    deadline = time.monotonic() + timeout_seconds
    while True:
        session = devin.get_session(session_id)
        if session.get("status_enum") in TERMINAL_STATES:
            break
        if time.monotonic() > deadline:
            session["status_enum"] = "timeout"
            break
        time.sleep(poll_seconds)

    github.comment(issue_number, result_comment(session, session_url))
    print(f"status {session.get('status_enum')}  pr {pr_url_of(session) or 'none'}")
    return session


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not args[0].isdigit():
        print(__doc__, file=sys.stderr)
        return 2
    try:
        github = GitHub(os.environ["GITHUB_REPO"], os.environ["GITHUB_TOKEN"])
        devin = Devin(os.environ["DEVIN_API_KEY"])
    except KeyError as exc:
        print(f"missing environment variable {exc}", file=sys.stderr)
        return 2
    session = remediate(int(args[0]), github, devin)
    return 0 if pr_url_of(session) else 1


if __name__ == "__main__":
    sys.exit(main())
