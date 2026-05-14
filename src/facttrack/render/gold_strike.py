"""Gold-strike summary — cross-county roll-up of the strongest critical / high
curative findings, ranked by acquisition value. The artifact Randell flips to
first thing Monday morning.

For each tract with at least one critical or high finding, surfaces:
  - The finding(s)
  - Current well status (HBP / shut-in / marginal / open)
  - NRI to Monument if acquired
  - Recommended action (top-lease, AOH cure, etc.)
  - Link to the full deal memo for that tract

Output is a single PDF compiled via Typst.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from facttrack import __version__
from facttrack.config import COUNTIES, PATHS
from facttrack.db import cursor
from facttrack.engine.context import load_project
from facttrack.engine.dealmemo import (
    _current_status_for_tract,
    _curative_scorecard,
    _competitive_landscape,
    _recommendation,
)
from facttrack.engine.nri import compute_nri_for_tract
from facttrack.render.pdf import _clean_tract_label

log = logging.getLogger(__name__)

_TEMPLATE = Path(__file__).parent / "templates" / "gold_strike.typ"


def _gold_findings_for_county(project_id: str) -> list[dict]:
    """Build per-tract dossiers for every tract with at least one critical /
    high finding in the project."""
    try:
        ctx = load_project(project_id)
    except Exception as e:
        log.warning("project %s not loadable: %s", project_id, e)
        return []
    with cursor() as cur:
        cur.execute(
            """
            SELECT rule_id, severity, title, tract_id, lease_id, suggested_action
              FROM curative_item
             WHERE project_id = %s AND severity IN ('critical', 'high')
            """,
            (project_id,),
        )
        findings = [dict(r) for r in cur.fetchall()]
    if not findings:
        return []

    tract_ids = {f["tract_id"] for f in findings if f.get("tract_id")}
    out: list[dict] = []
    for tract in ctx.tracts:
        if tract.id not in tract_ids:
            continue
        t_leases = ctx.leases_for_tract(tract.id)
        t_findings = [{"rule_id": f["rule_id"], "severity": f["severity"],
                       "title": f["title"], "tract_id": tract.id}
                      for f in findings if f.get("tract_id") == tract.id]
        status = _current_status_for_tract(tract.id, ctx)
        scorecard = _curative_scorecard(tract.id, t_findings)
        competitive = _competitive_landscape(tract.id, ctx)
        nri = compute_nri_for_tract(
            tract_label=_clean_tract_label(tract.label),
            leases=t_leases,
            chain_events=ctx.chain_events,
            wi_share=1.0,
        )
        recommendation = _recommendation(status, scorecard, competitive, nri)
        county_name = (COUNTIES.get(tract.county_fips).name
                       if tract.county_fips in COUNTIES else "Unknown")
        out.append({
            "project_id": project_id,
            "county_fips": tract.county_fips,
            "county_name": county_name,
            "tract_id": tract.id,
            "tract_label": _clean_tract_label(tract.label),
            "gross_acres": (f"{float(tract.gross_acres):g}"
                            if tract.gross_acres else "—"),
            "well_count": status["well_count"],
            "producing_count": status["producing_count"],
            "status_label": status["status"],
            "primary_operators": status["primary_operators"][:3],
            "lease_count": len(t_leases),
            "leases": [{
                "instrument": le.opr_instrument_no,
                "lessor": le.lessor_text or "—",
                "lessee": le.lessee_text or "—",
                "royalty": (f"{float(le.royalty_fraction):.4f}".rstrip("0").rstrip(".")
                            if le.royalty_fraction is not None else "—"),
            } for le in t_leases],
            "findings": [{
                "rule_id": f["rule_id"],
                "severity": f["severity"],
                "title": f["title"],
            } for f in findings if f.get("tract_id") == tract.id],
            "nri_value": nri.nri,
            "recommendation": recommendation["recommendation"],
            "recommendation_reasons": recommendation["reasons"],
            "walkaway_per_ac": recommendation.get("walkaway_bonus_ceiling_per_ac"),
            "critical_count": scorecard["crit_count"],
            "high_count": scorecard["high_count"],
            "dealmemo_path": (
                f"reports/{project_id}/dealmemos/dealmemo_tract_{tract.id}.pdf"
            ),
        })
    return out


def build_gold_strike(project_ids: list[str]) -> dict:
    """Build the gold-strike payload spanning multiple county projects."""
    all_strikes: list[dict] = []
    counties_scanned: list[dict] = []
    total_findings = 0
    for pid in project_ids:
        try:
            with cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n, "
                    "count(*) FILTER (WHERE severity='critical') AS crit, "
                    "count(*) FILTER (WHERE severity='high') AS high "
                    "FROM curative_item WHERE project_id = %s",
                    (pid,),
                )
                stats = dict(cur.fetchone() or {})
                cur.execute(
                    "SELECT label FROM project WHERE id = %s",
                    (pid,),
                )
                proj = cur.fetchone() or {}
                cur.execute(
                    "SELECT t.county_fips, count(DISTINCT t.id) AS tracts, "
                    "count(DISTINCT l.id) AS leases "
                    "FROM project_tract pt "
                    "JOIN tract t ON t.id = pt.tract_id "
                    "LEFT JOIN lease l ON l.tract_id = t.id "
                    "WHERE pt.project_id = %s GROUP BY t.county_fips",
                    (pid,),
                )
                breakdown = [dict(r) for r in cur.fetchall()]
            strikes = _gold_findings_for_county(pid)
            total_findings += int(stats.get("n") or 0)
            for b in breakdown:
                county_name = (COUNTIES.get(b["county_fips"]).name
                               if b["county_fips"] in COUNTIES else b["county_fips"])
                counties_scanned.append({
                    "project_id":    pid,
                    "county_fips":   b["county_fips"],
                    "county_name":   county_name,
                    "tract_count":   int(b["tracts"] or 0),
                    "lease_count":   int(b["leases"] or 0),
                    "critical_count": int(stats.get("crit") or 0) if b == breakdown[0] else 0,
                    "high_count":    int(stats.get("high") or 0) if b == breakdown[0] else 0,
                    "total_findings": int(stats.get("n") or 0) if b == breakdown[0] else 0,
                })
            all_strikes.extend(strikes)
        except Exception as e:
            log.warning("skipping %s: %s", pid, e)

    # Rank: critical-first, then well count, then NRI
    def _rank_key(s: dict) -> tuple:
        return (
            -s["critical_count"],
            -s["high_count"],
            -s["producing_count"],
            -s["well_count"],
            -s["nri_value"],
        )
    all_strikes.sort(key=_rank_key)

    return {
        "strikes":           all_strikes,
        "counties_scanned":  counties_scanned,
        "total_findings":    total_findings,
        "total_strikes":     len(all_strikes),
        "critical_strikes":  sum(1 for s in all_strikes if s["critical_count"] > 0),
        "high_strikes":      sum(1 for s in all_strikes if s["high_count"] > 0
                                 and s["critical_count"] == 0),
        "facttrack_version": __version__,
        "generated_at":      datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }


def render_gold_strike(project_ids: list[str], output: Path | None = None) -> Path:
    payload = build_gold_strike(project_ids)
    out_dir = PATHS.reports / "_gold_strike"
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output or (out_dir / "gold_strike.pdf")
    data_json = out_dir / "gold_strike.data.json"
    data_json.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    typ_target = out_dir / "gold_strike.typ"
    typ_target.write_text(_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
    typst = shutil.which("typst")
    if not typst:
        raise FileNotFoundError("typst not on PATH")
    cmd = [typst, "compile", str(typ_target), str(pdf_path),
           "--input", f"data={data_json.name}"]
    log.info("typst compile: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"typst exit {proc.returncode}: {proc.stderr.strip()}")
    log.info("gold-strike PDF → %s (%d bytes)", pdf_path, pdf_path.stat().st_size)
    return pdf_path


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", nargs="+",
                        default=[
                            "county_research_48001",
                            "county_research_48289",
                            "county_research_48161",
                            "county_research_48423",
                            "county_research_48347",
                            "county_research_48313",
                            "county_research_48471",
                        ])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = render_gold_strike(args.projects)
    print(p)
