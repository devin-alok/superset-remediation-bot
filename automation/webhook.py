"""FastAPI receiver: the live trigger path.

GitHub gives a handler ~10s, and a remediation takes minutes, so the endpoint
validates, acknowledges, and hands the issue number to a background task.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import report
from .config import settings
from .factory import build
from .metrics import as_dict, summarise
from .state import Store

log = logging.getLogger("webhook")
app = FastAPI(title="superset-remediation-bot")


def verify(body: bytes, signature: str | None) -> None:
    if not settings.webhook_secret:
        return  # unauthenticated only when explicitly unconfigured (local demo)
    if not signature:
        raise HTTPException(status_code=401, detail="missing signature")
    expected = "sha256=" + hmac.new(
        settings.webhook_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="bad signature")


def remediate(number: int) -> None:
    orchestrator = build()
    try:
        orchestrator.remediate(number)
    except Exception as exc:  # a failed session must still be visible
        orchestrator.store.emit(
            orchestrator.run_id, number, "session_error", detail=str(exc)
        )
        log.exception("remediation of #%s failed", number)
        return
    summary = summarise(orchestrator.store)
    report.write(summary, orchestrator.store, settings.repo, orchestrator.run_id)
    report.publish(orchestrator.github, settings.tracker_issue, summary, settings.repo)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "repo": settings.repo, "dry_run": settings.dry_run}


@app.post("/webhook")
async def webhook(
    request: Request,
    background: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, Any]:
    body = await request.body()
    verify(body, x_hub_signature_256)
    payload = await request.json()

    action = payload.get("action")
    label = (payload.get("label") or {}).get("name")
    issue = payload.get("issue") or {}

    labelled = action == "labeled" and label == settings.trigger_label
    opened_labelled = action == "opened" and settings.trigger_label in [
        item["name"] for item in issue.get("labels", [])
    ]
    if not (labelled or opened_labelled):
        return {"ignored": True, "action": action, "label": label}

    number = int(issue["number"])
    background.add_task(remediate, number)
    return {"accepted": number}


@app.get("/status", response_class=HTMLResponse)
def status() -> str:
    summary = summarise(Store(settings.state_dir))
    return (
        "<html><head><title>remediation status</title>"
        "<meta http-equiv='refresh' content='15'>"
        "<style>body{font-family:ui-monospace,monospace;margin:2rem}"
        "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:.3rem .6rem}"
        "</style></head><body>"
        + report.render_markdown(summary, settings.repo).replace("\n", "<br>")
        + "</body></html>"
    )


@app.get("/metrics.json")
def metrics_json() -> dict[str, Any]:
    return as_dict(summarise(Store(settings.state_dir)))
