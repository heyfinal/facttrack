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

    # ── Per-tract dossiers ─────────────────────────────────────────
    # Everything-on-one-page-per-tract view: legal description, leases on
    # tract with parsed clauses, wells (best-effort county-match), operators,
    # chain events, findings, recommended actions.
    tract_dossiers: list[dict] = []
    for t in ctx.tracts:
        t_leases = ctx.leases_for_tract(t.id)
        t_findings = [f for f in fview if f.get("tract_id") == t.id]
        t_chain = _chain_entries_for_tract(ctx, t)
        # Wells are county-scoped in load_project. Best-effort link by matching
        # the lessor surname against the well's lease_name as a WHOLE WORD —
        # not a substring (otherwise "HANKS" matches "SHANKS", "CAMP" matches
        # "CAMPBELL", etc. — a landman catches these in 30 seconds).
        import re as _re
        t_well_apis: set[str] = set()
        for w in ctx.wells:
            lease_name = (w.lease_name or "").upper()
            for le in t_leases:
                lessor_lastname = ((le.lessor_text or "").split() or [""])[0].upper()
                if not lessor_lastname or len(lessor_lastname) < 4:
                    continue
                if _re.search(rf"\b{_re.escape(lessor_lastname)}\b", lease_name):
                    t_well_apis.add(w.api_no)
                    break
        t_wells = [w for w in ctx.wells if w.api_no in t_well_apis]

        lease_summary = []
        for le in t_leases:
            lease_summary.append({
                "instrument": le.opr_instrument_no or "—",
                "recording_date": le.recording_date.isoformat() if le.recording_date else "—",
                "lessor": le.lessor_text or "—",
                "lessee": le.lessee_text or "—",
                "primary_term_years": float(le.primary_term_years) if le.primary_term_years else None,
                "primary_term_end": le.primary_term_end.isoformat() if le.primary_term_end else "—",
                "royalty": (
                    f"{float(le.royalty_fraction):.4f}".rstrip("0").rstrip(".")
                    if le.royalty_fraction is not None else "—"
                ),
                "has_pugh": "Yes" if le.has_pugh_clause else ("No" if le.has_pugh_clause is False else "—"),
                "depth_limit_ft": f"{float(le.depth_limit_ft):.0f}" if le.depth_limit_ft else "—",
            })
        well_summary = [{
            "api_no": w.api_no,
            "lease_name": (w.lease_name or "—"),
            "well_no": w.well_no or "",
            "status": w.status or "—",
            "spud_date": w.spud_date.isoformat() if w.spud_date else "—",
            "operator": (ctx.operators_by_p5[w.operator_p5].name
                         if w.operator_p5 and w.operator_p5 in ctx.operators_by_p5 else "—"),
        } for w in t_wells[:30]]
        tract_dossiers.append({
            "id": t.id,
            "label": _clean_tract_label(t.label),
            "abstract_no": t.abstract_no or "—",
            "survey_name": t.survey_name or "—",
            "gross_acres": (f"{float(t.gross_acres):g}" if t.gross_acres else "—"),
            "lease_count": len(t_leases),
            "well_count": len(t_wells),
            "finding_count": len(t_findings),
            "leases": lease_summary,
            "wells": well_summary,
            "chain": t_chain,
            "findings": [{"severity": f["severity"], "title": f["title"],
                          "rule_id": f["rule_id"], "effort": f["effort_display"]}
                         for f in t_findings],
        })

    # ── Multi-county RRC scope ────────────────────────────────────
    # The brother's territory at Monument spans Anderson + Houston counties.
    # OPR/chain analysis is Anderson-only (Houston OPR requires a paid
    # iDocket subscription). RRC wellbore + operator data is loaded for both
    # so the Operator and Wells sections present a complete picture of his
    # operator-side responsibilities.
    project_counties = {t.county_fips for t in ctx.tracts}
    rrc_counties = sorted(project_counties | {"48225"}) if project_counties else ["48001", "48225"]
    rrc_county_names = []
    for f in rrc_counties:
        cn = COUNTIES.get(f)
        rrc_county_names.append(cn.name if cn else f)

    operator_view: list[dict] = []
    with cursor() as cur:
        cur.execute(
            """
            SELECT o.rrc_p5_number, o.name, o.status,
                   count(DISTINCT w.api_no) AS well_count,
                   count(DISTINCT w.api_no) FILTER (WHERE w.status ILIKE '%%active%%'
                                                       OR w.status ILIKE '%%produc%%') AS active_count,
                   max(w.spud_date) AS latest_spud,
                   string_agg(DISTINCT c.name, ', ' ORDER BY c.name) AS counties
              FROM operator o
              JOIN well w ON w.operator_p5 = o.rrc_p5_number
              JOIN county c ON c.fips = w.county_fips
             WHERE w.county_fips = ANY(%s)
             GROUP BY o.rrc_p5_number, o.name, o.status
             ORDER BY well_count DESC
             LIMIT 30
            """,
            (rrc_counties,),
        )
        for row in cur.fetchall():
            operator_view.append({
                "p5":            row["rrc_p5_number"],
                "name":          row["name"] or "—",
                "status":        row["status"] or "—",
                "well_count":    int(row["well_count"] or 0),
                "active_count":  int(row["active_count"] or 0),
                "latest_spud":   row["latest_spud"].isoformat() if row.get("latest_spud") else "—",
                "counties":      row.get("counties") or "—",
            })

    # Wells across all RRC-scope counties, top 50 by spud date
    well_inventory: list[dict] = []
    with cursor() as cur:
        cur.execute(
            """
            SELECT w.api_no, w.county_fips, c.name AS county_name,
                   w.lease_name, w.well_no, w.spud_date, w.status,
                   o.name AS operator_name
              FROM well w
              JOIN county c ON c.fips = w.county_fips
              LEFT JOIN operator o ON o.rrc_p5_number = w.operator_p5
             WHERE w.county_fips = ANY(%s)
             ORDER BY w.spud_date DESC NULLS LAST
             LIMIT 50
            """,
            (rrc_counties,),
        )
        for row in cur.fetchall():
            well_inventory.append({
                "api_no":      row["api_no"],
                "county":      row["county_name"],
                "lease_name":  row["lease_name"] or "—",
                "well_no":     row["well_no"] or "",
                "operator":    row["operator_name"] or "—",
                "spud_date":   row["spud_date"].isoformat() if row.get("spud_date") else "—",
                "status":      row["status"] or "—",
            })

    # Per-county RRC summary for the Section VI/VII intro
    rrc_county_summary: list[dict] = []
    with cursor() as cur:
        cur.execute(
            """
            SELECT c.fips, c.name,
                   count(DISTINCT w.api_no) AS well_count,
                   count(DISTINCT w.operator_p5) AS operator_count,
                   count(DISTINCT w.api_no) FILTER (
                     WHERE w.status ILIKE '%%active%%' OR w.status ILIKE '%%produc%%'
                   ) AS active_count
              FROM county c
              LEFT JOIN well w ON w.county_fips = c.fips
             WHERE c.fips = ANY(%s)
             GROUP BY c.fips, c.name
             ORDER BY well_count DESC
            """,
            (rrc_counties,),
        )
        for row in cur.fetchall():
            rrc_county_summary.append({
                "fips":           row["fips"],
                "name":           row["name"],
                "well_count":     int(row["well_count"] or 0),
                "operator_count": int(row["operator_count"] or 0),
                "active_count":   int(row["active_count"] or 0),
            })

    # ── Lease document thumbnails for the appendix ────────────────
    lease_images: list[dict] = []
    cache_root = PATHS.cache / "lease_images" / county_fips
    if cache_root.exists():
        for le in ctx.leases:
            if not le.opr_instrument_no:
                continue
            safe_id = "".join(c if c.isalnum() or c in "._-" else "_" for c in le.opr_instrument_no)
            page1 = cache_root / safe_id / "page_01.png"
            if not page1.exists():
                continue
            # Copy to the project's reports dir so Typst can resolve relative
            target = PATHS.reports / ctx.project_id / "_lease_thumbs"
            target.mkdir(parents=True, exist_ok=True)
            target_path = target / f"{safe_id}_p1.png"
            if not target_path.exists():
                target_path.write_bytes(page1.read_bytes())
            lease_images.append({
                "instrument": le.opr_instrument_no,
                "parties": f"{(le.lessor_text or '—')[:48]} → {(le.lessee_text or '—')[:48]}",
                "recording_date": le.recording_date.isoformat() if le.recording_date else "—",
                "image_path": f"_lease_thumbs/{safe_id}_p1.png",
            })

    # Cover wells count spans both RRC scope counties (Anderson + Houston),
    # not just the project's OPR scope. The Wells Inventory section is
    # multi-county; the cover number must match.
    with cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM well WHERE county_fips = ANY(%s)",
            (rrc_counties,),
        )
        wells_total = int((cur.fetchone() or {}).get("n", 0))

    return {
        "project": {
            "project_id": ctx.project_id,
            "label": ctx.label,
            "tracts": [{"id": t.id, "label": _clean_tract_label(t.label)} for t in ctx.tracts],
            "leases_total": len(ctx.leases) + len(unattributed),
            "leases_attributed": len(ctx.leases),
            "wells_formatted": f"{wells_total:,}",
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
        "tract_dossiers": tract_dossiers,
        "operator_view": operator_view,
        "well_inventory": well_inventory,
        "lease_images": lease_images,
        "rrc_counties": rrc_county_names,
        "rrc_county_summary": rrc_county_summary,
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
