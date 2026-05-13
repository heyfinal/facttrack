// FactTrack — East Texas Landwork Report
// Typst template for magazine-grade PDF rendering.
//
// Data is injected as a single JSON payload at the path passed via
//   --input data=/path/to/payload.json
// Compile with:
//   typst compile report.typ report.pdf --input data=payload.json

#let data = json(sys.inputs.data)

// ─────────────────────────────────────────────────────────────────
// Design tokens — single source of truth
// ─────────────────────────────────────────────────────────────────
#let ink   = rgb("#0a1929")    // body
#let navy  = rgb("#0a2540")    // accent, headings
#let warm  = rgb("#3b4252")    // secondary text
#let mute  = rgb("#6a7280")    // tertiary text
#let rule  = rgb("#c9ced6")    // hairline rules
#let surf  = rgb("#f5f7fb")    // panel background
#let crit  = rgb("#b3261e")    // critical
#let high  = rgb("#b86e00")    // high
#let med   = rgb("#8a6d00")    // medium
#let ok    = rgb("#2e7d32")    // green
#let paper = rgb("#fdfcfa")    // off-white paper

// Faces — EB Garamond is the display serif (optical size 12 for body-display),
// IBM Plex Sans is the humanist sans for body + UI, IBM Plex Mono for code.
#let display = ("EB Garamond 12", "EB Garamond", "Liberation Serif", "Georgia")
#let body    = ("IBM Plex Sans", "Inter", "Liberation Sans", "DejaVu Sans")
#let mono    = ("IBM Plex Mono", "Liberation Mono", "DejaVu Sans Mono")

// ─────────────────────────────────────────────────────────────────
// Page setup — letter, generous margins, footer w/ page numbers
// ─────────────────────────────────────────────────────────────────
#set page(
  paper: "us-letter",
  margin: (top: 0.85in, bottom: 0.85in, left: 0.85in, right: 0.85in),
  fill: paper,
  footer: context {
    set text(7pt, fill: mute, font: body, tracking: 1pt)
    let n = counter(page).get().first()
    if n > 1 [
      #grid(
        columns: (1fr, auto, 1fr),
        align: (left, center, right),
        upper[FactTrack v#data.facttrack_version],
        upper[#data.county_name County · #data.project.project_id],
        upper[#n / #counter(page).final().first()],
      )
    ]
  },
)

#set text(font: body, size: 9.5pt, fill: ink, lang: "en")
#set par(justify: false, leading: 0.7em, first-line-indent: 0pt)

// Headings get the display serif
#show heading: it => {
  set text(font: display, weight: "semibold", fill: navy)
  set par(leading: 0.55em)
  it
}
#show heading.where(level: 1): it => {
  set text(size: 24pt, tracking: -0.4pt)
  block(below: 0.4em, it)
}
#show heading.where(level: 2): it => {
  set text(size: 13pt, tracking: -0.1pt)
  block(above: 1.8em, below: 0.6em)[
    #it
    #v(-0.45em)
    #line(length: 100%, stroke: 0.6pt + navy)
  ]
}
#show heading.where(level: 3): it => {
  set text(size: 10.5pt, weight: "semibold")
  block(above: 1.1em, below: 0.4em, it)
}

#show link: set text(fill: navy)

// ─────────────────────────────────────────────────────────────────
// Small helpers
// ─────────────────────────────────────────────────────────────────
#let eyebrow(s) = block(below: 0.5em)[
  #text(font: body, size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.6pt)[
    #upper(s)
  ]
]

#let small(content) = text(size: 8pt, fill: mute, content)
#let tnum(s) = text(font: body, features: ("tnum",), s)
#let mono-text(s) = text(font: mono, size: 8pt, fill: warm, s)

#let pill(label, kind: "outline") = {
  let bg = none
  let fg = warm
  let bd = rule
  if kind == "critical"  { bg = rgb("#fff5f4"); fg = crit; bd = crit }
  else if kind == "high"     { bg = rgb("#fff8ec"); fg = high; bd = high }
  else if kind == "medium"   { bg = rgb("#fffbe6"); fg = med;  bd = rgb("#b9941b") }
  else if kind == "low"      { bg = rgb("#f3f9f3"); fg = ok;   bd = ok }
  else if kind == "solid-red"   { bg = crit; fg = white; bd = crit }
  else if kind == "solid-amber" { bg = high; fg = white; bd = high }
  else if kind == "solid-green" { bg = ok;   fg = white; bd = ok }
  else if kind == "outline-grey" { bg = white; fg = mute; bd = rule }
  else if kind == "navy"     { bg = navy; fg = white; bd = navy }
  box(
    fill: bg,
    stroke: 0.75pt + bd,
    inset: (x: 6pt, y: 2pt),
    radius: 2pt,
  )[
    #text(font: body, size: 7pt, weight: "bold", tracking: 0.8pt, fill: fg)[
      #upper(label)
    ]
  ]
}

// ─────────────────────────────────────────────────────────────────
// COVER PAGE
// ─────────────────────────────────────────────────────────────────
#let cover-color = (
  "red":    crit,
  "yellow": high,
  "green":  ok,
)

#page(
  margin: (top: 0.7in, bottom: 0.7in, left: 0.7in, right: 0.7in),
  fill: paper,
)[
  // Wordmark
  #text(font: display, size: 11pt, weight: "semibold", fill: navy, tracking: 5pt)[
    #upper("FactTrack")
  ]
  #v(28pt)
  #line(length: 1in, stroke: 1pt + navy)
  #v(36pt)

  #eyebrow("East Texas Landwork Report")
  #text(font: display, size: 36pt, weight: "semibold", fill: navy, tracking: -0.8pt)[
    #data.project.label
  ]
  #v(8pt)
  #text(font: display, size: 11.5pt, fill: warm, style: "italic")[
    Curative triage and chain-of-title examination · #data.generated_at
  ]

  #v(36pt)
  #line(length: 100%, stroke: 0.5pt + rule)
  #v(12pt)

  // Project metadata — two-column dl. Label column is generous (1.8in) so the
  // long labels ("EXAMINATION PERIOD", "VERIFIED RELEASES") don't wrap or
  // bleed into the value column.
  #grid(
    columns: (1.8in, 1fr),
    row-gutter: 9pt,
    column-gutter: 12pt,
    eyebrow("Project"),     tnum(data.project.project_id),
    eyebrow("County"),      [#data.county_name, Texas #h(4pt) #small[(FIPS #data.county_fips)]],
    eyebrow("Tracts"),      tnum(str(data.project.tracts.len())),
    eyebrow("Leases"),
      [
        #tnum(str(data.project.leases_total))
        #if data.unattributed_leases.len() > 0 {
          h(6pt)
          small[(#data.project.leases_attributed attributed to tracts, #data.unattributed_leases.len() unattributed)]
        }
      ],
    eyebrow("Wells indexed"),
      [#tnum(data.project.wells_formatted) #h(6pt) #small[(RRC EWA wellbore inventory only — no production data)]],
    eyebrow("Examination period"), tnum(data.examination_period),
    eyebrow("Verified releases"),
      [#tnum(str(data.verified_release_count)) #h(6pt) #small[(grantor-side reverse-search; see Section III)]],
  )
  #v(12pt)
  #line(length: 100%, stroke: 0.5pt + rule)

  // Status block — colored left rule
  #v(28pt)
  #block(
    stroke: (left: 3pt + cover-color.at(data.overall_badge_color)),
    inset: (left: 14pt, top: 12pt, bottom: 12pt, right: 14pt),
  )[
    #eyebrow("Project status")
    #v(2pt)
    #text(font: display, size: 14pt, fill: navy)[
      #data.overall_badge_text
    ]
  ]

  // Hero banner — Anderson County hint (TODO: replace with a real tract-map image)
  #v(1fr)

  #block(
    width: 100%,
    fill: navy,
    inset: 18pt,
  )[
    #text(font: body, size: 9pt, fill: white)[
      *AUDIT TRAIL.* Every finding in this report is supported by a search
      transcript filed under #raw("docs/verification/") in this project's
      repository. Verification queries against publicsearch.us are reproducible
      instrument-by-instrument.
    ]
  ]

  #v(10pt)
  #text(font: body, size: 7.5pt, fill: mute, tracking: 1.2pt)[
    #upper("Prepared by FactTrack v" + data.facttrack_version)
  ]
  #v(6pt)
  #small[
    All findings sourced from public records of #data.county_name County,
    Texas and the Railroad Commission of Texas. No proprietary or customer
    data is included. This examination is a curative-triage instrument
    intended for use by a licensed Texas landman or attorney; it is not a
    substitute for a title opinion.
  ]
]

// ─────────────────────────────────────────────────────────────────
// SECTION I — Findings
// ─────────────────────────────────────────────────────────────────
#eyebrow("Section I")
= Findings & Curative Priority

#text(font: display, size: 11pt, fill: warm, style: "italic")[
  Ranked by severity and confidence. Each finding cites the specific instrument
  and the chain-of-title rationale.
]

#v(8pt)

#let lead-body = if data.findings.len() == 0 [
  *No curative items detected.* Public records reconcile cleanly against the
  registered rule set. Periodic re-examination is recommended; the rule
  registry continues to evolve.
] else [
  #data.summary.body
]

#block(
  fill: surf,
  inset: 14pt,
  stroke: (left: 2.5pt + navy),
  width: 100%,
)[
  #text(font: display, size: 11pt, fill: ink)[#lead-body]
]

#v(12pt)

#if data.findings.len() > 0 {
  table(
    columns: (22pt, 56pt, 1fr, 105pt, 70pt),
    inset: 8pt,
    align: (right + top, center + top, left + top, left + top, left + top),
    stroke: (x, y) => (
      top:    if y == 0 { 0.75pt + navy } else { 0pt },
      bottom: if y == 0 { 0.4pt + rule } else { 0.4pt + rgb("#e5e7eb") },
    ),
    fill: (x, y) => if y == 0 { surf } else { none },
    table.header(
      [#text(font: body, size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("#")]],
      [#text(font: body, size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Severity")]],
      [#text(font: body, size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Finding")]],
      [#text(font: body, size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Curative effort")]],
      [#text(font: body, size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Assignee")]],
    ),
    ..data.findings.enumerate().map(((i, f)) => (
      tnum(text(fill: mute, size: 8pt)[#{ if (i + 1) < 10 [0#(i + 1)] else [#(i + 1)] }]),
      pill(f.severity, kind: f.severity),
      [
        #text(weight: "semibold", fill: navy)[#f.title]
        #v(3pt)
        #text(size: 8.5pt, fill: warm)[#f.description]
        #v(4pt)
        #text(size: 8.5pt, fill: mute, style: "italic")[→ #f.suggested_action]
        #v(4pt)
        #mono-text(f.rule_id)
      ],
      tnum(text(size: 8.5pt)[#f.effort_display]),
      text(size: 8.5pt)[#f.assignee_display],
    )).flatten()
  )
}

#pagebreak()

// ─────────────────────────────────────────────────────────────────
// SECTION II — Lease Maintenance Calendar
// ─────────────────────────────────────────────────────────────────
#eyebrow("Section II")
= Lease Maintenance Calendar

#text(font: display, size: 11pt, fill: warm, style: "italic")[
  Leases by primary-term horizon. Historical leases (term expired more than five
  years ago) are shown for context; verify release on file before acting on them.
]

#v(10pt)

#if data.lease_calendar.len() == 0 {
  block(
    fill: surf,
    inset: 14pt,
    stroke: (left: 2.5pt + navy),
    width: 100%,
  )[
    No leases in this project have a parsed primary-term end date.
    Clause-extraction coverage on this project: primary term
    #data.clause_coverage.primary_term%, royalty #data.clause_coverage.royalty%,
    Pugh #data.clause_coverage.pugh%, depth limit
    #data.clause_coverage.depth_limit%. Leases without an extracted term are
    omitted from this calendar rather than displayed as unknown.
  ]
} else {
  table(
    columns: (145pt, 65pt, 65pt, 60pt, 80pt, 1fr),
    inset: 7pt,
    align: (left + top, right + top, right + top, right + top, left + top, left + top),
    stroke: (x, y) => (
      top:    if y == 0 { 0.75pt + navy } else { 0pt },
      bottom: if y == 0 { 0.4pt + rule } else { 0.4pt + rgb("#e5e7eb") },
    ),
    fill: (x, y) => if y == 0 { surf } else { none },
    table.header(
      [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Lease (lessor → lessee)")]],
      [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Recorded")]],
      [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Term ends")]],
      [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Royalty")]],
      [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Pugh / retained")]],
      [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Status")]],
    ),
    ..data.lease_calendar.map(row => (
      [
        #text(weight: "semibold", fill: navy)[#row.instrument]
        #v(2pt)
        #small[#row.parties]
      ],
      tnum(text(size: 8.5pt)[#row.recording_date]),
      tnum(text(size: 8.5pt)[#row.term_end]),
      tnum(text(size: 8.5pt)[#row.royalty_display]),
      text(size: 8.5pt)[#row.pugh_status],
      [
        #pill(row.risk_label, kind: row.risk_pill)
        #if row.risk_note != "" [
          #v(3pt)
          #small[#row.risk_note]
        ]
      ],
    )).flatten()
  )
}

#pagebreak()

// ─────────────────────────────────────────────────────────────────
// SECTION III — Chain of Title
// ─────────────────────────────────────────────────────────────────
#eyebrow("Section III")
= Chain of Title

#text(font: display, size: 11pt, fill: warm, style: "italic")[
  Chronological chain for each tract in scope. Only instruments directly linked
  to the tract's leases are shown; broader county-wide index entries are
  catalogued in Section V (Methodology).
]

#v(12pt)

#let event-icon(kind) = {
  if kind == "Lease"        { box(width: 8pt, height: 8pt, radius: 4pt, fill: navy) }
  else if kind == "Release" { box(width: 8pt, height: 8pt, radius: 4pt, fill: ok) }
  else if kind == "Top Lease" { box(width: 8pt, height: 8pt, radius: 4pt, fill: high) }
  else if kind == "Aoh" or kind == "Probate" or kind == "Rop" {
    box(width: 8pt, height: 8pt, radius: 4pt, fill: med)
  }
  else { box(width: 8pt, height: 8pt, radius: 4pt, stroke: 1pt + mute) }
}

// Unattributed leases block
#if data.unattributed_leases.len() > 0 [
  #block(
    breakable: false,
    above: 12pt, below: 8pt,
  )[
    #line(length: 100%, stroke: 0.4pt + rule)
    #v(4pt)
    #text(font: display, size: 11pt, weight: "semibold", fill: navy)[
      Unattributed leases
    ]
    #v(2pt)
    #small[
      #data.unattributed_leases.len() lease#if data.unattributed_leases.len() != 1 [s]
      whose legal description could not be mapped to a survey/abstract in this
      run (back-references to prior recordings, non-standard format, or
      OCR-noisy text). Surfaced here so they are not silently dropped.
    ]
    #v(8pt)
    #table(
      columns: (65pt, 80pt, 1fr, 1.5fr),
      inset: 6pt,
      stroke: (x, y) => (
        top:    if y == 0 { 0.5pt + navy } else { 0pt },
        bottom: if y == 0 { 0.4pt + rule } else { 0.4pt + rgb("#e5e7eb") },
      ),
      fill: (x, y) => if y == 0 { surf } else { none },
      table.header(
        [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Recorded")]],
        [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Instrument")]],
        [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Lessor → Lessee")]],
        [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Legal as recorded")]],
      ),
      ..data.unattributed_leases.map(u => (
        tnum(text(size: 8.5pt)[#u.recording_date]),
        tnum(text(size: 8.5pt)[#u.opr_instrument_no]),
        text(size: 8.5pt)[#u.lessor_text → #u.lessee_text],
        mono-text(u.legal_raw),
      )).flatten()
    )
  ]
]

#for tract in data.tracts [
  #block(
    breakable: false,
    above: 14pt,
    below: 6pt,
  )[
    #line(length: 100%, stroke: 0.4pt + rule)
    #v(4pt)
    #text(font: display, size: 11pt, weight: "semibold", fill: navy)[
      #tract.label
    ]
    #v(2pt)
    #small[
      #tract.chain_entries.len() linked instrument#if tract.chain_entries.len() != 1 [s] on file
    ]
    #v(6pt)

    #if tract.chain_entries.len() == 0 [
      #small(style: "italic")[
        No chain instruments linked to this tract in the current scrape window.
      ]
    ] else [
      #table(
        columns: (12pt, 65pt, 85pt, 70pt, 1fr),
        inset: 5pt,
        align: (center + horizon, left + top, left + top, left + top, left + top),
        stroke: (x, y) => (
          top:    if y == 0 { 0.5pt + navy } else { 0pt },
          bottom: 0.3pt + rgb("#e5e7eb"),
        ),
        fill: (x, y) => if y == 0 { surf } else { none },
        table.header(
          [], // icon column header empty
          [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Recorded")]],
          [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Instrument")]],
          [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Type")]],
          [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Parties (grantor → grantee)")]],
        ),
        ..tract.chain_entries.map(e => (
          event-icon(e.kind),
          tnum(text(size: 8.5pt)[#e.date]),
          tnum(text(size: 8.5pt)[#e.instrument]),
          text(size: 8.5pt)[#e.kind],
          text(size: 8.5pt)[#e.parties],
        )).flatten()
      )
    ]
  ]
]

#pagebreak()

// ─────────────────────────────────────────────────────────────────
// SECTION IV — Tract Acquisition Status
// ─────────────────────────────────────────────────────────────────
#eyebrow("Section IV")
= Tract Acquisition Status

#text(font: display, size: 11pt, fill: warm, style: "italic")[
  Per-tract readiness for acquisition or development commitment.
]

#v(10pt)

#table(
  columns: (1fr, 90pt, 60pt, 60pt, 130pt),
  inset: 7pt,
  align: (left + top, center + horizon, right + horizon, right + horizon, left + horizon),
  stroke: (x, y) => (
    top:    if y == 0 { 0.75pt + navy } else { 0pt },
    bottom: if y == 0 { 0.4pt + rule } else { 0.4pt + rgb("#e5e7eb") },
  ),
  fill: (x, y) => if y == 0 { surf } else { none },
  table.header(
    [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Tract")]],
    [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Status")]],
    [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Open items")]],
    [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Critical")]],
    [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Royalty (extracted)")]],
  ),
  ..data.acquisition_status.map(row => (
    text(size: 8.5pt)[#row.label],
    pill(row.badge_text, kind: row.badge_color),
    tnum(text(size: 8.5pt)[#str(row.open_count)]),
    tnum(text(size: 8.5pt)[#str(row.critical_count)]),
    tnum(text(size: 8.5pt)[#row.nri_summary]),
  )).flatten()
)

#v(14pt)

#block(
  fill: surf,
  inset: 14pt,
  stroke: (left: 2.5pt + navy),
  width: 100%,
)[
  *Reading this table.* _Insufficient data_ is shown when no clause-level
  information has been extracted for any lease on the tract — the index scrape
  alone cannot conclude an acquisition is ready. _Critical_ indicates one or
  more curative findings of severity = critical; _Blocked_ indicates
  high-severity findings only.
]

#pagebreak()

// ─────────────────────────────────────────────────────────────────
// SECTION V — Methodology & Scope
// ─────────────────────────────────────────────────────────────────
#eyebrow("Section V")
= Methodology & Scope of Examination

#text(font: display, size: 11pt, fill: warm, style: "italic")[
  Per Texas land-services practice, scope and exceptions are stated here in
  lieu of a separate certification page.
]

#v(12pt)

=== Examination scope
#v(4pt)
#grid(
  columns: (1.4in, 1fr),
  row-gutter: 8pt,
  eyebrow("Records examined"),
  [
    #data.county_name County Official Public Records as indexed on
    publicsearch.us (lease, assignment, release, AOH, probate index entries);
    Texas Railroad Commission EWA wellbore inventory (#data.rrc_pulled_at).
    RRC monthly production (PR) and P-4 operator-history data are out of
    scope for this examination — see exclusions below.
  ],

  eyebrow("Period"),
  tnum(data.examination_period),

  eyebrow("Examiner"),
  [FactTrack rule engine, version #data.facttrack_version (#str(data.rules_total) registered rules)],

  eyebrow("Clause extraction"),
  [
    Tesseract OCR (PSM 6) over signed publicsearch.us page images; regex
    extraction of primary term, royalty fraction, Pugh-clause language, depth
    limit, continuous-development language, and deceased-lessor markers.
    Per-field extraction on the #str(data.clause_coverage.total_leases)
    examined leases:
    primary term #data.clause_coverage.primary_term%,
    royalty #data.clause_coverage.royalty%,
    Pugh clause #data.clause_coverage.pugh%,
    depth limit #data.clause_coverage.depth_limit%.
  ],

  eyebrow("Verification"),
  [
    Grantor-side reverse-search against publicsearch.us for every lease in
    scope. #str(data.verified_release_count) recorded releases located and
    persisted to the chain of title; r05 primary-term findings auto-demote
    when an offsetting release is located. Search transcripts filed under
    #raw("docs/verification/") for instrument-level audit.
  ],
)

#v(14pt)
=== Records expressly excluded from this examination
#v(4pt)
#list(
  marker: text(fill: navy)[•],
  spacing: 6pt,
  [
    *County Clerk probate dockets.* Probate cases in the seven East-Texas
    counties currently supported by FactTrack are filed in the constitutional
    County Court and held by the County Clerk (the same office that runs the
    deed records); only contested cases are transferred to the District Court
    under Texas Estates Code §32.003. The probate _case docket_ — applications,
    wills offered for probate, letters testamentary, inventories — is not
    indexed on publicsearch.us. Documents that touch deed records (AOH,
    will-as-Muniment-of-Title, probate-deed-of-distribution) are indexed and
    are picked up by this examination.
  ],
  [
    *County Appraisal District (CAD) records.* Current mineral- and
    surface-owner identification has not been cross-referenced.
  ],
  [
    *Texas RRC monthly production (PR) reports and W-2 completion records.*
    Primary-term + continuous-production findings (rule r05) rely on absence
    of recorded release; an offsetting release elsewhere in the chain should
    be confirmed before treating the finding as actionable.
  ],
  [
    *Texas General Land Office state-leased mineral records.* Tracts with GLO
    interests are not currently surfaced.
  ],
  [
    *Recorded division orders and JOAs.* NRI reconciliation is presented as
    best-available; division-order accuracy is not represented.
  ],
)

#v(14pt)
=== Data sources actually populated in this run
#v(4pt)
#list(
  marker: text(fill: navy)[•],
  spacing: 4pt,
  ..data.data_sources.map(s => [
    *#s.name* — #s.description (last pulled #s.pulled_at).
  ])
)

#v(14pt)
=== Statutory references applied
#v(4pt)
#list(
  marker: text(fill: navy)[•],
  spacing: 6pt,
  [
    *Texas Estates Code §203.001.* An Affidavit of Heirship on file in deed
    records for five or more years is prima facie evidence of heirship.
    #mono-text("r02_probate_gap") downgrades to medium severity when an AOH
    is on file but younger than five years, and does not fire when an AOH
    ≥ five years old or any probate / RoP instrument names the same decedent.
  ],
  [
    *Habendum-clause defeasance.* #mono-text("r05_primary_term_no_continuous_prod")
    does not fire when a grantor-side reverse-search has located a recorded
    release of the lease by the lessee; the lease died honestly and the chain
    is clean.
  ],
)

#v(14pt)
=== Standards & limitations
#v(4pt)
#text(size: 8.5pt, fill: warm)[
  Findings are generated by a deterministic rule engine evaluating real
  public-record data. Each finding has a confidence score reflecting
  data-quality assumptions; values below 0.50 are flagged for manual review.
  Curative-effort estimates are based on East-Texas operator pricing as of
  #data.generated_at and are guidance only — actual cost depends on document
  discovery, recording fees, and any required attorney involvement. This
  report is a triage instrument and does not constitute a title opinion under
  the Texas State Bar's Title Examination Standards. A licensed Texas attorney
  should review any curative instrument prior to recording.
]
