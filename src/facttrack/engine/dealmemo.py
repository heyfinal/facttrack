"""Deal-memo generator — projects existing FactTrack data into the 1-page
acquisition-triage artifact a working landman walks into their boss's office
holding on a Wednesday morning.

The memo is intentionally NOT a substitute for the 33-page dossier. It is the
synthesis page. The dossier is the appendix the boss flips to when he asks
"show me the chain on that probate gap."

Six fixed blocks per the landman's spec:
    1. Header bar
    2. Current status (HBP / expired / open)
    3. Ownership & NRI
    4. Curative scorecard (LOW / MEDIUM / HIGH)
    5. Competitive landscape
    6. Recommendation (BUY / NEGOTIATE / WALK)
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from facttrack import __version__
from facttrack.config import COUNTIES, PATHS
from facttrack.db import cursor
from facttrack.engine.context import load_project
from facttrack.engine.nri import compute_nri_for_tract
from facttrack.render.pdf import _clean_tract_label, _effort_display

log = logging.getLogger(__name__)

_TEMPLATE = Path(__file__).parent.parent / "render" / "templates" / "dealmemo.typ"


_RECOMMEND_THRESHOLDS = {
    # Number of critical findings → recommendation default. Always overridable.
    0: "BUY",
    1: "NEGOTIATE",
}


def _current_status_for_tract(tract_id: int, ctx) -> dict:
    """Roll up well-status into a single 'current status' verdict for the tract."""
    # Reuse the per-tract well-link logic from the typst payload — best-effort
    # name match. Acceptable for triage; verifying linkage on RRC GIS is in
    # the methodology disclosure.
    import re as _re
    t_leases = ctx.leases_for_tract(tract_id)
    t_well_apis: set[str] = set()
    for w in ctx.wells:
        lease_name = (w.lease_name or "").upper()
        for le in t_leases:
            lessor_lastname = ((le.lessor_text or "").split() or [""])[0].upper()
            if lessor_lastname and len(lessor_lastname) >= 4 and \
               _re.search(rf"\b{_re.escape(lessor_lastname)}\b", lease_name):
                t_well_apis.add(w.api_no)
                break
    t_wells = [w for w in ctx.wells if w.api_no in t_well_apis]

    producing = [w for w in t_wells if (w.status or "").upper().startswith(("PRODUC", "ACTIVE"))]
    shutin    = [w for w in t_wells if "SHUT" in (w.status or "").upper()]
    inactive  = [w for w in t_wells if (w.status or "").upper() in {"PA", "PLUGGED", "NO PRODUCTION", "INACTIVE"}]

    if producing:
        status = "HBP / producing"
        rationale = f"{len(producing)} producing wellbore(s) on tract; lease likely held by production."
    elif shutin and not inactive:
        status = "HBP via shut-in"
        rationale = f"{len(shutin)} shut-in well(s); verify shut-in royalty payments before assuming HBP."
    elif t_wells:
        status = "Marginal / suspect"
        rationale = f"{len(t_wells)} well(s) on tract, none currently producing."
    else:
        status = "Open / no wells"
        rationale = "No RRC-indexed wellbores match this tract by name; verify on RRC GIS."

    return {
        "status": status, "rationale": rationale,
        "producing_count": len(producing),
        "shutin_count": len(shutin),
        "inactive_count": len(inactive),
        "well_count": len(t_wells),
        "primary_operators": sorted({
            ctx.operators_by_p5[w.operator_p5].name
            for w in t_wells
            if w.operator_p5 and w.operator_p5 in ctx.operators_by_p5
        })[:5],
        "last_spud": max((w.spud_date for w in t_wells if w.spud_date), default=None),
    }


def _curative_scorecard(tract_id: int, findings: list[dict]) -> dict:
    """Compress per-tract findings into a single LOW/MEDIUM/HIGH rating with
    rolled-up effort hours and cost."""
    t_findings = [f for f in findings if f.get("tract_id") == tract_id]
    crit = sum(1 for f in t_findings if f["severity"] == "critical")
    high = sum(1 for f in t_findings if f["severity"] == "high")
    medium = sum(1 for f in t_findings if f["severity"] == "medium")

    # Effort tier
    if crit >= 2 or high >= 3:
        tier = "HIGH"
    elif crit >= 1 or high >= 1:
        tier = "MEDIUM"
    elif medium >= 1:
        tier = "LOW"
    else:
        tier = "NONE"

    # Itemized effort lines
    items: list[dict] = []
    for f in t_findings:
        items.append({
            "rule_id":   f["rule_id"],
            "severity":  f["severity"],
            "title":     f["title"],
            "effort":    _effort_display(f["rule_id"]),
        })

    # Rough hour + recording cost estimate from the per-rule effort strings.
    # Strings look like "6–10 hr sr · county-clerk probate search · ~$300 rec."
    import re
    hours_lo, hours_hi, rec_cost = 0, 0, 0
    for f in t_findings:
        s = _effort_display(f["rule_id"])
        m = re.search(r"(\d+)\s*[–-]\s*(\d+)\s*hr", s)
        if m:
            hours_lo += int(m.group(1))
            hours_hi += int(m.group(2))
        m = re.search(r"~\$\s*([\d,]+)", s)
        if m:
            rec_cost += int(m.group(1).replace(",", ""))

    return {
        "tier": tier,
        "crit_count": crit,
        "high_count": high,
        "medium_count": medium,
        "items": items,
        "hours_lo": hours_lo,
        "hours_hi": hours_hi,
        "recording_cost": rec_cost,
    }


def _competitive_landscape(tract_id: int, ctx) -> dict:
    """Prior lessees and any competing top-leases on the tract."""
    t_leases = ctx.leases_for_tract(tract_id)
    lease_ids = {le.id for le in t_leases}
    prior_lessees: list[dict] = []
    for le in t_leases:
        prior_lessees.append({
            "instrument": le.opr_instrument_no,
            "lessee":     le.lessee_text or "—",
            "recorded":   le.recording_date.isoformat() if le.recording_date else "—",
        })

    # Any release events on file? If yes, prior lease was released — chain is clean
    releases: list[dict] = []
    top_leases: list[dict] = []
    for ev in ctx.chain_events:
        if ev.references_lease_id not in lease_ids:
            continue
        if ev.event_type == "release":
            releases.append({
                "instrument": ev.opr_instrument_no,
                "by":         ev.grantor_text or "—",
                "recorded":   ev.recording_date.isoformat() if ev.recording_date else "—",
            })
        elif ev.event_type == "top_lease":
            top_leases.append({
                "instrument": ev.opr_instrument_no,
                "by":         ev.grantee_text or "—",
                "recorded":   ev.recording_date.isoformat() if ev.recording_date else "—",
            })

    return {
        "prior_lessees": prior_lessees,
        "releases":      releases,
        "top_leases":    top_leases,
        "has_competing_top_lease": len(top_leases) > 0,
    }


def _recommendation(
    status: dict, scorecard: dict, competitive: dict, nri,
) -> dict:
    """Deterministic BUY / NEGOTIATE / WALK recommendation."""
    reasons: list[str] = []
    rec = "BUY"

    if scorecard["crit_count"] >= 2:
        rec = "WALK"
        reasons.append(f"{scorecard['crit_count']} critical chain defects — out of pocket on cure exceeds reasonable bonus.")
    elif scorecard["crit_count"] == 1 and scorecard["high_count"] == 0:
        rec = "NEGOTIATE"
        reasons.append("Single critical curative item — buyable contingent on the seller curing prior to closing or a bonus offset.")
    elif competitive["has_competing_top_lease"]:
        rec = "NEGOTIATE"
        reasons.append("Competing top-lease on file — confirm priority before committing bonus capital.")
    elif scorecard["high_count"] >= 1:
        rec = "NEGOTIATE"
        reasons.append(f"{scorecard['high_count']} high-severity finding(s) — buyable but cure pricing must be reflected in the offer.")
    elif scorecard["tier"] == "NONE" and status["status"].startswith(("Open", "Marginal")):
        rec = "BUY"
        reasons.append("Clean chain, no recorded HBP — proceed with standard form lease at market bonus.")
    elif scorecard["tier"] == "NONE" and status["status"].startswith("HBP"):
        rec = "WALK"
        reasons.append("Tract is HBP by current producer; no acquisition opportunity at this time — monitor for release.")
    else:
        reasons.append("Default — open chain, no recorded defects.")

    return {
        "recommendation": rec,
        "reasons": reasons,
        "walkaway_bonus_ceiling_per_ac": _bonus_ceiling(rec, nri, scorecard),
    }


def _bonus_ceiling(rec: str, nri, scorecard: dict) -> int | None:
    """Cap on what Monument should pay per acre given the curative drag.

    Crude heuristic — bosses will override but it anchors the conversation.
    Baseline East-TX modern bonus $300-$1,500/acre; we subtract a curative
    drag scaled to the scorecard hours."""
    if rec == "WALK":
        return None
    baseline = 750
    drag = 0
    if scorecard["tier"] == "MEDIUM":
        drag = 200
    elif scorecard["tier"] == "HIGH":
        drag = 600
    return max(0, baseline - drag)


def _load_findings_for_project(project_id: str) -> list[dict]:
    with cursor() as cur:
        cur.execute(
            """
            SELECT rule_id, severity, title, tract_id, lease_id, suggested_action
              FROM curative_item
             WHERE project_id = %s
            """,
            (project_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def build_dealmemo_for_tract(project_id: str, tract_id: int) -> dict:
    """Build the deal-memo payload for one tract."""
    ctx = load_project(project_id)
    tract = next((t for t in ctx.tracts if t.id == tract_id), None)
    if tract is None:
        raise ValueError(f"tract_id {tract_id} not in project {project_id}")

    findings = _load_findings_for_project(project_id)
    t_leases = ctx.leases_for_tract(tract_id)
    nri = compute_nri_for_tract(
        tract_label=_clean_tract_label(tract.label),
        leases=t_leases,
        chain_events=ctx.chain_events,
        wi_share=1.0,
    )
    status = _current_status_for_tract(tract_id, ctx)
    scorecard = _curative_scorecard(tract_id, findings)
    competitive = _competitive_landscape(tract_id, ctx)
    recommendation = _recommendation(status, scorecard, competitive, nri)

    return {
        "tract": {
            "label": _clean_tract_label(tract.label),
            "abstract_no": tract.abstract_no or "—",
            "survey_name": tract.survey_name or "—",
            "gross_acres": (f"{float(tract.gross_acres):g}" if tract.gross_acres else "—"),
            "county_fips": tract.county_fips,
            "county_name": (COUNTIES.get(tract.county_fips).name
                            if tract.county_fips in COUNTIES else "Unknown"),
        },
        "leases": [
            {
                "instrument": le.opr_instrument_no,
                "recorded":   le.recording_date.isoformat() if le.recording_date else "—",
                "lessor":     le.lessor_text or "—",
                "lessee":     le.lessee_text or "—",
                "royalty":    (f"{float(le.royalty_fraction):.4f}".rstrip("0").rstrip(".")
                               if le.royalty_fraction is not None else "—"),
                "primary_term_end": (le.primary_term_end.isoformat()
                                     if le.primary_term_end else "—"),
            }
            for le in t_leases
        ],
        "current_status": status,
        "nri": {
            "wi_share": nri.wi_share,
            "lessor_royalty": nri.lessor_royalty,
            "orri_burden": nri.orri_burden,
            "other_burden": nri.other_burden,
            "value": nri.nri,
            "value_pct": nri.nri * 100,
            "stack": [
                {"party": s.party, "kind": s.kind, "rate": s.rate,
                 "rate_pct": s.rate * 100, "source": s.source}
                for s in nri.stack
            ],
            "notes": nri.notes,
        },
        "curative_scorecard": scorecard,
        "competitive": competitive,
        "recommendation": recommendation,
        "project_id": project_id,
        "prepared_by": "FactTrack v" + __version__,
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }


def render_dealmemo(project_id: str, tract_id: int, output: Path | None = None) -> Path:
    """Render the 1-page deal-memo PDF for a tract via Typst."""
    payload = build_dealmemo_for_tract(project_id, tract_id)
    out_dir = PATHS.reports / project_id / "dealmemos"
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output or (out_dir / f"dealmemo_tract_{tract_id}.pdf")

    data_json = out_dir / f"dealmemo_tract_{tract_id}.data.json"
    data_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    typ_target = out_dir / f"dealmemo_tract_{tract_id}.typ"
    typ_target.write_text(_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")

    typst = shutil.which("typst")
    if not typst:
        raise FileNotFoundError("typst not on PATH")
    cmd = [typst, "compile", str(typ_target), str(pdf_path),
           "--input", f"data={data_json.name}"]
    log.info("typst compile: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"typst exit {proc.returncode}: {proc.stderr.strip()}")
    log.info("deal memo → %s (%d bytes)", pdf_path, pdf_path.stat().st_size)
    return pdf_path


def render_dealmemos_for_all_tracts(project_id: str) -> list[Path]:
    """Render a deal memo for every tract in the project."""
    ctx = load_project(project_id)
    paths: list[Path] = []
    for t in ctx.tracts:
        try:
            paths.append(render_dealmemo(project_id, t.id))
        except Exception as e:
            log.warning("dealmemo failed for tract %s: %s", t.id, e)
    return paths


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--tract", type=int, help="single tract id; if omitted, render all tracts")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.tract:
        path = render_dealmemo(args.project, args.tract)
        print(path)
    else:
        for p in render_dealmemos_for_all_tracts(args.project):
            print(p)
