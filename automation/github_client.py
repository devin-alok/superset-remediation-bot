"""Minimal GitHub REST client: issues, labels, comments, PR checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import requests

from .config import Settings

API = "https://api.github.com"


@dataclass
class Issue:
    number: int
    title: str
    body: str
    labels: list[str]
    url: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> Issue:
        return cls(
            number=data["number"],
            title=data.get("title", ""),
            body=data.get("body") or "",
            labels=[label["name"] for label in data.get("labels", [])],
            url=data.get("html_url", ""),
        )


class GitHubAPI(Protocol):
    def get_issue(self, number: int) -> Issue: ...
    def list_labelled(self, label: str) -> list[Issue]: ...
    def comment(self, number: int, body: str) -> None: ...
    def set_labels(self, number: int, add: list[str], remove: list[str]) -> None: ...


class GitHubClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.repo = settings.repo
        self.http = session or requests.Session()
        self.http.headers.update(
            {
                "Authorization": f"Bearer {settings.github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.http.request(method, f"{API}{path}", timeout=30, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    def get_issue(self, number: int) -> Issue:
        return Issue.from_api(self._request("GET", f"/repos/{self.repo}/issues/{number}"))

    def list_labelled(self, label: str) -> list[Issue]:
        data = self._request(
            "GET",
            f"/repos/{self.repo}/issues",
            params={"labels": label, "state": "open", "per_page": 100},
        )
        return [Issue.from_api(item) for item in data if "pull_request" not in item]

    def comment(self, number: int, body: str) -> None:
        self._request(
            "POST", f"/repos/{self.repo}/issues/{number}/comments", json={"body": body}
        )

    def set_labels(self, number: int, add: list[str], remove: list[str]) -> None:
        for label in remove:
            try:
                self._request("DELETE", f"/repos/{self.repo}/issues/{number}/labels/{label}")
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code != 404:
                    raise
        if add:
            self._request(
                "POST", f"/repos/{self.repo}/issues/{number}/labels", json={"labels": add}
            )

    def ensure_labels(self, labels: dict[str, str]) -> None:
        for name, color in labels.items():
            try:
                self._request(
                    "POST", f"/repos/{self.repo}/labels", json={"name": name, "color": color}
                )
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code != 422:
                    raise

    def pr_check_state(self, pr_url: str) -> str | None:
        """Combined CI state for a PR head, used for the 'PR passes CI' level."""
        number = pr_url.rstrip("/").split("/")[-1]
        pr = self._request("GET", f"/repos/{self.repo}/pulls/{number}")
        sha = pr["head"]["sha"]
        data = self._request("GET", f"/repos/{self.repo}/commits/{sha}/check-runs")
        runs = data.get("check_runs", [])
        if not runs:
            return None
        if any(run["status"] != "completed" for run in runs):
            return "pending"
        if all(run["conclusion"] in {"success", "neutral", "skipped"} for run in runs):
            return "success"
        return "failure"
