"""CLI: run the engine on one project_id and persist findings."""
from __future__ import annotations

import argparse
import logging
import sys

from .context import load_project
from .findings import persist_findings, rank_findings
from .rules import run_all_rules


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FactTrack engine runner")
    parser.add_argument("--project", required=True, help="project id (e.g. demo_anderson_001)")
    parser.add_argument("--dry-run", action="store_true", help="don't persist findings")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    ctx = load_project(args.project)
    findings = run_all_rules(ctx)
    findings = rank_findings(findings)
    print(f"\n=== {len(findings)} findings on project {args.project} ===")
    from facttrack.render.pdf import _effort_display
    for f in findings:
        effort = _effort_display(f.rule_id)
        print(f"  [{f.severity.upper():<8}] {f.rule_id:<35} confidence={f.confidence_score:.2f}  curative: {effort}")
        print(f"           {f.title}")

    if not args.dry_run:
        persist_findings(args.project, findings)
        print(f"\npersisted {len(findings)} findings.")
    else:
        print("\n(dry-run; nothing persisted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
