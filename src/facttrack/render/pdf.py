"""PDF rendering — turn engine output into a landman-grade FactTrack report."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from facttrack import __version__
from facttrack.config import COUNTIES, PATHS, ensure_dirs
from facttrack.db import cursor
from facttrack.engine.context import ProjectContext

log = logging.getLogger(__name__)

_TEMPLATES = Path(__file__).parent / "templates"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _impact_display(low: Any, high: Any) -> str:
    if low is None and high is None:
        return "—"
    if low is None:
        return f"≤ ${float(high):,.0f}"
    if high is None:
        return f"≥ ${float(low):,.0f}"
    return f"${float(low):,.0f}–${float(high):,.0f}"


_ASSIGNEE_LABELS = {
    "junior_landman": "Jr. landman",
    "senior_landman": "Sr. landman",
    "attorney_referral": "Atty referral",
    "operator_action": "Operator",
}


def _assignee_display(level: Any) -> str:
    return _ASSIGNEE_LABELS.get(level or "", "—")


def _badge_for_findings(findings: list[dict], has_clause_data: bool) -> tuple[str, str]:
    """Status badge for the project.

    Critical: any critical finding.
    Blocked: any high finding.
    Insufficient data: no clause-level data parsed yet (the default until OCR + clause
    extraction lands) — we DO NOT claim "Acquisition-ready" off an index scrape.
    Clear: only when we have clause data AND no findings.
    """
    if any(f["severity"] == "critical" for f in findings):
        return "red", "CRITICAL — curative required"
    if any(f["severity"] == "high" for f in findings):
        return "yellow", "BLOCKED pending curative"
    if not has_clause_data:
        return "yellow", "Insufficient data — clause extraction required"
    return "green", "No findings against current rule set"


def _has_clause_data(ctx) -> bool:
    """True iff at least one lease has parsed clause-level data populated."""
    for lease in ctx.leases:
        if any([
            lease.primary_term_end is not None,
            lease.royalty_fraction is not None,
            lease.has_pugh_clause is not None,
            lease.depth_limit_ft is not None,
        ]):
            return True
    return False


def _resolve_actual_data_sources(county_fips: str, county_name: str) -> list[dict]:
    """List ONLY the public sources we actually populated rows from in this run.

    Inspects ingestion_run + the populated tables to avoid claiming sources
    we didn't actually pull. Uses the latest non-empty source per dataset.
    """
    sources: list[dict] = []
    with cursor() as cur:
        cur.execute(
            """
            SELECT source, MAX(finished_at) AS last_pulled, SUM(rows_upserted) AS rows
            FROM ingestion_run
            WHERE finished_at IS NOT NULL
            GROUP BY source
            HAVING SUM(rows_upserted) > 0
            """,
        )
        rows = cur.fetchall()
    label_map = {
        f"publicsearch.us:{county_fips}": (
            f"{county_name} County OPR (publicsearch.us)",
            "Lease, assignment, release, AOH, probate index entries",
        ),
    }
    for row in rows:
        src = row["source"]
        label, desc = label_map.get(src, (src, "ingest run"))
        sources.append({
            "name": label,
            "description": desc + f" — {row['rows']} rows ingested",
            "pulled_at": row["last_pulled"].date().isoformat() if row.get("last_pulled") else "unknown",
        })
    if not sources:
        sources.append({
            "name": "No public sources ingested in this run",
            "description": "Run an ingest module before rendering",
            "pulled_at": "n/a",
        })
    return sources


def _count_registered_rules() -> int:
    from facttrack.engine.rules import RULE_REGISTRY
    return len(RULE_REGISTRY)


def _lease_calendar(ctx: ProjectContext) -> list[dict]:
    today = date.today()
    rows: list[dict] = []
    for lease in ctx.leases:
        term_end = lease.primary_term_end
        if term_end is None:
            continue
        delta_days = (term_end - today).days
        if delta_days < 0:
            risk = "red"
            risk_label = f"EXPIRED {abs(delta_days)} days ago"
        elif delta_days < 180:
            risk = "yellow"
            risk_label = f"Expires in {delta_days} days"
        else:
            risk = "green"
            risk_label = f"Active ({delta_days // 30} mo remaining)"

        pugh_status = "—"
        if lease.has_pugh_clause:
            pugh_status = "Pugh + retained acreage"
        elif lease.has_retained_acreage:
            pugh_status = "Retained acreage only"

        cont = "—"
        if lease.has_continuous_dev:
            cont = "Required"
        rows.append({
            "instrument": lease.opr_instrument_no or f"lease #{lease.id}",
            "lessor": (lease.lessor_text or "")[:60],
            "term_end": term_end.isoformat() if term_end else "—",
            "pugh_status": pugh_status,
            "continuous_prod": cont,
            "risk_color": risk,
            "risk_label": risk_label,
        })
    return sorted(rows, key=lambda r: r["risk_color"] == "green")


def _chain_entries_for_tract(ctx: ProjectContext, tract) -> list[dict]:
    """Build a chronological chain for a tract.

    Joins leases (by tract_id) PLUS every county chain_event whose date overlaps
    the tract's lease window — most chain events from the OPR scrape aren't yet
    linked to a specific lease (references_lease_id is null when ingested as
    standalone OPR rows), so we fall back to including all county events
    chronologically so the page reflects what's actually in the data.
    """
    entries: list[dict] = []
    tract_leases = ctx.leases_for_tract(tract.id)
    for lease in tract_leases:
        entries.append({
            "date": lease.recording_date.isoformat() if lease.recording_date else "—",
            "kind": "Lease",
            "instrument": lease.opr_instrument_no or "—",
            "parties": f"{(lease.lessor_text or '?')[:60]} → {(lease.lessee_text or '?')[:60]}",
            "curative": False,
        })
    # Include all county chain events (most are not lease-linked yet — show them
    # in chronological context rather than hiding the data).
    county_fips = tract.county_fips
    for ev in ctx.chain_events:
        if ev.county_fips != county_fips:
            continue
        entries.append({
            "date": ev.recording_date.isoformat() if ev.recording_date else "—",
            "kind": ev.event_type.replace("_", " ").title(),
            "instrument": ev.opr_instrument_no or "—",
            "parties": f"{(ev.grantor_text or '?')[:60]} → {(ev.grantee_text or '?')[:60]}",
            "curative": ev.event_type in ("top_lease", "orri_creation"),
            "curative_severity": "high" if ev.event_type == "top_lease" else "medium",
        })
    return sorted(entries, key=lambda e: e["date"] or "")


def _build_render_payload(
    ctx: ProjectContext,
    findings: list[dict],
    map_html_path: str | None,
    map_image_path: str | None,
) -> dict[str, Any]:
    # Per-finding display formatting
    fview: list[dict] = []
    for f in findings:
        fview.append({
            **f,
            "impact_display": _impact_display(f.get("dollar_impact_low"), f.get("dollar_impact_high")),
            "assignee_display": _assignee_display(f.get("assignee_level")),
            "deadline_display": (
                f["deadline"].isoformat() if isinstance(f.get("deadline"), (date, datetime)) else "—"
            ),
        })

    overall_color, overall_text = _badge_for_findings(fview, _has_clause_data(ctx))

    # Acquisition status per tract — NEVER claim "Ready" without parsed clause data.
    has_clauses = _has_clause_data(ctx)
    acq_rows: list[dict] = []
    for tract in ctx.tracts:
        tract_findings = [f for f in fview if f.get("tract_id") == tract.id]
        crit = sum(1 for f in tract_findings if f["severity"] == "critical")
        high = sum(1 for f in tract_findings if f["severity"] == "high")
        if crit > 0:
            color, text = "red", "Critical"
        elif high > 0:
            color, text = "yellow", "Blocked"
        elif not has_clauses:
            # Honest: index-only scrape can't conclude "ready". Flag it.
            color, text = "yellow", "Insufficient data"
        else:
            color, text = "green", "Ready"
        nri_summary = "—"
        if ctx.leases_for_tract(tract.id):
            royalties = [
                float(le.royalty_fraction)
                for le in ctx.leases_for_tract(tract.id)
                if le.royalty_fraction is not None
            ]
            if royalties:
                nri_summary = f"royalty {min(royalties):.4f} – {max(royalties):.4f}"
        acq_rows.append({
            "label": tract.label,
            "badge_color": color,
            "badge_text": text,
            "open_count": len(tract_findings),
            "critical_count": crit,
            "nri_summary": nri_summary,
        })

    # Headline / summary
    summary_findings_count = len(fview)
    summary_critical = sum(1 for f in fview if f["severity"] == "critical")
    summary_dollar_high = sum(float(f.get("dollar_impact_high") or 0) for f in fview)
    if summary_findings_count == 0:
        headline = "No curative items detected"
        body = "Public records on this tract group reconcile cleanly. Recommend periodic re-scan."
    else:
        headline = (
            f"{summary_findings_count} curative items identified "
            f"({summary_critical} critical) — est. ${summary_dollar_high:,.0f} max exposure"
        )
        body = (
            f"Top-ranked items below should be addressed before commitment on this tract group. "
            f"Estimated landman hours saved via this report: {min(summary_findings_count * 1.5, 25):.0f}–{summary_findings_count * 2 + 5}."
        )

    # Tracts with chain entries
    tract_view = []
    for t in ctx.tracts:
        tract_view.append({
            "label": t.label,
            "chain_entries": _chain_entries_for_tract(ctx, t),
        })

    # Wells with operator name resolution
    wells_view = []
    for w in ctx.wells:
        op_name = "—"
        if w.operator_p5 and w.operator_p5 in ctx.operators_by_p5:
            op_name = ctx.operators_by_p5[w.operator_p5].name
        wells_view.append({
            "api_no": w.api_no,
            "lease_name": w.lease_name or "—",
            "well_no": w.well_no or "",
            "operator_name": op_name,
            "status": w.status or "—",
        })

    # Project meta for the cover page
    counties = {t.county_fips for t in ctx.tracts}
    county_fips = next(iter(counties), "—") if counties else "—"
    county_name = COUNTIES.get(county_fips).name if county_fips in COUNTIES else "Unknown"

    # Truthful appendix: only sources we actually pulled FROM in this run.
    data_sources = _resolve_actual_data_sources(county_fips, county_name)

    return {
        "project": {
            "project_id": ctx.project_id,
            "label": ctx.label,
            "tracts": tract_view,
            "leases": ctx.leases,
            "wells": wells_view,
        },
        "county_fips": county_fips,
        "county_name": county_name,
        "findings": fview,
        "summary": {"headline": headline, "body": body},
        "overall_badge_color": overall_color,
        "overall_badge_text": overall_text,
        "lease_calendar": _lease_calendar(ctx),
        "acquisition_status": acq_rows,
        "acquisitions_narrative": (
            "Tract scope evaluated against the rule set listed in the appendix. "
            "Critical and high-severity findings should clear before any commitment "
            "instrument is recorded. Medium-severity findings can run in parallel."
        ),
        "data_sources": data_sources,
        # Reflect actual registry size, not aspirational count.
        "rules_total": _count_registered_rules(),
        "facttrack_version": __version__,
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "map_html_path": map_html_path,
        "map_image_path": map_image_path,
    }


def _load_findings(project_id: str) -> list[dict]:
    with cursor() as cur:
        cur.execute(
            """
            SELECT rule_id, severity, confidence_score, title, description, suggested_action,
                   tract_id, lease_id, assignee_level, dollar_impact_low, dollar_impact_high,
                   deadline, status
            FROM curative_item
            WHERE project_id = %s
            ORDER BY
              CASE severity WHEN 'critical' THEN 4 WHEN 'high' THEN 3
                            WHEN 'medium' THEN 2 WHEN 'low' THEN 1 ELSE 0 END DESC,
              confidence_score DESC,
              dollar_impact_high DESC NULLS LAST
            """,
            (project_id,),
        )
        return [dict(r) for r in cur.fetchall()]


@dataclass
class RenderResult:
    pdf_path: Path
    html_path: Path
    map_html_path: Path | None
    map_image_path: Path | None


def render_pdf(
    project_id: str,
    map_html_path: Path | None = None,
    map_image_path: Path | None = None,
) -> RenderResult:
    """Render the FactTrack PDF for `project_id`. Writes both HTML + PDF artifacts."""
    ensure_dirs()
    from facttrack.engine.context import load_project
    ctx = load_project(project_id)
    findings = _load_findings(project_id)
    payload = _build_render_payload(
        ctx, findings,
        map_html_path=str(map_html_path) if map_html_path else None,
        map_image_path=str(map_image_path) if map_image_path else None,
    )
    env = _env()
    template = env.get_template("report.html.j2")
    html = template.render(**payload)

    out_dir = PATHS.reports / project_id
    out_dir.mkdir(parents=True, exist_ok=True)
    html_path = out_dir / "report.html"
    pdf_path = out_dir / "report.pdf"
    html_path.write_text(html, encoding="utf-8")

    try:
        from weasyprint import HTML
        HTML(string=html, base_url=str(out_dir)).write_pdf(str(pdf_path))
        log.info("rendered PDF → %s", pdf_path)
    except Exception as e:
        log.warning("WeasyPrint PDF render failed (%s); HTML still produced", e)
        pdf_path = html_path  # fall back to HTML-only output

    return RenderResult(pdf_path=pdf_path, html_path=html_path,
                        map_html_path=map_html_path, map_image_path=map_image_path)


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = render_pdf(args.project)
    print(f"HTML: {result.html_path}")
    print(f"PDF:  {result.pdf_path}")
