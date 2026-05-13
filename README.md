# FactTrack

**East Texas landwork automation — public-records ingestion + curative triage + landman-grade reports.**

FactTrack ingests Texas Railroad Commission (RRC) data + East-Texas county Official Public Records (OPR) and produces a per-project report covering leasehold risk, curative priority, and project execution. The output is a PDF, an interactive HTML map, and an Excel workbook designed for landman workflows.

---

## What the engine does

For every project file (a set of tracts):

1. **Ingests public records** from TX RRC bulk dumps and county OPR portals on the `publicsearch.us` platform.
2. **Builds the canonical title chain** — leases, assignments, releases, ratifications, top-leases, AOH, probate, ORRI creation/release, pooled units.
3. **Runs curative-detection rules** — surfaces ranked, scored findings (severity + dollar impact + suggested action + assignee level).
4. **Computes lease maintenance** — Pugh-clause acreage release, retained-acreage well misses, continuous-production status, primary-term expiration calendar.
5. **Reconciles ownership** — NRI decimal math against recorded chain.
6. **Renders the landman report** — PDF, Excel workbook, Folium tract map.

## Curative rules — MVP

Each registered rule has a real implementation with no stub or placeholder code.

| # | Rule | Requires |
|---|---|---|
| 1 | Unrecorded RRC P-4 assignment | RRC P-4 history + county OPR chain |
| 2 | Probate gap (deceased lessor w/o AOH) | parsed lease parties (deceased flag) |
| 4 | Depth severance mismatch | parsed depth-limit clause + RRC completion data |
| 5 | Primary term expiring + no continuous production | parsed primary-term + RRC PR data |
| 6 | Pugh-clause acreage release missed | parsed Pugh clause text |
| 12 | Top-lease conflict | county OPR top-lease + lease primary-term data |
| 16 | Mineral / royalty ambiguity in conveyance | parsed lease clause text |
| 17 | ORRI cloud (unreleased > 36 mo post-lease termination) | county OPR ORRI events |

Other rules (stranger-to-title, retained-acreage well miss, NRI mismatch, unratified extension, unit ratification, surface-use dispute, pipeline ROW expiration, mineral classification mismatch) are deferred until the data sources they evaluate against are wired into ingestion.

## Stack

- Python 3.12+, PostgreSQL 16+
- httpx for HTTP, Playwright for dynamic-UI county portals
- scipy for Arps decline curve fitting
- WeasyPrint for PDF, Folium for maps, openpyxl for Excel
- Pydantic for canonical data models
- Tenacity for robust retries

## Layout

```
src/facttrack/
  config/      runtime settings (DB, HTTP, paths, counties)
  db/          PostgreSQL connection pool
  models/      pydantic canonical entities (Tract, Lease, ChainEvent, ...)
  ingest/      RRC bulk + county OPR ingestion modules
  engine/      curative-detection rules + decline / NRI / expiration math
  render/      PDF / Excel / map renderers

sql/
  schema.sql   canonical 15-table schema

  ingest/
    publicsearch.py         OPR index scrape (per-county lease list)
    publicsearch_docs.py    document image fetcher (multi-page, full-res)
    rrc_mft.py              RRC GoAnywhere MFT bulk-data downloader
    rrc_wellbore_parser.py  RRC EWA wellbore CSV parser
    legal_parser.py         legal-description → canonical tract upsert
    party_splitter.py       lessor/lessee text → lease_party rows + deceased flags
  ocr/
    tesseract.py            Tesseract OCR wrapper (with cache)
  engine/
    clause_parser.py        regex-driven clause extractor on OCR text
    rules.py                8 registered curative rules
    run.py                  CLI runner

sql/
  schema.sql   canonical 15-table schema

scripts/
  init_db.sh                  one-shot DB bootstrap
  run_anderson_pipeline.sh    full Anderson E2E (ingest → OCR → engine → report)
```

## Quickstart

```bash
# 1. Create DB + apply schema
bash scripts/init_db.sh

# 2. Install Python deps
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .

# 3. Pull real OPR records for a county (example: Anderson County, TX, FIPS 48001)
PYTHONPATH=src python3 -m facttrack.ingest.publicsearch --county 48001 --from 2024-01-01 --to $(date +%Y-%m-%d) --max 500

# 4. Parse legals → canonical tracts + project
PYTHONPATH=src python3 -m facttrack.ingest.legal_parser --county-fips 48001 --county-name Anderson

# 5. Run engine + render report
PYTHONPATH=src python3 -m facttrack.engine.run --project county_research_48001
PYTHONPATH=src python3 -m facttrack.render.build_report --project county_research_48001
```

## Status

The pipeline runs end-to-end on 100% real Texas public records. No synthetic, mock, or placeholder data is used anywhere in the codebase.

**Anderson County, TX — 1 CRITICAL finding on 14 leases**, after the grantor-side release verifier auto-demoted two prior r05 findings on 1957 Shell leases (Davenport, Chivers) by locating their recorded releases (1968-6829460, 1969-6929994). One CRITICAL `r02 probate_gap` finding remains on the 1958 C.W. Hanks Estate lease (no recorded AOH or probate in Anderson County deed records — independently verified, transcript in `docs/verification/`).

The full pipeline that surfaced this (ingest → multi-page document fetch → grantor-side release verifier → OCR → clause regex → curative engine with §203.001 logic → report renderer) is driven by `scripts/run_anderson_pipeline.sh` and uses 100% real Anderson County OPR records from 1957-1960 plus live publicsearch.us reverse-search.

**Counties on the free `publicsearch.us` platform:** Anderson, Leon, Freestone, Smith, Nacogdoches, Walker. *(Madison County is on Cott/Kofile `uslandrecords.com` and is currently a coverage gap.)*

## Data sources (all public)

- TX Railroad Commission bulk dumps (`mft.rrc.texas.gov`)
- East-Texas county OPR portals (`publicsearch.us` platform)
- TX General Land Office state mineral lease records (planned)

All data sourced from public records. No proprietary or customer data is used.

## License

TBD — contact via GitHub for licensing inquiries.
