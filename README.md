# FactTrack

**East Texas landwork automation — public-records ingestion + curative triage + landman-grade reports.**

FactTrack ingests Texas Railroad Commission (RRC) data + East-Texas county Official Public Records (OPR) and produces a monthly **Leasehold Risk + Curative Priority + Project Execution** report per project file. The output is a PDF, an interactive HTML map, and an Excel workbook designed for landman workflows.

The product targets small East-Texas land services shops doing acquisitions, title & curative, GIS, and project management — the workflows where 5–20 landman-hours per project file are still done by hand against public records.

---

## What the engine does

For every project file (a set of tracts):

1. **Ingests public records** — TX RRC (PR / P-4 / P-5 / W-1), county OPR (Tyler Tech iDox), TX GLO state lease records, TX Comptroller oil/gas tax records.
2. **Builds the canonical title chain** — leases, assignments, releases, ratifications, top-leases, AOH, probate, ORRI creation/release, pooled units.
3. **Runs 17 curative-detection rules** — surfaces ranked, scored findings (severity + dollar impact + suggested action + assignee level).
4. **Computes lease maintenance** — Pugh-clause acreage release, retained-acreage well misses, continuous-production status, primary-term expiration calendar.
5. **Reconciles ownership** — NRI decimal math against recorded chain.
6. **Renders the landman report** — generic-branded PDF (7 pages), Excel workbook (5 tabs), Folium tract map.

## Curative rules — MVP (9 implemented; 8 deferred to Phase 2)

The 9 rules registered in the engine produce real findings without any
placeholder logic. Rules that require data sources still being wired
(operator pay-deck NRI, surface deed records, GLO state cross-reference)
are NOT in the registry yet — they're tracked separately as Phase-2 work.

**Live in the registry:**

| # | Rule | Requires |
|---|---|---|
| 1 | Unrecorded RRC P-4 assignment | RRC P-4 history + county OPR chain |
| 2 | Probate gap (deceased lessor w/o AOH) | parsed lease parties (deceased flag) |
| 4 | Depth severance mismatch | parsed depth-limit clause + RRC completion data |
| 5 | Primary term expiring + no continuous production | parsed primary-term + RRC PR data |
| 6 | Pugh-clause acreage release missed | parsed Pugh clause text |
| 11 | Heirship affidavit > 10 years w/o probate | county OPR AOH events |
| 12 | Top-lease conflict | county OPR top-lease + lease primary-term data |
| 16 | Mineral / royalty ambiguity in conveyance | parsed lease clause text |
| 17 | ORRI cloud (unreleased > 36 mo post-lease termination) | county OPR ORRI events |

**Phase-2 (NOT in the registry — would require placeholder code today):**
r03 stranger-to-title, r07 retained-acreage well miss, r08 lease-assignment NRI mismatch,
r09 unratified extension, r10 missing unit ratification, r13 surface use dispute,
r14 pipeline ROW expiration, r15 county/state mineral classification mismatch.

## Stack

- Python 3.12+, PostgreSQL 16+
- httpx for HTTP, Playwright for dynamic-UI county portals
- scipy for Arps decline curve fitting
- WeasyPrint for PDF, Folium for maps, openpyxl for Excel
- Pydantic for canonical data models
- Tenacity for robust retries
- Optional: local Ollama for narrative prose; OpenRouter fallback

## Layout

```
src/facttrack/
  config/      runtime settings (DB, HTTP, LLM, paths, counties)
  db/          PostgreSQL connection pool
  models/      pydantic canonical entities (Tract, Lease, ChainEvent, ...)
  ingest/      RRC + county OPR + synthetic fixtures
  engine/      curative-detection rules + decline / NRI / expiration math
  render/      PDF / Excel / map renderers
  llm/         narrative writer (prose-only)

sql/
  schema.sql   canonical 15-table schema

scripts/
  init_db.sh   one-shot DB bootstrap

docs/
  PROJECT_DESIGN.md  full design (triple-LLM reviewed: Grok-4.20 + GPT-5.5 + DeepSeek-R1)
  BUILD_LOG.md       autonomous cycle log
```

## Quickstart

```bash
# 1. Create DB + apply schema
bash scripts/init_db.sh

# 2. Install Python deps
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .

# 3. Seed the synthetic East-TX demo (Anderson + Houston tracts)
PYTHONPATH=src python3 -m facttrack.ingest.synthetic

# 4. Run the analysis engine on a demo project (after Phase 2 lands)
PYTHONPATH=src python3 -m facttrack.engine.run --project demo_anderson_001

# 5. Render the report (after Phase 3 lands)
PYTHONPATH=src python3 -m facttrack.render.report --project demo_anderson_001
```

## Status — real data, no fixtures

The pipeline now runs end-to-end on 100% real Texas public records. No
synthetic, demo, mock, or placeholder data lives anywhere in the codebase or
database.

**Verified working on real data:**
- Anderson County OPR ingest via Playwright scraper (live `publicsearch.us` portal)
- Legal-description parser extracts real survey/abstract/acreage from each lease
- Engine runs the 9 registered rules against the real entities

**Honest current finding rate:** 0 findings on the recent Anderson County
2-year window. The rules need clause-level data (Pugh, primary term, depth
limits) that the OPR index doesn't expose — only the underlying lease PDFs do.
Extracting clauses from scanned PDFs (OCR + LLM-assisted text parsing) is the
Phase-2 workstream that unlocks the rules' full firing rate.

**Counties currently on the free `publicsearch.us` platform:**
Anderson, Leon, Freestone, Smith, Nacogdoches, Madison, Walker.

**Houston County** (originally a pilot target) uses iDocket subscription —
deferred to Phase 2 once a paid integration is justified by pilot revenue.

## Data sources (all public)

- TX Railroad Commission online queries + monthly bulk dumps
- Anderson County, TX OPR (Tyler Tech iDox)
- Houston County, TX OPR (Tyler Tech iDox)
- TX General Land Office state mineral lease records
- TX Comptroller oil/gas tax records

No proprietary customer data is used anywhere in this codebase. The demo fixture is deliberately synthetic; field labels and chain shapes mirror real East-TX public record patterns but every name, instrument number, and well API is fictional.

## License

TBD — repository owner: heyfinal. Contact via GitHub.
