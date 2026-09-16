# superset-remediation-bot

Event-driven remediation of [Apache Superset](https://github.com/apache/superset) issues
using the [Devin API](https://docs.devin.ai/api-reference/overview).

Label an issue `devin:remediate` on the fork
([devin-alok/superset](https://github.com/devin-alok/superset)) → a Devin session
is created from the issue body → the session opens a pull request → the issue is
commented, relabelled, and the run is rolled up into a status report.

```
GitHub issue labelled  ──▶  webhook / Actions / cron sweep
                                      │
                             orchestrator (this repo)
                                      │  POST /v1/sessions
                                      ▼
                             Devin session (Devin's VM)
                              clones Superset, fixes, tests, opens PR
                                      │  poll · nudge · harvest
                                      ▼
                    issue comment + labels · PR · events.jsonl · report.md
```

Devin's VM does the engineering work. This repo is supervision: trigger handling,
idempotency, cost guardrails, and the observability that makes the loop reviewable.

## Run the whole workflow with no credentials

The simulation replays recorded fixtures through the **same orchestrator code
path** as production — only the transport is swapped — so it spends no ACUs and
needs no keys:

```bash
docker build -t remediation-bot .
docker run --rm -e DRY_RUN=1 remediation-bot simulate
```

It prints the full run report: three issues, two PRs, one blocked session, the
success ladder and the ACU cost. That is the fastest way to see what the system
does.

## Run it live

```bash
cp .env.example .env     # fill in DEVIN_API_KEY + GITHUB_TOKEN
docker build -t remediation-bot .

# one issue, end to end (the demo path — no tunnel needed)
docker run --rm --env-file .env -v "$PWD/runs:/app/runs" \
  remediation-bot run --issue 11

# every labelled issue that has no live session (the cron/reconciliation path)
docker run --rm --env-file .env -v "$PWD/runs:/app/runs" remediation-bot sweep

# regenerate the report from the event log
docker run --rm --env-file .env -v "$PWD/runs:/app/runs" remediation-bot report
```

### Live webhook mode

```bash
docker compose up          # uvicorn on :8000, same image
```

| Route | Purpose |
| --- | --- |
| `POST /webhook` | GitHub `issues` events, HMAC-verified (`X-Hub-Signature-256`) |
| `GET /status` | auto-refreshing status page |
| `GET /metrics.json` | the same metrics as JSON |
| `GET /healthz` | liveness |

Point a GitHub webhook (`Issues` events, content type `application/json`, secret =
`GITHUB_WEBHOOK_SECRET`) at `https://<host>/webhook`. The handler validates,
acknowledges in well under GitHub's 10s budget, and processes in the background.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `GITHUB_REPO` | `devin-alok/superset` | target repository |
| `GITHUB_TOKEN` | — | repo-scoped token: read issues, comment, label |
| `GITHUB_WEBHOOK_SECRET` | — | HMAC secret; unset disables verification (local only) |
| `DEVIN_API_KEY` | — | from https://app.devin.ai/settings/api-keys |
| `DEVIN_ORG_ID` | — | set only for org-scoped v3 keys; empty uses v1 |
| `TRIGGER_LABEL` | `devin:remediate` | the label that starts a session |
| `TRACKER_ISSUE` | `10` | issue the rolled-up report is posted to |
| `MAX_ACU_PER_SESSION` | `5` | per-session cost ceiling |
| `MAX_CONCURRENT_SESSIONS` | `3` | how many sessions may run at once |
| `MAX_NUDGES` | `2` | auto-replies before a session is left for a human |
| `DRY_RUN` | `0` | `1` replays fixtures, no network, no ACUs |

## Observability — "how would I know this is working?"

Every transition appends one immutable line to `runs/events.jsonl`
(`trigger_received`, `session_created`, `session_polled`, `nudge_sent`,
`pr_opened`, `session_finished`, `session_blocked`, `session_timeout`,
`session_error`). Everything else — the status table, the metrics, the tracker
comment — is *derived* from that log, so no two views can disagree.

**Success is a ladder, not a boolean**, because "the session finished" says
nothing about whether the issue is fixed:

| Level | Signal | Source |
| --- | --- | --- |
| 1 | session completed | Devin session status |
| 2 | pull request opened | `pull_request(s)` on the session |
| 3 | PR CI green | GitHub check-runs on the PR head |

Reported alongside: total ACUs, **ACUs per PR opened** (the real cost-per-fix),
median time to PR, and nudge counts.

**Liveness alerts** — not just success metrics — catch the failure mode where the
system silently stops: triggers received but no session created (dropped
webhook), sessions that timed out, sessions that finished without a PR, and API
errors. Any alert makes the CLI exit non-zero, so a broken automation shows up as
a red Actions run rather than as silence.

Artifacts per run: `runs/report-<run_id>.md` and `.json`, uploaded by the
workflows and posted as a comment on the tracker issue.

## Design notes

- **Idempotency.** The issue number is the primary key of the session table, and
  sessions are created with `idempotent: true`. A redelivered webhook attaches to
  the running session instead of starting a second one — proven by
  `tests/test_orchestrator.py`.
- **Recovery.** Sessions are tagged `issue-<n>`, so even if `runs/` is lost the
  mapping is recoverable from the list-sessions endpoint.
- **Nudging over restarting.** A session waiting on a question gets a message
  (context preserved) rather than a fresh session (context and ACUs thrown away).
- **Structured output.** The prompt requires `{status, pr_url, summary,
  verification_output, blocked_reason}`, so the issue comment quotes real
  verification output instead of parsed prose.
- **API versions.** v1 and v3 responses are normalised into one `SessionState`;
  the orchestrator never branches on version.

## Development

```bash
pip install -e ".[dev]"
ruff check automation tests && mypy automation && pytest -q
```
