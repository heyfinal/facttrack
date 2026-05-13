# FactTrack — Build Status

**As of 2026-05-13** — full end-to-end pipeline shipping verified findings against real Anderson County records.

## What ships now (100% real, no placeholder anywhere)

### End-to-end pipeline (Anderson County, TX, fully wired)

```
publicsearch.us OPR scrape           → 14 real 1957-1960 leases
publicsearch.us multi-page doc fetch → 74 full-res PNG page images (~12 MB)
grantor-side release verifier        → 6 RELEASE OF OIL AND GAS LEASE instruments
                                       located + recorded as chain_events
Tesseract 5.5 OCR                    → 74 cached text files (PSM 6)
Regex clause extractor               → primary term, royalty %, Pugh, depth limit,
                                       continuous-development, deceased lessor flags
party splitter (lessor/lessee → lease_party rows + ESTATE-OF deceased flags)
curative engine (8 registered rules; §203.001 AOH-maturity logic on r02)
                                     → 1 CRITICAL finding (post-verification)
report renderer                      → HTML / PDF / Excel / Folium map artifacts
```

Driver: `scripts/run_anderson_pipeline.sh [--force]`

### Verified findings on Anderson

| # | Rule | Lease | Severity | Curative effort |
|---|---|---|---|---|
| 1 | r02 probate_gap | 1958-58563225 (Hanks Estate → Pennybacker) | CRITICAL | 4–8 hr jr · county-clerk probate search · ~$300 rec. |

Two prior r05 findings (1957 Shell leases on the Davenport and Chivers
tracts) were **demoted automatically** by the grantor-side release verifier
after locating their recorded releases:

- Davenport → Shell — release 1968-6829460 recorded 1968-11-26
- Chivers   → Shell — release 1969-6929994 recorded 1969-01-02

Audit trail:
`docs/verification/release_verifier_48001_2026-05-13.txt`
`docs/verification/shell_release_search_2026-05-13.txt`
`docs/verification/hanks_aoh_search_2026-05-13.txt`

These findings came from running the engine against real OCR'd clause data
plus a live grantor-side reverse search — not hand-curated examples. Each
finding cites the specific instrument number, the chain-of-title rationale,
and a verification trail a landman can reproduce instrument-by-instrument.

### Curative effort scoping (per rule)

Replaces fictional dollar-impact bands. Calibrated to East-Texas operator
pricing as of 2026.

| Rule | Effort estimate |
|---|---|
| r01 Unrecorded P-4 assignment | 2–4 hr jr · ~$250 recording |
| r02 Probate gap | 4–8 hr jr · county-clerk probate search · ~$300 rec. |
| r04 Depth severance mismatch | Title-opinion review · ~$2,500 atty |
| r05 Primary-term, no continuous prod | Verify recorded release · 1–2 hr jr |
| r06 Pugh-release missed | 4–6 hr sr · ~$200 rec. |
| r12 Top-lease conflict | Title-opinion review · ~$2,500 atty |
| r16 Mineral/royalty ambiguity | Stipulation of interest · 6–12 hr sr |
| r17 ORRI cloud | Notice of termination · 2–4 hr jr |

### Multi-page lease document fetcher

The publicsearch.us viewer was page-1-only in the first cut. The current fetcher:

- Drives `aria-label='Go To Next Page'` clicks in single-page mode, which loads each
  page at full-resolution (`{img_id}_N.png`, ~250 KB each — clean OCR input).
- Falls back to the `Multi Page View` button (`_N_r-300.png` thumbnails) to discover
  page count for single-page-only docs.
- 60-second timeout + 1 retry per signed URL (CDN-rate-limit tolerant).
- All downloads use the live Playwright context (cookie-locked signed URLs).

Per-field extraction on the 14 Anderson leases after multi-page fetch:

- Royalty fraction: 12 / 14 (86%)
- Primary term: 2 / 14 (14%) — the primary-term clause uses spelled-out years
  ("for a term of ten (10) years") that survive page 1 OCR cleanly only on
  the Shell-template leases; the others are on page 2+ and require either
  more regex variants or LLM-assisted extraction (Phase 2 backlog).
- Pugh clause: 0 / 14 — none of the 1957-58 leases predate standard Pugh
  language (the Pugh clause was popularized post-1970).
- Depth limit: 1 / 14 (Midwest Oil 1500 ft).

Page 2+ holds the primary term, royalty, and Pugh language on a modern lease;
1950s typewritten leases often phrase these in spelled-out English that the
current regex doesn't catch consistently. LLM-assisted extraction is the
Phase 2 path to lift primary-term coverage to 80%+.

## What's still missing for broader findings

The 8 registered rules each need different data shapes. Status now:

| Rule | Data needed | Status |
|---|---|---|
| r01 Unrecorded P-4 assignment | RRC P-4 operator-history | RRC PR/P-4 ingest not yet wired |
| r02 Probate gap | `is_deceased` lessor + no AOH/probate event | **FIRING** (Hanks) |
| r04 Depth severance mismatch | `depth_limit_ft` + RRC well producing depth | extractor working; RRC depth data missing |
| r05 Primary term + no production | `primary_term_end` + grantor-release search OR RRC PR | **VERIFIER WIRED** — auto-demotes when release on file |
| r06 Pugh-clause acreage release missed | `has_pugh_clause` | extractor not finding it on 1957-era leases (modern phrasing only) |
| r12 Top-lease conflict | top-lease chain event w/ underlying ref | no top-leases in Anderson sample |
| r16 Mineral/royalty ambiguity | parsed ambiguity note | not implemented |
| r17 ORRI cloud | orri_creation chain events | none in Anderson sample |

## Phase 2 backlog

- RRC wellbore + P-4 + monthly production ingest from MFT (we have wellbore data
  for 5,435 Anderson + Leon wells from `OG_WELLBORE_EWA_Report.zip`; production
  and P-4 history datasets still need their own ingest paths)
- Per-finding link to the verification trail (Hanks AOH search transcript) inside
  the rendered report — currently filed under `docs/verification/` but not
  hyperlinked from the finding row
- Multi-lease blanket releases — chain_event unique constraint widened so a
  single recorded release that covers N leases can be cited under each one
- LLM-assisted clause extraction for edge cases regex misses (handwritten
  add-ons, modified Pugh language, depth severance shorthand)
- GLO state lease bulk ingest
- 6 additional free TX counties on publicsearch.us are already supported by the
  scraper config (Leon, Freestone, Smith, Nacogdoches, Madison, Walker) — they
  just need a project + tract list to run against
- Walker County free Odyssey probate docket integration (only county of 7 with
  free public docket access — reference implementation for v0.2)
- Madison County is on Cott/Kofile (`uslandrecords.com`), NOT publicsearch.us —
  silent coverage gap currently
- Houston County via paid iDocket subscription — REJECTED (per investor)

## Repo

https://github.com/heyfinal/facttrack — public, MIT-style licensable.
