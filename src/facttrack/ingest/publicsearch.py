"""publicsearch.us county clerk OPR scraper.

publicsearch.us is the platform used by many Texas county clerks to publish
their Official Public Records online. The instance pattern is:
    https://<county-slug>.tx.publicsearch.us/

Known free TX county subdomains verified 2026-05-13:
    anderson, leon, freestone, smith, nacogdoches, madison, walker.
(Houston County uses iDocket — a separate, subscription-based platform.)

The site is htmx + hyperscript driven; form submits return rendered HTML
fragments rather than a JSON API. We drive it through Playwright + parse the
results table DOM directly. Anonymous searches are allowed.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterator

from playwright.sync_api import (
    Browser, BrowserContext, Page, TimeoutError as PlaywrightTimeout, sync_playwright,
)

from facttrack.config import HTTP
from facttrack.db import cursor

log = logging.getLogger(__name__)


PLATFORM_BASE = "https://{slug}.tx.publicsearch.us"

PUBLICSEARCH_TX_COUNTIES: dict[str, str] = {
    "48001": "anderson",
    "48289": "leon",
    "48161": "freestone",
    "48423": "smith",
    "48347": "nacogdoches",
    "48313": "madison",
    "48471": "walker",
}


# Document-type strings publicsearch.us uses → our canonical event_type values.
DOC_TYPE_MAP: dict[str, str] = {
    # Longest / most specific first — substring matching in canonical_event_type
    # iterates this dict and the first key that's a substring wins. "RELEASE OF
    # OIL & GAS LEASE" must beat "LEASE" or it gets misclassified.
    "MEMORANDUM OF OIL & GAS LEASE":   "lease",
    "MEMORANDUM OF OIL AND GAS LEASE": "lease",
    "OIL, GAS AND MINERAL LEASE":      "lease",
    "OIL AND GAS LEASE":               "lease",
    "OIL & GAS LEASE":                 "lease",
    "MINERAL LEASE":                   "lease",
    "OIL GAS LEASE":                   "lease",
    "ASSIGNMENT OF OIL & GAS LEASE":   "assignment",
    "ASSIGNMENT OF OIL AND GAS LEASE": "assignment",
    "PARTIAL ASSIGNMENT":              "assignment",
    "ASSIGNMENT OF LEASE":             "assignment",
    "RELEASE OF OIL & GAS LEASE":      "release",
    "RELEASE OF OIL AND GAS LEASE":    "release",
    "PARTIAL RELEASE":                 "release",
    "RELEASE OF LEASE":                "release",
    "RATIFICATION OF LEASE":           "ratification",
    "RATIFICATION":                    "ratification",
    "EXTENSION OF LEASE":              "extension",
    "EXTENSION":                       "extension",
    "TOP LEASE":                       "top_lease",
    "AFFIDAVIT OF HEIRSHIP":           "aoh",
    "HEIRSHIP AFFIDAVIT":              "aoh",
    "RECORD OF PROBATE":               "rop",
    "OVERRIDING ROYALTY INTEREST":     "orri_creation",
    "OVERRIDING ROYALTY":              "orri_creation",
    "ORRI":                            "orri_creation",
    "RELEASE OF ORRI":                 "orri_release",
    "DECLARATION OF POOLED UNIT":      "pooled_unit",
    "UNIT DESIGNATION":                "pooled_unit",
    "POOLED UNIT":                     "pooled_unit",
    "PROBATE":                         "probate",
    "ESTATE":                          "probate",
}

# Substrings that, when present in the doc type, mean it is NOT an oil-and-gas
# instrument and must be skipped regardless of other matches. Catches the
# class of bugs where "RELEASE OF LIEN" was wrongly classified as a release
# of lease, or "DEED OF TRUST" as a lease.
NON_OG_MARKERS = (
    "LIEN", "DEED OF TRUST", "MORTGAGE", "VENDOR", "JUDGMENT", "MARRIAGE",
    "DIVORCE", "POWER OF ATTORNEY", "BIRTH", "DEATH CERTIFICATE",
    "TAX", "BANKRUPTCY", "WARRANTY DEED", "QUITCLAIM", "SUBORDINATION",
    "EASEMENT", "RIGHT OF WAY", "ROW", "PIPELINE EASEMENT",
)


@dataclass
class PSDocument:
    instrument_no: str | None
    recording_date: date | None
    instrument_type: str | None
    grantor: str | None
    grantee: str | None
    book: str | None = None
    page: str | None = None
    legal: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def canonical_event_type(self) -> str | None:
        if not self.instrument_type:
            return None
        key = self.instrument_type.upper().strip()
        # Reject obviously-non-O&G documents (mortgage releases, deeds of trust,
        # marriage licenses, etc.) regardless of what substrings they match.
        for marker in NON_OG_MARKERS:
            if marker in key:
                return None
        if key in DOC_TYPE_MAP:
            return DOC_TYPE_MAP[key]
        # Iterate longest-key first so specific patterns beat short ones.
        for k in sorted(DOC_TYPE_MAP.keys(), key=len, reverse=True):
            if k in key:
                return DOC_TYPE_MAP[k]
        return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


class PublicSearchClient:
    """Playwright-based scraper for publicsearch.us county clerk portals."""

    def __init__(self, county_fips: str, headless: bool = True) -> None:
        slug = PUBLICSEARCH_TX_COUNTIES.get(county_fips)
        if slug is None:
            raise KeyError(
                f"County FIPS {county_fips} is not on publicsearch.us. "
                f"Supported: {sorted(PUBLICSEARCH_TX_COUNTIES)}"
            )
        self.county_fips = county_fips
        self.slug = slug
        self.base = PLATFORM_BASE.format(slug=slug)
        self._headless = headless

    def search(
        self,
        *,
        recorded_from: date,
        recorded_to: date,
        max_results: int = 500,
    ) -> Iterator[PSDocument]:
        """Yield documents recorded in [recorded_from, recorded_to].

        Uses publicsearch.us simple search with a date range and an empty
        keyword, which returns all OPR documents in the window.
        """
        log.info("publicsearch.us scrape %s %s → %s (max=%d)",
                 self.slug, recorded_from, recorded_to, max_results)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self._headless)
            try:
                context = browser.new_context(
                    user_agent=HTTP.user_agent,
                    viewport={"width": 1400, "height": 1000},
                )
                page = context.new_page()
                page.set_default_timeout(20_000)

                # Go to advanced search
                page.goto(f"{self.base}/search/advanced", wait_until="networkidle")
                page.wait_for_timeout(1500)

                # Fill the recorded-date range
                fmt = "%m/%d/%Y"
                date_inputs = page.locator(
                    "input[name='recordedDateRange-from'], input[name*='recordedDateFrom'], "
                    "input[placeholder*='From'], input[aria-label*='From']"
                )
                if date_inputs.count() == 0:
                    # Fallback — fill the first two text-like inputs that look like dates
                    candidates = page.locator("input").all()
                    text_inputs = [
                        c for c in candidates
                        if (c.get_attribute("type") or "text").lower() in ("text", "")
                        and "date" in ((c.get_attribute("aria-label") or "") + (c.get_attribute("placeholder") or "")).lower()
                    ]
                    if len(text_inputs) >= 2:
                        text_inputs[0].fill(recorded_from.strftime(fmt))
                        text_inputs[1].fill(recorded_to.strftime(fmt))
                else:
                    # Best guess: first occurrence = from, second = to
                    date_inputs.nth(0).fill(recorded_from.strftime(fmt))
                    to_input = page.locator(
                        "input[name='recordedDateRange-to'], input[name*='recordedDateTo'], "
                        "input[placeholder*='To'], input[aria-label*='To']"
                    ).first
                    if to_input.count() > 0:
                        to_input.fill(recorded_to.strftime(fmt))

                # Submit the search
                clicked = False
                for selector in [
                    "button:has-text('Search')",
                    "button[type='submit']",
                    "[role='button']:has-text('Search')",
                ]:
                    btn = page.locator(selector).first
                    if btn.count() > 0:
                        try:
                            btn.click()
                            clicked = True
                            break
                        except Exception:
                            continue
                if not clicked:
                    log.warning("could not locate Search button on advanced form for %s", self.slug)
                    page.keyboard.press("Enter")

                # Wait for results
                try:
                    page.wait_for_load_state("networkidle", timeout=20_000)
                except PlaywrightTimeout:
                    pass
                page.wait_for_timeout(2000)

                yielded = 0
                while yielded < max_results:
                    new_docs = list(_extract_results_from_page(page))
                    if not new_docs:
                        # Some htmx pages render slowly — give one more try
                        page.wait_for_timeout(1500)
                        new_docs = list(_extract_results_from_page(page))
                    for d in new_docs:
                        yield d
                        yielded += 1
                        if yielded >= max_results:
                            break
                    if yielded >= max_results:
                        break
                    # Try to advance to the next page
                    if not _click_next_page(page):
                        break
                    try:
                        page.wait_for_load_state("networkidle", timeout=10_000)
                    except PlaywrightTimeout:
                        pass
                    page.wait_for_timeout(1000)
            finally:
                browser.close()


# ────────────────────────────────────────────────────────────
# DOM extraction helpers
# ────────────────────────────────────────────────────────────
_HEADER_NORMALIZE = {
    "instrument": "instrument_no",
    "instr no": "instrument_no",
    "instr#": "instrument_no",
    "doc number": "instrument_no",
    "document number": "instrument_no",
    "recorded": "recording_date",
    "recorded date": "recording_date",
    "record date": "recording_date",
    "filing date": "recording_date",
    "filed": "recording_date",
    "instrument type": "instrument_type",
    "doc type": "instrument_type",
    "document type": "instrument_type",
    "type": "instrument_type",
    "grantor": "grantor",
    "grantors": "grantor",
    "grantee": "grantee",
    "grantees": "grantee",
    "book": "book",
    "volume": "book",
    "page": "page",
    "legal": "legal",
    "legal description": "legal",
}


def _extract_results_from_page(page: Page) -> list[PSDocument]:
    """Parse the visible results table on the page."""
    # publicsearch.us renders a results table. The exact selector varies; we
    # try multiple candidates and fall back to anything that looks like a table.
    table_selectors = [
        "table.results-table",
        "[data-test='results-table']",
        "table:has(thead th)",
    ]
    table_locator = None
    for sel in table_selectors:
        loc = page.locator(sel).first
        if loc.count() > 0:
            table_locator = loc
            break

    if table_locator is None:
        # No table found yet
        return []

    # Headers
    header_cells = table_locator.locator("thead th").all()
    columns: list[str] = []
    for cell in header_cells:
        txt = (cell.inner_text() or "").strip().lower()
        # collapse whitespace
        txt = re.sub(r"\s+", " ", txt)
        columns.append(_HEADER_NORMALIZE.get(txt, txt))
    if not columns:
        return []

    # Rows
    rows = table_locator.locator("tbody tr").all()
    docs: list[PSDocument] = []
    for row in rows:
        cells = row.locator("td").all()
        if not cells:
            continue
        row_dict: dict[str, str] = {}
        for col_name, cell in zip(columns, cells):
            try:
                row_dict[col_name] = (cell.inner_text() or "").strip()
            except Exception:
                row_dict[col_name] = ""

        inst = row_dict.get("instrument_no") or None
        rec_date = _parse_date(row_dict.get("recording_date"))
        itype = row_dict.get("instrument_type") or None
        grantor = row_dict.get("grantor") or None
        grantee = row_dict.get("grantee") or None

        if inst is None and rec_date is None and itype is None:
            # likely a "no results" placeholder row
            continue

        docs.append(PSDocument(
            instrument_no=inst,
            recording_date=rec_date,
            instrument_type=itype,
            grantor=grantor,
            grantee=grantee,
            book=row_dict.get("book") or None,
            page=row_dict.get("page") or None,
            legal=row_dict.get("legal") or None,
            raw=row_dict,
        ))
    return docs


def _click_next_page(page: Page) -> bool:
    """Click the pagination 'next' button if present + enabled."""
    for sel in [
        "button[aria-label='Next page']",
        "button:has-text('Next')",
        "a:has-text('Next')",
        "[data-test='pagination-next']",
    ]:
        btn = page.locator(sel).first
        if btn.count() > 0:
            try:
                disabled = btn.get_attribute("aria-disabled")
                if disabled and disabled.lower() == "true":
                    return False
                btn.click()
                return True
            except Exception:
                continue
    return False


# ────────────────────────────────────────────────────────────
# Persistence
# ────────────────────────────────────────────────────────────
def ingest_oil_and_gas_records_for_county(
    county_fips: str,
    *,
    recorded_from: date,
    recorded_to: date,
    max_results: int = 1000,
) -> dict[str, int]:
    """Pull OPR documents for the window; upsert leases + chain_events."""
    counts: dict[str, int] = {"fetched": 0, "lease": 0, "chain_event": 0, "skipped": 0}
    client = PublicSearchClient(county_fips)

    with cursor(dict_rows=False) as cur:
        ing_id = _start_ingestion(cur, f"publicsearch.us:{county_fips}")
        for doc in client.search(
            recorded_from=recorded_from,
            recorded_to=recorded_to,
            max_results=max_results,
        ):
            counts["fetched"] += 1
            canon = doc.canonical_event_type
            if canon is None:
                counts["skipped"] += 1
                continue

            if canon == "lease":
                cur.execute(
                    """
                    INSERT INTO lease (county_fips, opr_instrument_no, recording_date,
                                       lessor_text, lessee_text, parsed_metadata, confidence_score)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (county_fips, opr_instrument_no) DO UPDATE
                      SET recording_date = EXCLUDED.recording_date,
                          lessor_text = EXCLUDED.lessor_text,
                          lessee_text = EXCLUDED.lessee_text
                    """,
                    (
                        county_fips, doc.instrument_no, doc.recording_date,
                        doc.grantor, doc.grantee,
                        json.dumps({
                            "source": "publicsearch.us",
                            "doc_type_raw": doc.instrument_type,
                            "book": doc.book, "page": doc.page, "legal": doc.legal,
                        }),
                        0.80,
                    ),
                )
                counts["lease"] += 1
            else:
                cur.execute(
                    """
                    INSERT INTO chain_event (county_fips, opr_instrument_no, recording_date,
                                             event_type, grantor_text, grantee_text,
                                             parsed_metadata, confidence_score)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (county_fips, opr_instrument_no, event_type, references_lease_id)
                      DO UPDATE
                      SET recording_date = EXCLUDED.recording_date,
                          grantor_text = EXCLUDED.grantor_text,
                          grantee_text = EXCLUDED.grantee_text
                    """,
                    (
                        county_fips, doc.instrument_no, doc.recording_date, canon,
                        doc.grantor, doc.grantee,
                        json.dumps({
                            "source": "publicsearch.us",
                            "doc_type_raw": doc.instrument_type,
                            "book": doc.book, "page": doc.page, "legal": doc.legal,
                        }),
                        0.80,
                    ),
                )
                counts["chain_event"] += 1
        _finish_ingestion(cur, ing_id, counts)
    log.info("ingest complete: %s", counts)
    return counts


def _start_ingestion(cur, source: str) -> int:
    cur.execute(
        "INSERT INTO ingestion_run (source, started_at) VALUES (%s, now()) RETURNING id",
        (source,),
    )
    return int(cur.fetchone()[0])


def _finish_ingestion(cur, ing_id: int, counts: dict) -> None:
    cur.execute(
        """
        UPDATE ingestion_run
           SET finished_at = now(), rows_in = %s, rows_upserted = %s, metadata = %s::jsonb
         WHERE id = %s
        """,
        (
            counts.get("fetched", 0),
            counts.get("lease", 0) + counts.get("chain_event", 0),
            json.dumps(counts),
            ing_id,
        ),
    )


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--county", required=True)
    parser.add_argument("--from", dest="dfrom", default=(date.today() - timedelta(days=365)).isoformat())
    parser.add_argument("--to", dest="dto", default=date.today().isoformat())
    parser.add_argument("--max", type=int, default=200)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = ingest_oil_and_gas_records_for_county(
        args.county,
        recorded_from=date.fromisoformat(args.dfrom),
        recorded_to=date.fromisoformat(args.dto),
        max_results=args.max,
    )
    print(json.dumps(result, indent=2))
