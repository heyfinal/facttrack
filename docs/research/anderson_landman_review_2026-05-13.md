# Anderson County — Senior-Landman Verified Analysis

**Prepared:** 2026-05-13 (overnight)
**Reviewer:** 25-yr E-TX operator-side landman (sub-agent), with WebSearch verification
**Scope:** 6 rule-engine critical findings, 44 attributed leases
**Bottom line:** of 6 engine findings, **3 are commercially live** — Hanks (heir-cure), Summers (clean-sheet), Double J (phone-call lease). The other 3 are zombie leases (no live counterparty).

---

## Verified operator-side facts

| Entity | Verified status | Source |
|---|---|---|
| **Double J Oil & Gas Interests LLC** | No RRC operator footprint. Mineral-holding LLC, not an operator. | Negative search across RRC operator listings + Texas-Drilling.com |
| **Oden & Associates LLC** | No RRC operator footprint. Not the same as H.D. Oden Inc (#618278, Midland) or Oden & Wetzel (#618279, Permian). | [Texas-Drilling H.D. Oden](https://www.texas-drilling.com/operators/oden-h-d-inc/618278), [Oden & Wetzel](https://www.texas-drilling.com/operators/oden-wetzel/618279) |
| **Coho Resources Inc** | Operator #166707. Active 1993–Aug 2002. TX counties: Zavala, Lavaca, Colorado, Waller, Navarro. **Never Anderson County.** Filed Chapter 11 Feb 2002. Assets sold 2003 to Citation (OK + Red River TX) and Denbury (MS + Navarro TX). No corporate successor today — entity is dissolved. **NOT a predecessor of Halcón / Battalion** (separate corporate line). | [Texas-Drilling Coho](https://www.texas-drilling.com/operators/coho-resources-inc/166707), [Rigzone asset sale](https://www.rigzone.com/news/oil_gas/a/4011/coho_approves_sale_of_oil_gas_assets/), [Hart Energy Battalion](https://www.hartenergy.com/exclusives/halcon-resources-changes-name-battalion-oil-185567/) |
| **EP Operating Co (#253275)** | Filed permits May 1985 – Feb 1993. 20+ TX counties, none Anderson. No successor in Anderson. **Not EP Energy Corp.** | [Texas-Drilling EP Op Co](https://www.texas-drilling.com/operators/ep-operating-company/253275) |
| **EP Operating LP (#253229)** | Filed permits Jan 1993 – Jul 1995. Henderson/Leon/Freestone in our area but no Anderson. Leases reassigned to Capital Star, Faulconer, Hilcorp, Urban, others. | [Texas-Drilling EP Op LP](https://www.texas-drilling.com/operators/ep-operating-limited-partnership/253229) |
| **Highmark Energy Operating LLC** | Top current Anderson Cty operator (95k bbl / 476k MCF). Mid-tier. Realistic Monument peer/competitor. | [DrillingEdge](https://www.drillingedge.com/texas/anderson-county) |
| **Rose City Resources LLC** | #2 Anderson operator (175k bbl). Tyler-area mid-tier. | [DrillingEdge](https://www.drillingedge.com/texas/anderson-county) |

---

## Top 3 — for the morning artifact

### #1 LEAD — Finding r02 HANKS C W ESTATE OF (1958-58563225)

**Grade: B+ / pursue.** The only finding with real, evidentiary chain-of-title gap AND a live commercial path. 147-line negative-search transcript proves no probate / no AOH / no curative event for Hanks estate in Anderson deed records. Lessee (Pennybacker) is a person, not a dissolved corp — there's a counterparty for ratification negotiation. Underlying acreage is in central Anderson County where Highmark and Rose City are currently producing CV/Pettit.

**Monument play:** heir-hunt → top-lease unleased heir fractions at **$750–$1,200/NMA, 1/5 to 3/16 royalty**. Cost-to-cure dominated by heir hunt (1958 estate = 3–4 generations deep, $2k–$5k record/genealogy work).

**Kill criterion:** if heir search reveals estate was formally probated in another Texas county (Smith, Henderson, Cherokee — common for old Palestine families that moved to Tyler) and lease was ratified post-probate, walk.

### #2 — Finding r02 SUMMERS ALFRED H ESTATE OF (1992-9235335)

**Grade: D+ / probable false positive on lessee side, but clean-sheet leasing opportunity.** Probate gap is real on lessor side. Lessee (Coho) never operated Anderson per Texas-Drilling.com operator history — lease was speculative position that died at primary-term end 1995 with no operator to assert continuation. Summers heirs almost certainly believe they're unleased, and they're right.

**Monument play:** pair Summers heir hunt with Hanks heir hunt (same county courthouse trip). Marginal cost-to-cure approaches $0 as a free-rider on Finding #1.

**Kill criterion:** if Summers estate probated in another county and lease was ratified, OR if tract is off-prospect, walk.

### #3 — Finding r05 DOUBLE J → ODEN & ASSOCIATES (2021-5852)

**Grade: C- / pass unless on-prospect.** Oden & Associates LLC has zero RRC operator footprint — flipper/mineral aggregator who couldn't or didn't assign before primary-term end. Double J is now an unleased mineral owner. Phone-call lease; no curative theater required.

**Monument play:** lease direct from Double J at $750–$1,200/NMA — but only if tract is on prospect map.

**Kill criterion:** more than 3 miles outside any horizontal permit issued in the county in the last 24 months → walk.

---

## Zombie leases (informational only)

These flag deterministically per the rule engine but have no live counterparty. Spend zero curative dollars.

- **#3 — TARBUTTON JEAN D → COHO RESOURCES INC (1991-9133913)** — Coho never operated Anderson. Either OCR garble of CONOCO/CABOT/CARLISLE, or dead speculative lease. Verify deed-image; if confirmed Coho, treat as fresh-lease opportunity to Tarbutton heirs (no operator to negotiate release with).
- **#5 — COLLEY JILL DAVEY → EP OPERATING CO (1992-9235282)** — EP Operating wound down in Texas Feb 1993, before primary even expired. Lease lapsed by its terms 31 yrs ago. No successor entity. Direct-lease to Colley/heirs if on-prospect.
- **#6 — ROGERS ORAL MAUDE → EP OPERATING CO (1992-9234933)** — same as #5.

---

## Methodological corrections to FactTrack

1. **Wrong successor chain in code.** The `SUCCESSOR_CHAINS` mapping in `release_verifier_fuzzy.py` lists "COHO RESOURCES → HALCON RESOURCES, BATTALION OIL" — this is incorrect. Halcón Resources (Floyd Wilson, 2011) is a separate company from Coho Energy. Remove this mapping; the fuzzy verifier will not find successor releases because the corporate succession doesn't exist.
2. **Operator footprint cross-check needed.** Before treating an r05 finding as actionable, the engine should cross-check whether the lessee ever operated the county per RRC operator history. If never, it's a zombie lease — flag as informational, not curative.
3. **Pre-2010 r05 needs a "no live counterparty" demotion path** based on operator dissolution date (from RRC P-5 records or RigZone / Texas-Drilling.com cross-ref).
