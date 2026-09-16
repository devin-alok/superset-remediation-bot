"""Build a self-contained HTML report of the remediation pipeline from GitHub labels.

    python report.py            # writes report.html (or $REPORT_PATH)

There is no database: every `devin:*` label change GitHub recorded on an issue is an
event with a timestamp, so counts and time-to-PR are derived from the issue timelines.
"""

from __future__ import annotations

import html
import json
import os
import statistics
import sys
from datetime import UTC, datetime
from typing import Any

from github_api import GitHub, GitHubAPI

QUEUED, IN_PROGRESS, PR_OPEN, BLOCKED = (
    "devin:remediate",
    "devin:in-progress",
    "devin:pr-open",
    "devin:blocked",
)
STATES = {QUEUED: "queued", IN_PROGRESS: "in progress", PR_OPEN: "pr open", BLOCKED: "blocked"}


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def issue_row(
    issue: dict[str, Any], events: list[dict[str, Any]], now: datetime
) -> dict[str, Any]:
    """Current state and time-to-PR (or elapsed) for one issue, derived from label events."""
    labels = {label["name"] for label in issue.get("labels", [])}
    state = next(
        (STATES[name] for name in (PR_OPEN, BLOCKED, IN_PROGRESS, QUEUED) if name in labels),
        "-",
    )
    started = next(
        (
            _when(e["created_at"])
            for e in events
            if e["event"] == "labeled" and e["label"]["name"] == IN_PROGRESS
        ),
        None,
    )
    ended = next(
        (
            _when(e["created_at"])
            for e in events
            if e["event"] == "labeled"
            and e["label"]["name"] in {PR_OPEN, BLOCKED}
            and started
            and _when(e["created_at"]) >= started
        ),
        None,
    )
    minutes = None
    if started is not None:
        minutes = round(((ended or now) - started).total_seconds() / 60, 1)
    return {
        "number": issue["number"],
        "title": issue["title"],
        "url": issue["html_url"],
        "state": state,
        "minutes": minutes,
        "done": ended is not None,
    }


def collect(github: GitHubAPI, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    seen: dict[int, dict[str, Any]] = {}
    for label in STATES:
        for issue in github.list_issues(label, state="all"):
            seen.setdefault(issue["number"], issue)
    rows = [
        issue_row(issue, github.events(number), now) for number, issue in sorted(seen.items())
    ]
    counts = {name: sum(row["state"] == name for row in rows) for name in STATES.values()}
    solved = [row["minutes"] for row in rows if row["done"] and row["state"] == "pr open"]
    finished = counts["pr open"] + counts["blocked"]
    return {
        "repo": github.repo,
        "generated_at": now.strftime("%Y-%m-%d %H:%M UTC"),
        "counts": counts,
        "success_rate": round(100 * counts["pr open"] / finished) if finished else None,
        "timing": {
            "median": statistics.median(solved) if solved else None,
            "min": min(solved) if solved else None,
            "max": max(solved) if solved else None,
        },
        "rows": rows,
    }


def metrics_line(data: dict[str, Any]) -> str:
    return json.dumps(
        {"event": "tick", **data["counts"], "median_minutes": data["timing"]["median"]}
    )


CSS = """
body{font:15px system-ui,sans-serif;margin:2rem auto;max-width:960px;color:#222}
h1{margin:0}.muted{color:#777}.tiles{display:flex;gap:1rem;margin:1.5rem 0;flex-wrap:wrap}
.tile{flex:1;min-width:130px;padding:1rem;border-radius:8px;background:#f4f6f8;text-align:center}
.tile b{display:block;font-size:2rem}.queued b{color:#666}.progress b{color:#d98a00}
.ok b{color:#1a7f37}.bad b{color:#c62828}table{width:100%;border-collapse:collapse}
td,th{padding:.5rem;border-bottom:1px solid #e5e5e5;text-align:left}
td:last-child{width:180px}.bar{height:8px;background:#1a7f37;border-radius:4px;display:inline-block}
.bar.bad{background:#c62828}.bar.progress{background:#d98a00}
"""


def render(data: dict[str, Any]) -> str:
    c, t = data["counts"], data["timing"]
    rate = "-" if data["success_rate"] is None else f"{data['success_rate']}%"
    longest = max((row["minutes"] or 0 for row in data["rows"]), default=0) or 1

    def tile(cls: str, value: Any, label: str) -> str:
        return f'<div class="tile {cls}"><b>{value}</b>{label}</div>'

    def minutes(value: float | None) -> str:
        return "-" if value is None else f"{value:g} min"

    rows = []
    for row in data["rows"]:
        cls = {"pr open": "ok", "blocked": "bad", "in progress": "progress"}.get(
            row["state"], ""
        )
        width = int(100 * (row["minutes"] or 0) / longest)
        running = " (running)" if row["minutes"] and not row["done"] else ""
        rows.append(
            f'<tr><td><a href="{row["url"]}">#{row["number"]}</a></td>'
            f"<td>{html.escape(row['title'])}</td><td>{row['state']}</td>"
            f"<td>{minutes(row['minutes'])}{running}</td>"
            f'<td><span class="bar {cls}" style="width:{width}%"></span></td></tr>'
        )
    return f"""<!doctype html><meta charset="utf-8"><title>Remediation report</title>
<style>{CSS}</style>
<h1>Remediation report — {html.escape(data["repo"])}</h1>
<p class="muted">generated {data["generated_at"]}</p>
<div class="tiles">
{tile("queued", c["queued"], "queued")}{tile("progress", c["in progress"], "in progress")}
{tile("ok", c["pr open"], "successful (PR open)")}
{tile("bad", c["blocked"], "failed / blocked")}
{tile("", rate, "success rate")}
</div>
<div class="tiles">
{tile("", minutes(t["median"]), "median time to PR")}{tile("", minutes(t["min"]), "fastest")}
{tile("", minutes(t["max"]), "slowest")}
</div>
<table><tr><th>Issue</th><th>Title</th><th>State</th><th>Time to PR</th><th></th></tr>
{"".join(rows)}
</table>
"""


def write(github: GitHubAPI, path: str | None = None) -> tuple[str, dict[str, Any]]:
    path = path or os.environ.get("REPORT_PATH", "report.html")
    data = collect(github)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render(data))
    return os.path.abspath(path), data


def url(path: str) -> str:
    """file:// URL of the report; HOST_REPORT_DIR maps a container path to the host."""
    host_dir = os.environ.get("HOST_REPORT_DIR")
    if host_dir:
        path = os.path.join(host_dir, os.path.basename(path))
    return f"file://{path}"


def main() -> int:
    github = GitHub(os.environ["GITHUB_REPO"], os.environ["GITHUB_TOKEN"])
    path, data = write(github)
    print(metrics_line(data))
    print(f"report written to {url(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
