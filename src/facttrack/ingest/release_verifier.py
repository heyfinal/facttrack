"""Grantor-side release verification on publicsearch.us.

For every lease in a county, search the county clerk's index for any document
where the *lessee* (e.g. "SHELL OIL COMPANY") is the grantor and the *lessor*
(e.g. "DAVENPORT") is the grantee, and the document type contains "RELEASE".
A matching row recorded after the lease date proves the lease was released —
which short-circuits rule_05's "primary term expired, no continuous prod"
finding (the lease died honestly, not silently).

This is run AFTER ingestion but BEFORE the rules engine. Verifier hits run live
against publicsearch.us — no caching, no mocks — and persist matches as
`chain_event` rows of `event_type='release'` linked back to the lease via
`references_lease_id`.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime

from playwright.sync_api import (
    Page, TimeoutError as PlaywrightTimeout, sync_playwright,
)

from facttrack.config import HTTP
from facttrack.db import cursor
from facttrack.ingest.publicsearch import PUBLICSEARCH_TX_COUNTIES

log = logging.getLogger(__name__)


# Tokens that frequently appear in publicsearch.us search output for releases.
RELEASE_DOC_TOKENS = (
    "RELEASE OF OIL AND GAS LEASE",
    "RELEASE OF OIL & GAS LEASE",
    "RELEASE OF LEASE",
    "PARTIAL RELEASE",
    "RELEASE OF OIL",
    "RELEASE",
)

# Words to drop from a lessee company name when building a search query —
# leaves the distinctive core (e.g. "SHELL OIL COMPANY" → "SHELL").
COMPANY_NOISE = {
    "COMPANY", "CO", "CORP", "CORPORATION", "INC", "INCORPORATED", "LTD",
    "LLC", "LLP", "LP", "OIL", "GAS", "PETROLEUM", "ENERGY", "RESOURCES",
    "OPERATING", "PRODUCTION", "PRODUCING", "EXPLORATION", "AND", "&",
}


@dataclass
class ReleaseMatch:
    lease_id: int
    instrument_no: str
    recording_date: date | None
    grantor_text: str
    grantee_text: str
    doc_type: str
    raw_row: str


def _company_core(lessee: str) -> str:
    """Reduce a lessee company string to its distinctive core."""
    tokens = re.split(r"[\s,./()]+", lessee.upper().strip())
    core = [t for t in tokens if t and t not in COMPANY_NOISE]
    if not core:
        return lessee.upper().strip()
    # Most identifying single token is usually the first (SHELL, TEXACO, GULF…)
    return core[0]


def _lessor_surname(lessor: str) -> str:
    """Pull the surname off a "LAST FIRST MIDDLE" lessor string."""
    tokens = re.split(r"[\s,]+", lessor.upper().strip())
    return tokens[0] if tokens else lessor.upper().strip()


def _parse_row_date(row_text: str) -> date | None:
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", row_text)
    if not m:
        return None
    for fmt in ("%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(m.group(0), fmt).date()
        except ValueError:
            continue
    return None


def _parse_row_instrument(row_text: str) -> str | None:
    # publicsearch.us renders instrument numbers like "1957-57563178"
    m = re.search(r"\b(19\d{2}|20\d{2})-(\d{4,12})\b", row_text)
    return f"{m.group(1)}-{m.group(2)}" if m else None


def _row_is_release(row_text: str) -> str | None:
    """Return the matched doc-type token if row looks like a release row."""
    up = row_text.upper()
    for token in RELEASE_DOC_TOKENS:
        if token in up:
            return token
    return None


def _search_publicsearch(
    page: Page, base_url: str, query: str
) -> list[str]:
    """Run a single keyword search; return raw row-text strings."""
    rows: list[str] = []
    page.goto(base_url, wait_until="networkidle", timeout=30_000)
    page.wait_for_timeout(1500)
    si = None
    for sel in ("input[type='search']", "input[placeholder*='Search']"):
        loc = page.locator(sel).first
        if loc.count() > 0:
            si = loc
            break
    if si is None:
        return rows
    si.fill(query)
    si.press("Enter")
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeout:
        pass
    page.wait_for_timeout(2500)

    for tr in page.locator("tr").all():
        try:
            txt = (tr.inner_text() or "").strip()
        except Exception:
            continue
        if not txt or txt.upper().startswith("GRANTOR"):
            continue
        if "\t" in txt or "  " in txt:
            rows.append(" ".join(txt.split()))
    return rows


def _find_release_for_lease(
    page: Page, base_url: str,
    *, lessee: str, lessor: str, lease_recording_date: date | None,
    audit_lines: list[str],
) -> ReleaseMatch | None:
    """Search publicsearch.us for a release of `lessee → lessor`.

    Strategy:
      1. Query the lessor surname (broad index pull — same approach as
         verify_hanks_aoh.py).
      2. Scan rows for a release-type instrument that names BOTH the lessee
         core and the lessor surname.
      3. Recording date must post-date the lease recording.
    """
    company_core = _company_core(lessee)
    surname = _lessor_surname(lessor)
    queries = [
        f"{surname} {company_core}",
        surname,
    ]
    seen_keys: set[str] = set()
    candidates: list[tuple[str, date | None]] = []

    for q in queries:
        try:
            rows = _search_publicsearch(page, base_url, q)
        except Exception as e:
            audit_lines.append(f"  ! search failed for {q!r}: {e}")
            continue
        audit_lines.append(f"  query={q!r}  rows={len(rows)}")
        for row_text in rows:
            up = row_text.upper()
            if company_core not in up or surname not in up:
                continue
            doc_token = _row_is_release(row_text)
            if not doc_token:
                continue
            key = row_text[:200]
            if key in seen_keys:
                continue
            seen_keys.add(key)
            rec_date = _parse_row_date(row_text)
            if (
                lease_recording_date is not None
                and rec_date is not None
                and rec_date < lease_recording_date
            ):
                continue
            candidates.append((row_text, rec_date))
            audit_lines.append(f"    candidate ({doc_token}): {row_text[:180]}")

    if not candidates:
        return None

    # Pick the earliest matching release after the lease date.
    candidates.sort(key=lambda c: c[1] or date.max)
    row_text, rec_date = candidates[0]
    inst = _parse_row_instrument(row_text) or ""
    doc_type = _row_is_release(row_text) or "RELEASE"
    return ReleaseMatch(
        lease_id=-1,  # filled in by caller
        instrument_no=inst,
        recording_date=rec_date,
        grantor_text=lessee,
        grantee_text=lessor,
        doc_type=doc_type,
        raw_row=row_text,
    )


def _upsert_release_event(match: ReleaseMatch, county_fips: str) -> None:
    if not match.instrument_no:
        log.warning(
            "release match for lease_id=%s has no parseable instrument — skipping upsert",
            match.lease_id,
        )
        return
    with cursor(dict_rows=False) as cur:
        cur.execute(
            """
            INSERT INTO chain_event (
                county_fips, opr_instrument_no, recording_date, event_type,
                grantor_text, grantee_text, references_lease_id, raw_text,
                parsed_metadata, confidence_score
            )
            VALUES (%s, %s, %s, 'release', %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (county_fips, opr_instrument_no, event_type, references_lease_id)
              DO UPDATE
              SET recording_date  = EXCLUDED.recording_date,
                  grantor_text    = EXCLUDED.grantor_text,
                  grantee_text    = EXCLUDED.grantee_text,
                  raw_text        = EXCLUDED.raw_text,
                  parsed_metadata = EXCLUDED.parsed_metadata
            """,
            (
                county_fips, match.instrument_no, match.recording_date,
                match.grantor_text, match.grantee_text, match.lease_id,
                match.raw_row,
                json.dumps({
                    "source": "publicsearch.us",
                    "verifier": "release_verifier",
                    "doc_type_raw": match.doc_type,
                }),
                0.85,
            ),
        )


def verify_lease_releases(
    county_fips: str,
    *,
    headless: bool = True,
    sleep_between_sec: float = 1.0,
    audit_log_path: str | None = None,
) -> dict[str, int]:
    """Verify every lease in a county against publicsearch.us release index.

    For each lease (lessor, lessee) in `county_fips`, search the county index
    for a matching grantor-side release. Persist any match as a `chain_event`
    of `event_type='release'`. Returns summary stats and optionally writes a
    full audit trail to `audit_log_path`.
    """
    slug = PUBLICSEARCH_TX_COUNTIES.get(county_fips)
    if not slug:
        raise KeyError(f"county {county_fips} not on publicsearch.us")
    base = f"https://{slug}.tx.publicsearch.us"

    with cursor() as cur:
        cur.execute(
            """
            SELECT id, opr_instrument_no, lessor_text, lessee_text, recording_date
            FROM lease
            WHERE county_fips = %s
              AND lessor_text IS NOT NULL
              AND lessee_text IS NOT NULL
            ORDER BY recording_date NULLS LAST
            """,
            (county_fips,),
        )
        leases = cur.fetchall()

    audit: list[str] = []
    audit.append(f"=== release_verifier: county_fips={county_fips} slug={slug} ===")
    audit.append(f"leases to check: {len(leases)}")
    audit.append("")

    summary = {"leases_checked": 0, "releases_found": 0, "leases_unreleased": 0}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            context = browser.new_context(
                user_agent=HTTP.user_agent,
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()
            page.set_default_timeout(30_000)

            for row in leases:
                summary["leases_checked"] += 1
                header = (
                    f"--- lease id={row['id']} inst={row['opr_instrument_no']} "
                    f"lessor={row['lessor_text']!r} lessee={row['lessee_text']!r} "
                    f"recorded={row['recording_date']} ---"
                )
                audit.append(header)
                log.info(header)
                try:
                    match = _find_release_for_lease(
                        page, base,
                        lessee=row["lessee_text"],
                        lessor=row["lessor_text"],
                        lease_recording_date=row["recording_date"],
                        audit_lines=audit,
                    )
                except Exception as e:
                    audit.append(f"  ! verifier exception: {e}")
                    log.warning("verifier exception on lease %s: %s", row["id"], e)
                    match = None

                if match is None:
                    audit.append("  → NO release found")
                    summary["leases_unreleased"] += 1
                else:
                    match.lease_id = row["id"]
                    audit.append(
                        f"  → RELEASE found: inst={match.instrument_no} "
                        f"recorded={match.recording_date} type={match.doc_type!r}"
                    )
                    summary["releases_found"] += 1
                    try:
                        _upsert_release_event(match, county_fips)
                    except Exception as e:
                        audit.append(f"  ! upsert failed: {e}")
                        log.warning("release upsert failed for lease %s: %s", row["id"], e)

                audit.append("")
                time.sleep(sleep_between_sec)
        finally:
            browser.close()

    audit.append("=== summary ===")
    audit.append(json.dumps(summary, indent=2))

    if audit_log_path:
        with open(audit_log_path, "w") as f:
            f.write("\n".join(audit) + "\n")
        log.info("wrote verifier audit trail → %s", audit_log_path)

    log.info("release_verifier summary: %s", summary)
    return summary


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--county", required=True)
    parser.add_argument("--audit", default=None, help="path to write audit log")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = verify_lease_releases(
        args.county,
        headless=not args.headed,
        audit_log_path=args.audit,
    )
    print(json.dumps(result, indent=2))
