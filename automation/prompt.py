"""Turn a GitHub issue into a Devin session prompt.

The issue bodies already contain "Intended remediation" and "Verification"
sections, so the prompt mostly frames them with the repository's own rules
(AGENTS.md) and the PR contract the orchestrator relies on.
"""

from __future__ import annotations

from typing import Any

from .github_client import Issue

REPO_RULES = """\
Repository rules (from AGENTS.md / .cursor/rules, non-negotiable):
- Keep the change minimal and scoped to this issue. No drive-by refactors.
- Python: add type hints; the code must pass `pre-commit run --files <changed>`
  (ruff, black, mypy).
- Frontend: TypeScript only, no `any`, use `@superset-ui/core` abstractions.
- New source files need the Apache Software Foundation licence header.
- Never hand-edit generated files (lockfiles, `requirements/*.txt` are compiled
  from `requirements/*.in` via `pip-compile`).
- Run the targeted tests you can run; do not attempt a full CI run locally.
"""

STRUCTURED_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["fixed", "partial", "blocked"]},
        "pr_url": {"type": ["string", "null"]},
        "summary": {"type": "string"},
        "verification_output": {"type": "string"},
        "blocked_reason": {"type": ["string", "null"]},
    },
    "required": ["status", "summary"],
}


def build_prompt(issue: Issue, repo: str, base_branch: str = "master") -> str:
    return f"""\
You are remediating a single tracked issue in the repository `{repo}`.

## Issue #{issue.number}: {issue.title}
{issue.url}

{issue.body}

## Your task
1. Clone `{repo}` and branch off `{base_branch}` as
   `devin/issue-{issue.number}-<short-slug>`.
2. Implement the fix described in the "Intended remediation" section above.
   If that section is wrong or incomplete after you read the code, implement
   the correct minimal fix and say so in the PR description.
3. Run the verification steps in the issue, plus `pre-commit run --files` on
   every file you changed. Paste the real command output — do not summarise it.
4. Open a pull request against `{base_branch}` of `{repo}` whose description
   explains root cause, fix, and verification, and whose body contains the
   line `Closes #{issue.number}` so merging closes the issue.
5. If you cannot complete the fix, still report: stop, do not open a
   speculative PR, and explain precisely what blocked you.

{REPO_RULES}

## Reporting
Call `provide_structured_output` with:
  status: "fixed" | "partial" | "blocked"
  pr_url: the PR URL, or null
  summary: 2-3 sentences on root cause and fix
  verification_output: the commands you ran and their real output
  blocked_reason: null unless status is "blocked"
"""


def session_title(issue: Issue) -> str:
    return f"Remediate #{issue.number}: {issue.title[:80]}"


def session_tags(issue: Issue) -> list[str]:
    # `issue-<n>` is the join key across GitHub, Devin and the event log, and
    # makes the mapping recoverable via the list-sessions endpoint.
    return ["superset-remediation", f"issue-{issue.number}"]
