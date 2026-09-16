"""GitHub REST calls the bot needs: issues, comments and labels."""

from __future__ import annotations

from typing import Any, Protocol

import requests

GITHUB_API = "https://api.github.com"


class GitHubAPI(Protocol):
    repo: str

    def get_issue(self, number: int) -> dict[str, Any]: ...
    def list_issues(self, label: str) -> list[dict[str, Any]]: ...
    def comments(self, number: int) -> list[str]: ...
    def comment(self, number: int, body: str) -> None: ...
    def relabel(self, number: int, old: str, new: str) -> None: ...


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
