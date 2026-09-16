"""`remediate` — the command the workflows, Docker image and demo all use."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace

from . import report
from .config import settings as default_settings
from .factory import build
from .metrics import as_dict, summarise
from .state import Store

log = logging.getLogger("remediate")


def _finalise(orchestrator, publish: bool) -> int:
    ci_states = {}
    if not orchestrator.settings.dry_run:
        for record in orchestrator.store.all():
            if record.pr_url:
                try:
                    state = orchestrator.github.pr_check_state(record.pr_url)
                except Exception as exc:  # network/permission issues must not
                    log.warning("CI state lookup failed for #%s: %s", record.issue, exc)
                    continue  # lose the rest of the report
                if state:
                    ci_states[record.issue] = state

    summary = summarise(orchestrator.store, ci_states)
    md_path, json_path = report.write(
        summary, orchestrator.store, orchestrator.settings.repo, orchestrator.run_id
    )
    print(md_path.read_text(encoding="utf-8"))
    print(f"\nreport: {md_path}  {json_path}")

    if publish:
        report.publish(
            orchestrator.github,
            orchestrator.settings.tracker_issue,
            summary,
            orchestrator.settings.repo,
        )
    return 1 if summary.alerts else 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="remediate")
    parser.add_argument("--no-publish", action="store_true", help="skip tracker comment")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="remediate a single issue")
    run.add_argument("--issue", type=int, required=True)
    run.add_argument("--no-wait", action="store_true", help="create the session and exit")

    sub.add_parser("sweep", help="remediate every labelled issue with no live session")
    sub.add_parser("simulate", help="replay fixtures end to end, no credentials needed")
    sub.add_parser("report", help="regenerate the report from the event log")
    sub.add_parser("status", help="print the issue -> session table as JSON")

    args = parser.parse_args(argv)

    if args.command == "report":
        store = Store(default_settings.state_dir)
        summary = summarise(store)
        print(report.render_markdown(summary, default_settings.repo))
        return 0

    if args.command == "status":
        print(json.dumps(as_dict(summarise(Store(default_settings.state_dir))), indent=2))
        return 0

    if args.command == "simulate":
        settings = replace(
            default_settings,
            dry_run=True,
            state_dir=default_settings.state_dir / "simulation",
        )
        orchestrator = build(settings)
        for outcome in orchestrator.sweep():
            log.info(
                "issue #%s -> %s (%s ACUs) %s",
                outcome.issue,
                outcome.status,
                outcome.acus,
                outcome.pr_url or "no PR",
            )
        return _finalise(orchestrator, publish=False)

    orchestrator = build()
    publish = not args.no_publish

    if args.command == "run":
        outcome = orchestrator.remediate(args.issue, wait=not args.no_wait)
        log.info("issue #%s -> %s %s", outcome.issue, outcome.status, outcome.pr_url or "")
    else:
        orchestrator.sweep()

    return _finalise(orchestrator, publish=publish)


if __name__ == "__main__":
    sys.exit(main())
