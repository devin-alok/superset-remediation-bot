# superset-remediation-bot

Fixes a GitHub issue in [devin-alok/superset](https://github.com/devin-alok/superset)
by handing it to a Devin session.

```
remediate <issue>  ──▶  read issue  ──▶  POST /v1/sessions (Devin API)
                                              │
                                     Devin session (Devin's VM)
                                     clones the repo, fixes, tests, opens a PR
                                              │
                        poll until finished ──▶ comment session + PR URL on the issue
```

Everything in this repo is `remediate.py`; the engineering work happens inside the Devin
session.

## Run

```bash
cp .env.example .env          # fill in GITHUB_TOKEN and DEVIN_API_KEY
docker build -t remediation-bot .
docker run --rm --env-file .env remediation-bot 14
```

The container prints the session URL, waits for the session to finish (polling every
30 s, up to 90 min), prints the resulting PR URL, and posts both as comments on the issue.
Exit code is `0` when a PR was opened, `1` otherwise.

## Develop

```bash
pip install -e ".[dev]"
ruff check . && mypy remediate.py && pytest -q
```
