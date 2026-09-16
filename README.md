# superset-remediation-bot

Event-driven remediation of issues in [devin-alok/superset](https://github.com/devin-alok/superset):
label an issue `devin:remediate` and a Devin session fixes it and opens a PR.

```
issue labelled devin:remediate
        │  (container polls GitHub every 60 s)
        ▼
label → devin:in-progress, POST /v1/sessions (Devin API), comment session URL
        │
   Devin session (Devin's VM): clones the repo, fixes, tests, opens a PR
        │  (container polls GET /v1/sessions/{id})
        ▼
comment result + PR URL, label → devin:pr-open | devin:blocked
```

Everything in this repo is `remediate.py`; the engineering work happens inside the Devin
session. Labels and comments double as the state machine, so there is no database: on restart the
container resumes every `devin:in-progress` issue from its "Devin session started" comment.

## Run

```bash
cp .env.example .env          # fill in GITHUB_TOKEN and DEVIN_API_KEY
docker build -t remediation-bot .
docker run --rm --env-file .env remediation-bot        # watch mode (the automation)
docker run --rm --env-file .env remediation-bot 14     # one issue, then exit
```

Sessions are given up to 90 min. In one-issue mode the exit code is `0` when a PR was
opened, `1` otherwise.

`GITHUB_TOKEN` needs *Issues: read and write* on the target repo.

## Develop

```bash
pip install -e ".[dev]"
ruff check . && mypy remediate.py && pytest -q
```
