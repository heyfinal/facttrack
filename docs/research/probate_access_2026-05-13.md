# East-TX Probate Access Research — 2026-05-13

Research scope: probate-record availability in the 7 counties we currently
support (Anderson + 6 others on publicsearch.us). Motivated by senior-landman
critique that our `r02 probate_gap` rule does not cross-check District-Clerk
probate dockets.

## Headline

**The landman partly overstated the gap.** In all 7 of our target East-Texas
counties, full probate cases are filed with the **County Clerk** (in the
constitutional County Court or County Court at Law), not the District Clerk.
Probate matters only land in District Court when a contested case is
transferred under Estates Code §32.003 — a rare event in rural East Texas.

`publicsearch.us` indexes the County Clerk's deed records, which include:

- Affidavits of Heirship (AOH)
- Wills offered as Muniment of Title
- Probate-deed-of-distribution filings
- Recital-of-Probate (RoP) instruments

`publicsearch.us` does **not** typically index the probate **case docket**
(applications, wills offered for probate, letters testamentary, inventories).
Those live in the County Clerk's probate-case file system.

## Per-county online probate docket availability

| County | Deed (publicsearch.us) | Probate docket online | Notes |
|---|---|---|---|
| Anderson  | yes | auth-walled (pubaccess.co.anderson.tx.us, courthouse-only) | unscrapeable without creds |
| Leon      | yes | none | courthouse-only |
| Freestone | yes | none | courthouse-only |
| Smith     | yes | indexes available, images by request | email Probate Deputy Clerks |
| Nacogdoches | yes | none | courthouse-only |
| Madison   | **no — different vendor** | none | i2i.uslandrecords.com/TX/Madison/D/Default.aspx — **silent coverage hole for FactTrack** |
| Walker    | yes | **YES — free Odyssey** (portal-txwalker.tylertech.cloud) | only one of 7 with structured public probate docket |

## Texas-wide options

- **re:SearchTX** (research.txcourts.gov) — Tyler-operated under OCA contract. Free search tier; full document download requires paid subscription. Small-county probate coverage is uneven — not a reliable substitute.
- **Odyssey** adoption in our 7-county footprint: Walker only.
- No PACER-equivalent for Texas state courts.

## Free no-auth cross-checks

- **Texas Estates Code §203.001** — AOH on file in deed records ≥5 years is **prima facie evidence of heirship**. This is the highest-ROI rule we can add: any AOH our existing publicsearch.us scrape finds that is older than 5 years self-cures the chain without needing a probate. https://texas.public.law/statutes/tex._est._code_section_203.001
- **FamilySearch** — free, has digitized historical Texas probate minutes (Anderson 1846-1934, Madison 1873-1939, Leon from 1846+). Useful for pre-1990 gaps.
- **Find a Grave / SSDI** — free, decent negative signal ("no death record at all = AOH may be premature"). Poor positive precision on common surnames.
- **TexShare newspaper archives** (TX library card required) — obituary cross-check; noisy for full automation.

## v0.2 recommendation

**Do in ≤2 engineering days:**

1. **`_probate_search_attempted` metadata flag** on every `chain_event` (values: `not_attempted` | `publicsearch_deed_only` | `odyssey_walker` | `manual_required`). The report can then truthfully say what we did and didn't check.
2. **Implement `r02b_aoh_5yr_prima_facie` rule** — if an AOH is in deed records ≥5 years and names heirs for the decedent in our `r02_probate_gap`, downgrade the defect to "self-cured per §203.001." Pure SQL/date logic, no new scraping.
3. **Wire Walker Odyssey** as the reference implementation for the rare county that has a free public probate docket. ASP.NET WebForms, similar to what we already scrape.
4. **Flag the Madison silent gap** — Madison isn't on publicsearch.us at all; uses uslandrecords.com (Cott/Kofile). Add a `_madison_county_uslandrecords_TODO` so we don't pitch Madison support we don't have.

**Flag as "manual landman task" (don't build):**

- Anderson `pubaccess` portal — auth-walled, not worth credential-juggling
- Smith probate index — only available by email
- Leon / Freestone / Nacogdoches / Madison probate dockets — courthouse-only; surface in report as a "courthouse pull required" line item

## Investor framing

The landman is right that we have a probate gap — but for our rural East-Texas
footprint, probate isn't in the District Clerk's office. The real gap is the
**probate case docket** (the in-progress filings before final orders get
recorded in deed records). For Hanks specifically, our verification search
already proved no AOH exists in the Anderson deed records, so the §203.001
workaround doesn't apply — the finding stands. For future projects, the
§203.001 rule plus the Walker Odyssey reference implementation will reclassify
a meaningful share of `r02 probate_gap` defects as self-cured.
