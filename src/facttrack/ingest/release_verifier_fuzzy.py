"""Second-pass release verifier with fuzzy lessee-name matching.

The base release_verifier finds releases where the lessee company name still
matches the modern grantor field. It misses cases where the lessee has gone
through M&A and the release was filed under the successor's name:
    COHO RESOURCES INC ──→ Halcón Resources ──→ Battalion Oil ──→ ...
    EP OPERATING CO    ──→ Cipher Mining        ──→ ...

This module re-checks every lease with an r05 critical / high finding using
broader grantor candidates: the lessee's distinctive root name, the lessor's
last name as grantee, and a list of known E-TX successor companies.

If a release is found, persists the chain_event row so the engine demotes
the r05 on next run. The audit transcript records every search attempted
so a landman can independently re-walk the logic.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright

from facttrack.config import HTTP
from facttrack.db import cursor
from facttrack.ingest.publicsearch import PUBLICSEARCH_TX_COUNTIES
from facttrack.ingest.release_verifier import COMPANY_NOISE, RELEASE_DOC_TOKENS

log = logging.getLogger(__name__)


# Known East-TX M&A successor chains. Lessee → list of names to try.
# Expand as we observe more aliases in the wild.
SUCCESSOR_CHAINS: dict[str, list[str]] = {
    "COHO RESOURCES": ["HALCON RESOURCES", "BATTALION OIL", "AETHON ENERGY"],
    "COHO RESOURCES INC": ["HALCON RESOURCES", "BATTALION OIL"],
    "EP OPERATING CO": ["EP ENERGY", "EP RESOURCES"],
    "EP OPERATING": ["EP ENERGY"],
    "BURLINGTON RESOURCES OIL & GAS": ["CONOCOPHILLIPS"],
    "ANADARKO E&P": ["OCCIDENTAL PETROLEUM", "OXY"],
    "TIDEWATER OIL COMPANY": ["GETTY OIL", "TEXACO", "CHEVRON"],
    "SHELL OIL COMPANY": ["SHELL OIL"],
}


def _strip_noise(name: str) -> str:
    """Reduce a company/individual name to its distinctive root for search."""
    if not name:
        return ""
    cleaned = re.sub(r"[^A-Z0-9 ]", " ", name.upper())
    words = [w for w in cleaned.split() if w not in COMPANY_NOISE]
    return " ".join(words[:2]).strip()  # first two distinctive words


def _search_candidates(lessor_text: str, lessee_text: str) -> list[str]:
    """Build the candidate grantor names to search for as a release filer."""
    cands: list[str] = []
    lessee_root = _strip_noise(lessee_text)
    lessor_root = _strip_noise(lessor_text)
    if lessee_root:
        cands.append(lessee_root)
    # Known successors
    for key, succs in SUCCESSOR_CHAINS.items():
        if key in (lessee_text or "").upper():
            cands.extend(succs)
    # Also try the lessor's surname alone — sometimes a release is found by
    # searching the GRANTEE side (the lessor receiving back the lease).
    if lessor_root:
        cands.append(lessor_root.split()[0])
    # Dedupe + filter
    seen = set()
    out = []
    for c in cands:
        c2 = c.strip()
        if not c2 or len(c2) < 4 or c2 in seen:
            continue
        seen.add(c2)
        out.append(c2)
    return out


@dataclass
class FuzzyMatch:
    lease_id: int
    instrument_no: str
    recording_date: date | None
    grantor_text: str
    grantee_text: str
    doc_type: str
    raw_row: str
    matched_via: str   # which candidate string surfaced this match


def _row_looks_like_release(row_text: str) -> bool:
    upper = row_text.upper()
    return any(tok in upper for tok in RELEASE_DOC_TOKENS)


def _row_release_doc_type(row_text: str) -> str:
    upper = row_text.upper()
    for tok in RELEASE_DOC_TOKENS:
        if tok in upper:
            return tok
    return ""


def _parse_release_row(row_text: str) -> tuple[str, str, date | None, str]:
    """Best-effort parse of a publicsearch.us result-row text into
    (grantor, grantee, recording_date, instrument_no)."""
    # Row format varies; rely on the same split logic as release_verifier
    # which uses whitespace-tokens for grantor/grantee/date/instrument.
    parts = row_text.split()
    # Find an ISO-ish date
    rec_date: date | None = None
    inst_no = ""
    for i, p in enumerate(parts):
        if re.match(r"^\d{1,2}/\d{1,2}/\d{4}$", p):
            try:
                m, d, y = p.split("/")
                rec_date = date(int(y), int(m), int(d))
            except Exception:
                pass
            if i + 1 < len(parts):
                inst_no = parts[i + 1]
            break
    grantor = " ".join(parts[:3])
    grantee = " ".join(parts[3:6])
    return grantor, grantee, rec_date, inst_no


def _publicsearch_search(page, query: str) -> list[str]:
    """Run a single keyword search on publicsearch.us; return raw row texts."""
    page.goto(page.url.split("?")[0], wait_until="networkidle", timeout=30_000)
    page.wait_for_timeout(1500)
    for sel in ("input[type='search']", "input[placeholder*='Search']"):
        inp = page.locator(sel).first
        if inp.count() > 0:
            inp.fill(query)
            inp.press("Enter")
            break
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:
        pass
    page.wait_for_timeout(2500)
    rows: list[str] = []
    for tr in page.locator("tr").all()[:80]:
        try:
            txt = (tr.inner_text() or "").strip()
        except Exception:
            continue
        if not txt or txt.startswith("GRANTOR"):
            continue
        rows.append(txt)
    return rows


def fuzzy_verify(county_fips: str, audit_path: Path | None = None) -> dict:
    """For every lease in `county_fips` with NO release_event on file but a
    critical-severity finding flag, run the broader candidate searches."""
    slug = PUBLICSEARCH_TX_COUNTIES.get(county_fips)
    if not slug:
        raise KeyError(f"county {county_fips} not on publicsearch.us")

    with cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT l.id, l.opr_instrument_no, l.recording_date,
                   l.lessor_text, l.lessee_text
              FROM facttrack.lease l
              JOIN facttrack.curative_item ci ON ci.lease_id = l.id
             WHERE l.county_fips = %s
               AND ci.severity IN ('critical', 'high')
               AND ci.rule_id = 'r05_primary_term_no_continuous_prod'
               AND NOT EXISTS (
                   SELECT 1 FROM facttrack.chain_event ce
                    WHERE ce.references_lease_id = l.id
                      AND ce.event_type = 'release'
               )
             ORDER BY l.id
            """,
            (county_fips,),
        )
        targets = cur.fetchall()

    if not targets:
        log.info("no speculative r05 leases to re-verify in %s", county_fips)
        return {"county_fips": county_fips, "leases_checked": 0,
                "releases_found": 0, "matches": []}

    audit_lines: list[str] = [
        f"=== fuzzy_release_verifier: county={county_fips} slug={slug} ===",
        f"speculative leases to re-check: {len(targets)}",
        "",
    ]
    matches: list[FuzzyMatch] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(
                user_agent=HTTP.user_agent,
                viewport={"width": 1400, "height": 1000},
            )
            page = ctx.new_page()
            page.goto(f"https://{slug}.tx.publicsearch.us/", wait_until="networkidle",
                      timeout=30_000)
            page.wait_for_timeout(1500)

            for row in targets:
                audit_lines.append(
                    f"--- lease id={row['id']} inst={row['opr_instrument_no']} "
                    f"lessor='{row['lessor_text']}' lessee='{row['lessee_text']}' ---"
                )
                candidates = _search_candidates(row["lessor_text"] or "", row["lessee_text"] or "")
                if not candidates:
                    audit_lines.append("  no candidates derivable; skipping")
                    continue
                found = False
                for cand in candidates:
                    if found:
                        break
                    audit_lines.append(f"  query='{cand}'")
                    try:
                        rows = _publicsearch_search(page, cand)
                    except Exception as e:
                        audit_lines.append(f"    search failed: {e}")
                        continue
                    audit_lines.append(f"    rows returned: {len(rows)}")
                    for rt in rows:
                        if not _row_looks_like_release(rt):
                            continue
                        # Heuristic: the row must mention the lessor's surname
                        # AND a release-document token. That filters incidental
                        # releases from other parties.
                        lessor_surname = ((row["lessor_text"] or "").split() or [""])[0].upper()
                        if (lessor_surname and len(lessor_surname) >= 4 and
                                lessor_surname not in rt.upper()):
                            continue
                        doc_type = _row_release_doc_type(rt)
                        grantor, grantee, rec_date, inst_no = _parse_release_row(rt)
                        audit_lines.append(
                            f"    candidate ({doc_type}): {rt[:240]}"
                        )
                        audit_lines.append(
                            f"  → RELEASE found via '{cand}': "
                            f"inst={inst_no} recorded={rec_date} type='{doc_type}'"
                        )
                        matches.append(FuzzyMatch(
                            lease_id=row["id"],
                            instrument_no=inst_no or f"FUZZY_{row['id']}",
                            recording_date=rec_date,
                            grantor_text=grantor,
                            grantee_text=grantee,
                            doc_type=doc_type,
                            raw_row=rt,
                            matched_via=cand,
                        ))
                        found = True
                        break
                if not found:
                    audit_lines.append("  → still no release found; r05 finding stands")
        finally:
            browser.close()

    # Persist matches
    if matches:
        with cursor(dict_rows=False) as cur:
            for m in matches:
                try:
                    cur.execute(
                        """
                        INSERT INTO chain_event (
                            county_fips, opr_instrument_no, recording_date, event_type,
                            grantor_text, grantee_text, references_lease_id,
                            raw_text, parsed_metadata, confidence_score
                        )
                        VALUES (%s, %s, %s, 'release', %s, %s, %s, %s, %s::jsonb, %s)
                        ON CONFLICT (county_fips, opr_instrument_no, event_type,
                                     references_lease_id) DO UPDATE
                          SET recording_date  = EXCLUDED.recording_date,
                              grantor_text    = EXCLUDED.grantor_text,
                              grantee_text    = EXCLUDED.grantee_text,
                              raw_text        = EXCLUDED.raw_text,
                              parsed_metadata = EXCLUDED.parsed_metadata
                        """,
                        (
                            county_fips, m.instrument_no, m.recording_date,
                            m.grantor_text, m.grantee_text, m.lease_id,
                            m.raw_row,
                            json.dumps({
                                "source": "publicsearch.us",
                                "verifier": "fuzzy_release_verifier",
                                "matched_via": m.matched_via,
                                "doc_type_raw": m.doc_type,
                            }),
                            0.70,  # lower confidence than first-pass verifier
                        ),
                    )
                except Exception as e:
                    log.warning("persist failed for lease %d: %s", m.lease_id, e)

    summary = {
        "county_fips": county_fips,
        "leases_checked": len(targets),
        "releases_found": len(matches),
        "matches": [{"lease_id": m.lease_id, "inst": m.instrument_no,
                     "matched_via": m.matched_via, "doc_type": m.doc_type,
                     "recorded": m.recording_date.isoformat() if m.recording_date else None}
                    for m in matches],
    }
    audit_lines.append("")
    audit_lines.append("=== summary ===")
    audit_lines.append(json.dumps(summary, indent=2, default=str))

    if audit_path:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text("\n".join(audit_lines), encoding="utf-8")
        log.info("wrote fuzzy-verifier audit → %s", audit_path)
    return summary


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--county", required=True)
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    out = fuzzy_verify(args.county, audit_path=args.audit)
    print(json.dumps(out, indent=2, default=str))
