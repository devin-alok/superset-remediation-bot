"""Render the run report (markdown + JSON) and publish it to the tracker issue."""

from __future__ import annotations

import json
from pathlib import Path

from .github_client import GitHubAPI
from .metrics import Summary, as_dict
from .state import Store, utcnow

MARKER = "<!-- superset-remediation-bot:status -->"


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m{secs:02d}s"


def render_markdown(summary: Summary, repo: str) -> str:
    acus_per_pr = summary.acus_per_pr if summary.acus_per_pr is not None else "—"
    time_to_pr = (
        f"{summary.median_minutes_to_pr} min"
        if summary.median_minutes_to_pr is not None
        else "—"
    )
    rows = [
        "| Issue | State | PR | CI | ACUs | Nudges | Duration | Session |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in summary.issues:
        pr = f"[PR]({item.pr_url})" if item.pr_url else "—"
        session = (
            f"[{item.session_id[:16]}](https://app.devin.ai/sessions/"
            f"{item.session_id.removeprefix('devin-')})"
            if item.session_id
            else "—"
        )
        rows.append(
            f"| [#{item.issue}](https://github.com/{repo}/issues/{item.issue}) "
            f"| `{item.state}` | {pr} | {item.ci or '—'} | {item.acus} | {item.nudges} "
            f"| {_duration(item.duration_seconds)} | {session} |"
        )

    alerts = (
        "\n".join(f"- {alert}" for alert in summary.alerts)
        if summary.alerts
        else "- none"
    )

    return f"""{MARKER}
## Remediation status — {utcnow()}

{chr(10).join(rows)}

### Effectiveness
Success is reported as a ladder, because "the session finished" is not the same
as "the issue is fixed".

| Level | Meaning | Count |
| --- | --- | --- |
| 0 | triggers received | {summary.triggers} |
| 0 | duplicate triggers suppressed | {summary.duplicates_suppressed} |
| 1 | session completed | {summary.level1_completed} / {summary.sessions_created} |
| 2 | pull request opened | {summary.level2_pr_opened} / {summary.sessions_created} |
| 3 | pull request CI green | {summary.level3_ci_green} / {summary.level2_pr_opened} |
| — | blocked / timed out | {summary.blocked} |

- Total ACUs: **{summary.total_acus}**
- ACUs per PR opened: **{acus_per_pr}**
- Median time to PR: **{time_to_pr}**

### Alerts
{alerts}
"""


def write(summary: Summary, store: Store, repo: str, run_id: str) -> tuple[Path, Path]:
    markdown = render_markdown(summary, repo)
    md_path = store.dir / f"report-{run_id}.md"
    json_path = store.dir / f"report-{run_id}.json"
    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(as_dict(summary), indent=2), encoding="utf-8")
    (store.dir / "report-latest.md").write_text(markdown, encoding="utf-8")
    return md_path, json_path


def publish(github: GitHubAPI, tracker_issue: int, summary: Summary, repo: str) -> None:
    """Post the rolled-up table on the tracker issue — no extra infra to host."""
    github.comment(tracker_issue, render_markdown(summary, repo))
