"""Wire the orchestrator to either the live APIs or the in-memory doubles."""

from __future__ import annotations

from dataclasses import replace

from .config import Settings
from .config import settings as default_settings
from .devin_client import DevinClient
from .fakes import FakeDevin, FakeGitHub
from .github_client import GitHubClient
from .orchestrator import LABEL_COLORS, Orchestrator
from .state import Store


def build(settings: Settings | None = None, run_id: str | None = None) -> Orchestrator:
    settings = settings or default_settings
    settings.require_live_credentials()
    store = Store(settings.state_dir)

    if settings.dry_run:
        # No credentials, no network, no ACUs — same orchestrator code.
        settings = replace(settings, poll_interval_seconds=0)
        return Orchestrator(settings, FakeDevin(), FakeGitHub(), store, run_id)

    github = GitHubClient(settings)
    github.ensure_labels(LABEL_COLORS)
    return Orchestrator(settings, DevinClient(settings), github, store, run_id)
