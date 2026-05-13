# FactTrack — Build Status

**As of 2026-05-13 11:35 CDT** — autonomous build complete to the limit of free public data.

## What ships now (100% real, no placeholder anywhere)

### End-to-end pipeline
- PostgreSQL 18 with the 15-table canonical schema
- Live ingest from `publicsearch.us` for any of 7 free TX county clerk portals
- Legal-description parser that extracts real East-TX survey / abstract / acreage data into canonical tract rows
- Engine with 9 curative-detection rules (every rule has a real implementation — no stubs)
- PDF report (WeasyPrint), Excel workbook (openpyxl), interactive Folium map

### Verified data flow (Anderson County, TX)
- Scraped real `publicsearch.us` Anderson OPR (2024–2026 window)
- 14 real oil & gas leases ingested (Shell Oil, Tidewater Oil, Sabine Royalty, etc.)
- 9 real chain events (assignments / ratifications / pooled unit)
- 13 real tracts canonicalized from legal descriptions
- Engine ran clean — 0 findings (honest result; see "What's missing for findings" below)
- PDF + Excel + map artifacts under `reports/county_research_48001/`

### What's missing for the engine to actually fire findings on real data

The 9 registered rules each need clause-level data that the OPR index alone doesn't carry:

| Rule | What it needs that we don't have yet |
|---|---|
| r01 Unrecorded P-4 assignment | RRC P-4 operator-history ingest (RRC PR/P-4 dumps) |
| r02 Probate gap | parsed `is_deceased` flag on `lease_party` (needs lease PDF text) |
| r04 Depth severance mismatch | parsed `depth_limit_ft` on lease (lease PDF text) |
| r05 Primary term expired + no continuous prod | parsed `primary_term_end` + RRC PR data |
| r06 Pugh-clause acreage release missed | parsed `has_pugh_clause` (lease PDF text) |
| r11 AOH > 10 yr no probate | broader OPR history scrape with AOH-type filter |
| r12 Top-lease conflict | broader OPR history scrape with TOP_LEASE doc type |
| r16 Mineral/royalty ambiguity | parsed clause text on lease |
| r17 ORRI cloud | broader OPR history scrape with ORRI doc types |

All of these unblock with one of three Phase-2 workstreams:

1. **RRC bulk-dump ingest** (free, ~5GB/month) → unlocks r01, r05
2. **Deeper OPR scrape** with doc-type filters + pagination fix → unlocks r11, r12, r17
3. **Lease-image OCR + LLM clause extraction** → unlocks r02, r04, r05, r06, r16

(1) is the highest-leverage and most autonomous next step.
(3) is the highest impact and most time-consuming.

## What I'd flag to daniel BEFORE the pitch

1. The pitch artifact today shows the ingest pipeline + canonical schema + report scaffold on real Anderson data. The OWNER-facing impression is: "you scraped 14 real leases and parsed them — but where are the findings?"
2. To make Monument's owner say "how do we get this on every project file," the demo needs findings. That requires at minimum Phase 2 workstream (1) — RRC ingest — which adds ~3–5 days of build time.
3. Alternative: pitch FactTrack as a **canonical-record-aggregation** tool first (replace landman manual data entry), with curative detection as the upsell tier. That makes today's demo defensible at $300–$500/mo as a data-aggregation play.

## What's left to be "fully complete"

### Phase 2.1 — RRC ingest (PARTIALLY ATTEMPTED, DEFERRED)
The RRC publishes documented bulk-data dumps at `mft.rrc.texas.gov/link/*`
URLs covering wellbore data, P-5 organizations, P-4 history, production
reports (PR), and field-rule data. We verified the data inventory:
https://www.rrc.texas.gov/resource-center/research/data-sets-available-for-download/

Implementation hurdles encountered (and why this is not in the codebase):
1. The `mft.rrc.texas.gov/link/*` URLs are GoAnywhere MFT click-through gates,
   not direct file downloads. Anonymous Playwright navigation through them
   requires session/token handling that we don't yet have.
2. Several core datasets (Statewide Production, Oil/Gas Ledger, P-4 dump)
   are published in **EBCDIC** format — mainframe encoding requiring
   conversion before parsing.
3. The legal RRC scraping rule explicitly forbids automated PDQ access:
   "the Railroad Commission of Texas will end the session for that user."
   So the *only* sanctioned automated path is the MFT bulk downloads.

Per the operator's "no placeholder code" rule, the stub RRC modules
(`rrc.py`, `rrc_bulk.py`) and the generic county-OPR stub (`county_opr.py`)
have been deleted from the codebase. Phase 2.1 work would re-introduce them
as real implementations.

### Phase 2.2 — Deeper OPR scrape + pagination fix
- `publicsearch.us` scraper currently returns only first page (~50 docs).
  The "Next" button selector needs to be re-discovered for the platform's
  pagination control (which changes per county skin).
- Adding document-type filters (AOH-only, ORRI-only, top-lease-only)
  surfaces the chain events that rules 11, 12, 17 evaluate against.

### Phase 2.3 — Lease-image clause extraction
- Download lease PDFs from `publicsearch.us` (image-view endpoint)
- Run OCR (Tesseract for English-print legibility, Textract for scanned)
- LLM-assisted clause extraction: Pugh clause, primary term, depth limit,
  habendum, surface restrictions, mineral/royalty distinctions, depth severance
- Each extracted field has a confidence score; low-confidence fields are
  flagged for landman review rather than auto-acted-on

### Other Phase 2 items
- [ ] GLO state lease bulk ingest (free, downloadable, smaller scope)
- [ ] External LLM code review of the full codebase
- [ ] Pricing strategy decision (post-pilot, NOT autonomous)
- [ ] Pitch package cover letter in daniel's voice (NOT autonomous)
- [ ] iDocket subscription decision for Houston County (NOT autonomous)

## Repo

https://github.com/heyfinal/facttrack — public, MIT-style licensable. The full codebase is committed. README + this STATUS.md tell anyone landing on it what's real and what's deferred.
