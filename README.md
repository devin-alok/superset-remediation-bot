# superset-remediation-bot

Event-driven remediation of issues in [devin-alok/superset](https://github.com/devin-alok/superset):
label an issue `devin:remediate` and a Devin session fixes it and opens a PR.

```
issue labelled devin:remediate
        │  (container polls GitHub every 60 s)
        ▼
label → devin:in-progress, POST /v3/organizations/{org}/sessions, comment session URL
        │
   Devin session (Devin's VM): clones the repo, fixes, tests, opens a PR
        │  (container polls GET /v3/organizations/{org}/sessions/{id})
        ▼
comment result + PR URL, label → devin:pr-open | devin:blocked
```

The code is three small modules — `github_api.py` (issues, comments, labels), `devin_api.py`
(create/poll sessions) and `remediate.py` (prompt + one `tick` run every `POLL_SECONDS`);
the engineering work happens inside the Devin session. A tick is stateless: it starts a
session for every `devin:remediate` issue, then checks every `devin:in-progress` issue by
reading its "Devin session started" comment. Labels and comments are the whole state machine,
so there is no database and the container can be restarted at any time.

## Run

```bash
git clone https://github.com/devin-alok/superset-remediation-bot.git
cd superset-remediation-bot
cp .env.example .env          # fill in GITHUB_TOKEN, DEVIN_ORG_ID, DEVIN_API_KEY
docker build -t remediation-bot .
docker run --rm --env-file .env remediation-bot   # Ctrl-C to stop; run only one instance at a time
```

The log prints one line per event (`#26 attempt 1 session <url>`, `#26 finished  pr <url>`);
everything else is visible on the issue itself as comments and labels.

To remediate a single issue, label it `devin:remediate`. A session still working after
`SESSION_TIMEOUT_MINUTES` (default 25) is terminated and a fresh one started, up to
`MAX_ATTEMPTS` (default 3) per issue; after that the issue is labelled `devin:blocked`.
At most `MAX_CONCURRENT` (default 5) issues are in flight at once; the rest wait with their
`devin:remediate` label. `POLL_SECONDS` (default 60) is how often GitHub is checked. An API
error on one issue is logged and the tick moves on to the next issue.

`GITHUB_TOKEN` needs *Issues: read and write* on the target repo. `DEVIN_ORG_ID` and
`DEVIN_API_KEY` come from Devin *Settings → Service Users* (a service user with the
`UseDevinSessions` permission); the [v3 API](https://docs.devin.ai/api-reference/v3/sessions/post-organizations-sessions)
does not accept personal API keys.

## Develop

```bash
pip install -e ".[dev]"
ruff check . && mypy . && pytest -q
```
