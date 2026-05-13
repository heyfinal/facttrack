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
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["format_thousands"] = lambda n: f"{int(n):,}" if n is not None else "—"
    return env


_ASSIGNEE_LABELS = {
    "junior_landman": "Jr. landman",
    "senior_landman": "Sr. landman",
    "attorney_referral": "Atty referral",
    "operator_action": "Operator",
}


def _assignee_display(level: Any) -> str:
    return _ASSIGNEE_LABELS.get(level or "", "—")


# Curative-effort estimates calibrated to East-Texas operator pricing 2026.
# Replaces wide $-impact bands (which read as inflated to working landmen) with
# the labor + recording shape a Director of Land actually scopes against.
_EFFORT_BY_RULE = {
    "r01_unrecorded_p4_assignment":     "2–4 hr jr · ~$250 rec.",
    "r02_probate_gap":                  "4–8 hr jr · district-clerk search · ~$300 rec.",
    "r04_depth_severance_mismatch":     "Title-opinion review · ~$2,500 atty",
    "r05_primary_term_no_continuous_prod": "Verify recorded release · 1–2 hr jr",
    "r06_pugh_release_missed":          "4–6 hr sr · ~$200 rec.",
    "r12_top_lease_conflict":           "Title-opinion review · ~$2,500 atty",
    "r16_mineral_royalty_ambiguity":    "Stipulation of interest · 6–12 hr sr",
    "r17_orri_cloud":                   "Notice of termination · 2–4 hr jr",
}


def _effort_display(rule_id: str) -> str:
    return _EFFORT_BY_RULE.get(rule_id, "Refer to senior landman")


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
    """Build the lease maintenance calendar.

    Pre-1980 expired leases are not shown as "EXPIRED N days ago" — a working
    landman reads that on a 67-year-old lease and instantly distrusts the
    report. They are shown as "Historical" with a hint to verify the release
    is on file before treating the absence-of-release as a chain defect.
    """
    today = date.today()
    rows: list[dict] = []
    for lease in ctx.leases:
        term_end = lease.primary_term_end
        if term_end is None:
            continue
        delta_days = (term_end - today).days
        years_past = -delta_days / 365.25 if delta_days < 0 else 0

        if delta_days < 0 and years_past > 5:
            # Pre-modern lease — almost certainly released, just possibly not
            # captured in our scrape. Don't scream EXPIRED at the reader.
            risk_pill = "outline-grey"
            risk_label = f"Historical ({term_end.year})"
            risk_note = "Verify recorded release before action"
        elif delta_days < 0:
            risk_pill = "critical"
            risk_label = "Term expired"
            risk_note = f"{abs(delta_days)} d past term end"
        elif delta_days < 180:
            risk_pill = "high"
            risk_label = "Expires < 6 mo"
            risk_note = f"{delta_days} d remaining"
        elif delta_days < 540:
            risk_pill = "medium"
            risk_label = "Expires < 18 mo"
            risk_note = f"~{delta_days // 30} mo remaining"
        else:
            risk_pill = "low"
            risk_label = "Active"
            risk_note = f"~{delta_days // 30} mo remaining"

        pugh_status = "—"
        if lease.has_pugh_clause:
            pugh_status = "Pugh + retained"
        elif lease.has_retained_acreage:
            pugh_status = "Retained acreage"

        parties = f"{(lease.lessor_text or '—')[:42]} → {(lease.lessee_text or '—')[:42]}"
        royalty = (
            f"{float(lease.royalty_fraction):.4f}".rstrip("0").rstrip(".")
            if lease.royalty_fraction is not None else "—"
        )
        rows.append({
            "instrument": lease.opr_instrument_no or f"lease #{lease.id}",
            "parties": parties,
            "recording_date": lease.recording_date.isoformat() if lease.recording_date else "—",
            "term_end": term_end.isoformat() if term_end else "—",
            "royalty_display": royalty,
            "pugh_status": pugh_status,
            "risk_pill": risk_pill,
            "risk_label": risk_label,
            "risk_note": risk_note,
            "_sort_key": (
                {"critical": 0, "high": 1, "medium": 2, "low": 3, "outline-grey": 4}.get(risk_pill, 5),
                term_end,
            ),
        })
    rows.sort(key=lambda r: r["_sort_key"])
    for r in rows:
        r.pop("_sort_key", None)
    return rows


def _chain_entries_for_tract(ctx: ProjectContext, tract) -> list[dict]:
    """Build a chronological chain for ONE tract.

    Only includes (a) leases whose tract_id == this tract, and (b) chain events
    explicitly linked to one of those leases via references_lease_id. Showing
    every county-wide chain event under every tract — the prior behavior —
    produces a page that visibly repeats the same 9 unrelated instruments under
    each of 13 tract headings, which reads as auto-generated noise to any
    landman with chain-of-title experience.
    """
    entries: list[dict] = []
    tract_leases = ctx.leases_for_tract(tract.id)
    tract_lease_ids = {le.id for le in tract_leases}
    for lease in tract_leases:
        entries.append({
            "date": lease.recording_date.isoformat() if lease.recording_date else "—",
            "kind": "Lease",
            "instrument": lease.opr_instrument_no or "—",
            "parties": f"{(lease.lessor_text or '—')[:48]} → {(lease.lessee_text or '—')[:48]}",
        })
    for ev in ctx.chain_events:
        if ev.references_lease_id not in tract_lease_ids:
            continue
        entries.append({
            "date": ev.recording_date.isoformat() if ev.recording_date else "—",
            "kind": ev.event_type.replace("_", " ").title(),
            "instrument": ev.opr_instrument_no or "—",
            "parties": f"{(ev.grantor_text or '—')[:48]} → {(ev.grantee_text or '—')[:48]}",
        })
    return sorted(entries, key=lambda e: e["date"] or "")


def _clause_coverage_pct(ctx: ProjectContext) -> int:
    if not ctx.leases:
        return 0
    parsed = sum(
        1 for le in ctx.leases
        if any([le.primary_term_years, le.royalty_fraction,
                le.has_pugh_clause, le.depth_limit_ft])
    )
    return round(100 * parsed / len(ctx.leases))


def _examination_period(ctx: ProjectContext) -> str:
    dates = [le.recording_date for le in ctx.leases if le.recording_date]
    dates += [ev.recording_date for ev in ctx.chain_events if ev.recording_date]
    if not dates:
        return "—"
    return f"{min(dates).isoformat()} – {max(dates).isoformat()}"


def _rrc_pulled_at() -> str:
    """When was the RRC wellbore data last refreshed?"""
    try:
        with cursor() as cur:
            cur.execute(
                """
                SELECT MAX(finished_at) AS t FROM ingestion_run
                 WHERE source LIKE 'rrc_mft:%%' AND rows_upserted > 0
                """
            )
            row = cur.fetchone()
            t = row.get("t") if row else None
            return t.date().isoformat() if t else "not yet pulled"
    except Exception:
        return "not yet pulled"


def _build_render_payload(
    ctx: ProjectContext,
    findings: list[dict],
    map_html_path: str | None,
    map_image_path: str | None,
) -> dict[str, Any]:
    # Per-finding display formatting. Dollar bands replaced with curative-effort
    # estimates — landmen scope work in labor + recording, not exposure bands.
    fview: list[dict] = []
    for f in findings:
        fview.append({
            **f,
            "effort_display": _effort_display(f.get("rule_id", "")),
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

    # Lead paragraph — written as a landman would, not as marketing copy.
    summary_findings_count = len(fview)
    summary_critical = sum(1 for f in fview if f["severity"] == "critical")
    summary_high = sum(1 for f in fview if f["severity"] == "high")
    if summary_findings_count == 0:
        headline = "No curative items detected"
        body = (
            "The examined records reconcile cleanly against the registered rule set. "
            "Periodic re-examination is recommended as the rule registry continues to evolve. "
            "Records expressly excluded from this examination are listed in Section V."
        )
    else:
        sev_pieces = []
        if summary_critical:
            sev_pieces.append(f"{summary_critical} critical")
        if summary_high:
            sev_pieces.append(f"{summary_high} high")
        sev_descr = ", ".join(sev_pieces) if sev_pieces else "all medium / low"
        headline = f"{summary_findings_count} curative items"
        body = (
            f"{summary_findings_count} curative items identified across "
            f"{len(ctx.tracts)} tracts ({sev_descr}). Each finding cites the specific instrument "
            f"and the chain-of-title rationale; curative-effort estimates assume current "
            f"East-Texas operator pricing. Findings flagged as historical should be confirmed "
            f"against the recorded-release index before action — see Section V for the records "
            f"expressly excluded from this examination."
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
        "clause_coverage_pct": _clause_coverage_pct(ctx),
        "examination_period": _examination_period(ctx),
        "rrc_pulled_at": _rrc_pulled_at(),
        "data_sources": data_sources,
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
