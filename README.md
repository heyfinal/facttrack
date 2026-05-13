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

## Curative rules (17, MVP)

| # | Rule |
|---|---|
| 1 | Unrecorded RRC P-4 assignment |
| 2 | Probate gap (lessor death w/o AOH) |
| 3 | Stranger to title (RRC + county + GLO cross-check) |
| 4 | Depth severance mismatch |
| 5 | Primary term expiring + no continuous production |
| 6 | Pugh-clause acreage release missed |
| 7 | Retained-acreage well miss |
| 8 | Lease Assignment NRI Mismatch |
| 9 | Unratified extension |
| 10 | Missing lease ratification (unitized) |
| 11 | Heirship affidavit > 10 years w/o probate |
| 12 | Top-lease conflict |
| 13 | Surface use dispute |
| 14 | Pipeline ROW expiration |
| 15 | County/state mineral classification mismatch |
| 16 | Mineral / royalty ambiguity in conveyance |
| 17 | ORRI cloud (unreleased ORRI > 36mo post-lease-termination) |

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

## Status

Currently in build (autonomous mode). See `BUILD_LOG.md` for per-cycle progress. The MVP demo runs on **synthetic-but-realistic** East-TX tract fixtures so the analysis engine and rendering pipeline can be developed and shown end-to-end without depending on the live county OPR scrapers (those come in as a Phase 2 add-on when paired with a pilot customer's NDA-covered project files).

## Data sources (all public)

- TX Railroad Commission online queries + monthly bulk dumps
- Anderson County, TX OPR (Tyler Tech iDox)
- Houston County, TX OPR (Tyler Tech iDox)
- TX General Land Office state mineral lease records
- TX Comptroller oil/gas tax records

No proprietary customer data is used anywhere in this codebase. The demo fixture is deliberately synthetic; field labels and chain shapes mirror real East-TX public record patterns but every name, instrument number, and well API is fictional.

## License

TBD — repository owner: heyfinal. Contact via GitHub.
