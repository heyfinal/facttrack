"""Magazine-grade PDF rendering via Typst.

Replaces the WeasyPrint path for premium PDF output. Generates a single JSON
payload describing the report, then invokes the `typst compile` CLI against
`templates/report.typ`. Typst handles all typography + tables natively — the
result is publication-quality (EB Garamond display, IBM Plex Sans body) at
about 1/5 the file size of the prior WeasyPrint PDF.

The WeasyPrint HTML+PDF path (`facttrack.render.pdf`) is preserved for
back-compat and developer iteration; this module is the deliverable artifact.
"""
from __future__ import annotations

import decimal
import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from facttrack import __version__
from facttrack.config import COUNTIES, PATHS, ensure_dirs
from facttrack.db import cursor
from facttrack.engine.context import ProjectContext, load_project
from facttrack.render.pdf import (
    _badge_for_findings,
    _chain_entries_for_tract,
    _clause_coverage_breakdown,
    _clean_tract_label,
    _count_registered_rules,
    _effort_display,
    _examination_period,
    _has_clause_data,
    _lease_calendar,
    _resolve_actual_data_sources,
    _rrc_pulled_at,
    _unattributed_leases,
)

log = logging.getLogger(__name__)

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "report.typ"


class _JSONFallback(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, decimal.Decimal):
            return float(o)
        if isinstance(o, (date, datetime)):
            return o.isoformat()
        return super().default(o)


def _typst_binary() -> str:
    binary = shutil.which("typst")
    if not binary:
        raise FileNotFoundError(
            "typst binary not on PATH. Install: cargo install typst-cli, "
            "or grab a release from https://github.com/typst/typst/releases."
        )
    return binary


def _load_findings(project_id: str) -> list[dict]:
    with cursor() as cur:
        cur.execute(
            """
            SELECT rule_id, severity, confidence_score, title, description, suggested_action,
                   tract_id, lease_id, assignee_level, deadline, status
            FROM curative_item
            WHERE project_id = %s
            ORDER BY
              CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3
                            WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END DESC,
              confidence_score DESC
            """,
            (project_id,),
        )
        return [dict(r) for r in cur.fetchall()]


_ASSIGNEE_LABELS = {
    "junior_landman": "Jr. landman",
    "senior_landman": "Sr. landman",
    "attorney_referral": "Atty referral",
    "operator_action": "Operator",
}


def _build_payload(ctx: ProjectContext, findings: list[dict]) -> dict[str, Any]:
    # Per-finding view
    fview: list[dict] = []
    for f in findings:
        fview.append({
            "rule_id": f["rule_id"],
            "severity": f["severity"],
            "title": f["title"],
            "description": f["description"],
            "suggested_action": f["suggested_action"],
            "effort_display": _effort_display(f.get("rule_id", "")),
            "assignee_display": _ASSIGNEE_LABELS.get(f.get("assignee_level") or "", "—"),
        })

    overall_color, overall_text = _badge_for_findings(fview, _has_clause_data(ctx))

    # Acquisition status per-tract (mirror of render.pdf logic)
    acq_rows: list[dict] = []
    for tract in ctx.tracts:
        tract_leases = ctx.leases_for_tract(tract.id)
        has_tract_clauses = any(
            le.primary_term_end or le.royalty_fraction or le.has_pugh_clause
            or le.depth_limit_ft for le in tract_leases
        )
        tract_findings = [f for f in fview if f.get("tract_id") == tract.id]
        crit = sum(1 for f in tract_findings if f["severity"] == "critical")
        high = sum(1 for f in tract_findings if f["severity"] == "high")
        if crit > 0:
            color, text_ = "solid-red", "Critical"
        elif high > 0:
            color, text_ = "solid-amber", "Blocked"
        elif not has_tract_clauses:
            color, text_ = "outline-grey", "Insufficient data"
        else:
            color, text_ = "solid-green", "No findings"
        nri_summary = "—"
        royalties = [float(le.royalty_fraction) for le in tract_leases if le.royalty_fraction is not None]
        if royalties:
            nri_summary = f"royalty {min(royalties):.4f} – {max(royalties):.4f}"
        acq_rows.append({
            "label": _clean_tract_label(tract.label),
            "badge_color": color,
            "badge_text": text_,
            "open_count": len(tract_findings),
            "critical_count": crit,
            "nri_summary": nri_summary,
        })

    # Tract chain blocks
    tract_view = []
    for t in ctx.tracts:
        tract_view.append({
            "label": _clean_tract_label(t.label),
            "chain_entries": _chain_entries_for_tract(ctx, t),
        })

    # County resolution
    counties = {t.county_fips for t in ctx.tracts}
    county_fips = next(iter(counties), "—") if counties else "—"
    county_name = COUNTIES.get(county_fips).name if county_fips in COUNTIES else "Unknown"

    # Headline lead — same shape as the prior renderer
    findings_count = len(fview)
    summary_critical = sum(1 for f in fview if f["severity"] == "critical")
    summary_high = sum(1 for f in fview if f["severity"] == "high")
    if findings_count == 0:
        headline = "No curative items detected"
        body = (
            "The examined records reconcile cleanly against the registered "
            "rule set. Periodic re-examination is recommended as the rule "
            "registry continues to evolve. Records expressly excluded from "
            "this examination are listed in Section V."
        )
    else:
        sev_pieces = []
        if summary_critical:
            sev_pieces.append(f"{summary_critical} critical")
        if summary_high:
            sev_pieces.append(f"{summary_high} high")
        sev_descr = ", ".join(sev_pieces) if sev_pieces else "all medium / low"
        item_word = "item" if findings_count == 1 else "items"
        tract_word = "tract" if len(ctx.tracts) == 1 else "tracts"
        headline = f"{findings_count} curative {item_word}"
        body = (
            f"{findings_count} curative {item_word} identified across "
            f"{len(ctx.tracts)} {tract_word} ({sev_descr}). Each finding "
            f"cites the specific instrument and the chain-of-title rationale; "
            f"curative-effort estimates assume current East-Texas operator "
            f"pricing. Findings flagged as historical should be confirmed "
            f"against the recorded-release index before action — see Section "
            f"V for the records expressly excluded from this examination."
        )

    # Verified release count for the cover meta + Section V summary
    with cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM chain_event "
            "WHERE county_fips = %s AND event_type = 'release'",
            (county_fips,),
        )
        verified_release_count = (cur.fetchone() or {}).get("n", 0)

    unattributed = _unattributed_leases(county_fips)

    return {
        "project": {
            "project_id": ctx.project_id,
            "label": ctx.label,
            "tracts": [{"id": t.id, "label": _clean_tract_label(t.label)} for t in ctx.tracts],
            "leases_total": len(ctx.leases) + len(unattributed),
            "leases_attributed": len(ctx.leases),
            "wells_formatted": f"{len(ctx.wells):,}",
        },
        "county_fips": county_fips,
        "county_name": county_name,
        "findings": fview,
        "summary": {"headline": headline, "body": body},
        "overall_badge_color": overall_color,
        "overall_badge_text": overall_text,
        "lease_calendar": _lease_calendar(ctx),
        "acquisition_status": acq_rows,
        "tracts": tract_view,
        "unattributed_leases": unattributed,
        "clause_coverage": _clause_coverage_breakdown(ctx),
        "examination_period": _examination_period(ctx),
        "rrc_pulled_at": _rrc_pulled_at(),
        "data_sources": _resolve_actual_data_sources(county_fips, county_name),
        "rules_total": _count_registered_rules(),
        "verified_release_count": int(verified_release_count or 0),
        "facttrack_version": __version__,
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }


@dataclass
class TypstRenderResult:
    pdf_path: Path
    typ_path: Path
    data_json_path: Path


def render_typst_pdf(project_id: str) -> TypstRenderResult:
    """Compile the magazine-grade PDF for `project_id` via Typst."""
    ensure_dirs()
    ctx = load_project(project_id)
    findings = _load_findings(project_id)
    payload = _build_payload(ctx, findings)

    out_dir = PATHS.reports / project_id
    out_dir.mkdir(parents=True, exist_ok=True)

    data_json = out_dir / "report.data.json"
    data_json.write_text(json.dumps(payload, cls=_JSONFallback, indent=2), encoding="utf-8")

    # Copy template next to the JSON so relative paths inside typst resolve.
    typ_target = out_dir / "report.typ"
    typ_target.write_text(_TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    pdf_path = out_dir / "report.pdf"
    # Typst resolves json() paths relative to the .typ file's directory; pass
    # just the basename so it doesn't double-prefix the absolute path.
    cmd = [
        _typst_binary(), "compile",
        str(typ_target), str(pdf_path),
        "--input", f"data={data_json.name}",
    ]
    log.info("typst compile: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(
            f"typst failed (exit {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
        )
    log.info("rendered PDF → %s (%d bytes)", pdf_path, pdf_path.stat().st_size)
    return TypstRenderResult(pdf_path=pdf_path, typ_path=typ_target, data_json_path=data_json)


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    r = render_typst_pdf(args.project)
    print(f"PDF: {r.pdf_path}")
    print(f"DATA: {r.data_json_path}")
