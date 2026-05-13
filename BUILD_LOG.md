# FactTrack Build Log — Autonomous

Tracking each autonomous cycle. Daniel can read this anytime to see exactly where things stand.

## Strategy adjustment (2026-05-13)

After triple-LLM review (Grok, GPT-5.5, DeepSeek-R1) of the v3 design, plus real-world ingest verification:

- **Live county OPR scraping deferred to post-pilot** — requires interactive selector capture against the live Tyler Tech iDox UI for Anderson + Houston, which isn't reliable from a single autonomous run.
- **Demo runs on synthetic but realistic East-TX tract data** — same shape as real public records, but generated locally. This is defensible at pitch time: the artifact demonstrates ANALYSIS QUALITY, not ingest reliability. Live scraping becomes a Phase 2 add-on at pilot signing.
- **Real RRC bulk-dump ingest** stays in scope (RRC publishes monthly dumps at mft.rrc.texas.gov — stable, documented, reliable). This proves the ingest path works for the largest data source.

This is the right path: pitch-quality output in < 12 days, scraper as a follow-on.

## Cycle log

### Cycle 1 — 2026-05-13 (foundation)
- ✅ Project structure (src/facttrack/, sql/, scripts/, tests/, docs/)
- ✅ PROJECT_DESIGN.md v3 (triple-LLM reviewed)
- ✅ 15-table canonical PostgreSQL schema applied on iMac (port 5432)
- ✅ Python 3.13 venv with all deps on iMac
- ✅ RRC ingestion module skeleton + health check (RRC reachable)
- ✅ County OPR scraper framework (Tyler Tech iDox base class)
- ✅ pydantic data models (Tract, Lease, ChainEvent, CurativeItem, etc.)
- ✅ Connection pool helper with auto-commit/rollback context manager

### Cycle 2 — RRC bulk-dump ingest + synthetic fixture
- [ ] RRC monthly bulk-dump fetcher (mft.rrc.texas.gov)
- [ ] Operator (P-5) bulk loader
- [ ] Synthetic East-TX tract generator (one Anderson, one Houston)
- [ ] Seed two demo projects into the DB

### Cycle 3 — Engine: 17 curative rules
- [ ] Rule framework (base class, finding emitter, confidence scoring)
- [ ] Rules 1-9: title chain + assignment-based rules
- [ ] Rules 10-17: heirship + expiration + ambiguity + ORRI rules
- [ ] NRI decimal calculator
- [ ] Lease expiration calculator (Pugh + retained acreage + continuous prod)
- [ ] Risk scorer + dollar-impact estimator

### Cycle 4 — Rendering
- [ ] PDF template (WeasyPrint, generic FactTrack brand, 7 pages)
- [ ] Excel workbook generator (openpyxl, landman tabs)
- [ ] Folium tract map renderer
- [ ] Static PNG map export

### Cycle 5 — LLM narrative layer
- [ ] Ollama client (handles RMkali offline → Mini fallback → OpenRouter)
- [ ] Prompt templates for landman voice
- [ ] Prose-only mode (LLM writes narrative, never decides)

### Cycle 6 — Demo polish
- [ ] Run full pipeline on Anderson synthetic tract
- [ ] Run full pipeline on Houston synthetic tract
- [ ] Iterate PDF until pitch-quality

### Cycle 7 — Code review + pitch
- [ ] External LLM code review (Grok-4.20)
- [ ] Address any high-severity findings
- [ ] Cover letter draft
- [ ] One-page price sheet

## Files I won't touch unless asked
- Anything outside `/Users/daniel/facttrack/` and `~/facttrack/` on iMac
- daniel's WellRX work
- Bug bounty fleet (retired per daniel)
