"""Settings, loaded from the environment once at import time."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Everything the orchestrator needs to know about the outside world."""

    repo: str = os.environ.get("GITHUB_REPO", "devin-alok/superset")
    github_token: str = os.environ.get("GITHUB_TOKEN", os.environ.get("GITHUB_PAT", ""))
    webhook_secret: str = os.environ.get("GITHUB_WEBHOOK_SECRET", "")

    devin_api_key: str = os.environ.get("DEVIN_API_KEY", "")
    devin_org_id: str = os.environ.get("DEVIN_ORG_ID", "")
    devin_base_url: str = os.environ.get("DEVIN_BASE_URL", "https://api.devin.ai")

    # The label whose application is the trigger, and the states the bot drives.
    trigger_label: str = os.environ.get("TRIGGER_LABEL", "devin:remediate")
    label_in_progress: str = "devin:in-progress"
    label_pr_open: str = "devin:pr-open"
    label_blocked: str = "devin:blocked"

    tracker_issue: int = int(os.environ.get("TRACKER_ISSUE", "10"))

    # Guardrails. A runaway session on a dependency upgrade is the expensive
    # failure mode, so both a per-session and a per-run ceiling exist.
    max_acu_per_session: float = float(os.environ.get("MAX_ACU_PER_SESSION", "5"))
    max_concurrent_sessions: int = int(os.environ.get("MAX_CONCURRENT_SESSIONS", "3"))
    max_nudges: int = int(os.environ.get("MAX_NUDGES", "2"))
    poll_interval_seconds: int = int(os.environ.get("POLL_INTERVAL_SECONDS", "30"))
    session_timeout_seconds: int = int(os.environ.get("SESSION_TIMEOUT_SECONDS", "5400"))

    dry_run: bool = field(default_factory=lambda: _flag("DRY_RUN"))
    state_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("STATE_DIR", "runs"))
    )

    @property
    def uses_v3(self) -> bool:
        """Org-scoped v3 needs an org id; legacy ``apk_`` keys do not have one."""
        return bool(self.devin_org_id)

    def require_live_credentials(self) -> None:
        if self.dry_run:
            return
        missing = [
            name
            for name, value in (
                ("DEVIN_API_KEY", self.devin_api_key),
                ("GITHUB_TOKEN", self.github_token),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(
                f"missing {', '.join(missing)}; set them or run with DRY_RUN=1"
            )


settings = Settings()
