// FactTrack — Gold-Strike Cross-County Summary
// Top tracts with critical/high curative items, ranked by Monday-morning value.

#let data = json(sys.inputs.data)

#let ink   = rgb("#0a1929")
#let navy  = rgb("#0a2540")
#let warm  = rgb("#3b4252")
#let mute  = rgb("#6a7280")
#let rule  = rgb("#c9ced6")
#let surf  = rgb("#f5f7fb")
#let paper = rgb("#fdfcfa")
#let crit  = rgb("#b3261e")
#let high  = rgb("#b86e00")
#let ok    = rgb("#2e7d32")
#let gold  = rgb("#7a5d00")

#let display = ("EB Garamond 12", "EB Garamond", "Liberation Serif", "Georgia")
#let body    = ("IBM Plex Sans", "Inter", "Liberation Sans", "DejaVu Sans")
#let mono    = ("IBM Plex Mono", "Liberation Mono", "DejaVu Sans Mono")

#set page(
  paper: "us-letter",
  margin: (top: 0.75in, bottom: 0.75in, left: 0.7in, right: 0.7in),
  fill: paper,
  footer: context {
    set text(7pt, fill: mute, font: body, tracking: 1pt)
    let n = counter(page).get().first()
    if n > 1 [
      #grid(
        columns: (1fr, auto, 1fr),
        align: (left, center, right),
        upper[FactTrack v#data.facttrack_version],
        upper[Gold-Strike Summary],
        upper[#n / #counter(page).final().first()],
      )
    ]
  },
)
#set text(font: body, size: 9pt, fill: ink)
#set par(leading: 0.6em, justify: false)

#let eyebrow(s) = text(font: body, size: 7.5pt, weight: "semibold", fill: mute,
  tracking: 1.6pt)[#upper(s)]

#let pill(label, kind: "outline") = {
  let bg = none; let fg = warm; let bd = rule
  if kind == "critical"        { bg = rgb("#fff5f4"); fg = crit; bd = crit }
  else if kind == "high"       { bg = rgb("#fff8ec"); fg = high; bd = high }
  else if kind == "buy"        { bg = ok;   fg = white; bd = ok }
  else if kind == "negotiate"  { bg = high; fg = white; bd = high }
  else if kind == "walk"       { bg = crit; fg = white; bd = crit }
  box(fill: bg, stroke: 0.7pt + bd, inset: (x: 6pt, y: 2pt), radius: 2pt)[
    #text(font: body, size: 7pt, weight: "bold", tracking: 0.8pt, fill: fg)[
      #upper(label)
    ]
  ]
}

// ═══════════ COVER ═══════════
#text(font: display, size: 10.5pt, weight: "semibold", fill: navy,
      tracking: 5pt)[#upper("FactTrack")]
#v(20pt)
#line(length: 1.2in, stroke: 1pt + navy)
#v(28pt)
#eyebrow("Cross-County Acquisition Triage")
#text(font: display, size: 32pt, weight: "semibold", fill: navy, tracking: -0.6pt)[
  Gold-Strike Summary
]
#v(4pt)
#text(font: display, size: 11.5pt, fill: warm, style: "italic")[
  Tracts with critical or high curative findings, ranked Monday-ready ·
  #data.generated_at
]
#v(24pt)
#line(length: 100%, stroke: 0.5pt + rule)
#v(12pt)

#grid(
  columns: (1fr, 1fr, 1fr, 1fr),
  column-gutter: 12pt,
  ..([
    #block(fill: surf, inset: 12pt, width: 100%)[
      #eyebrow("Critical strikes")
      #v(2pt)
      #text(font: display, size: 28pt, weight: "semibold", fill: crit)[
        #str(data.critical_strikes)
      ]
    ]
  ], [
    #block(fill: surf, inset: 12pt, width: 100%)[
      #eyebrow("High-only strikes")
      #v(2pt)
      #text(font: display, size: 28pt, weight: "semibold", fill: high)[
        #str(data.high_strikes)
      ]
    ]
  ], [
    #block(fill: surf, inset: 12pt, width: 100%)[
      #eyebrow("Total findings")
      #v(2pt)
      #text(font: display, size: 28pt, weight: "semibold", fill: navy)[
        #str(data.total_findings)
      ]
    ]
  ], [
    #block(fill: surf, inset: 12pt, width: 100%)[
      #eyebrow("Counties scanned")
      #v(2pt)
      #text(font: display, size: 28pt, weight: "semibold", fill: navy)[
        #str(data.counties_scanned.len())
      ]
    ]
  ])
)

#v(18pt)
#eyebrow("Counties scanned")
#v(6pt)
#table(
  columns: (auto, 1fr, 60pt, 60pt, 60pt, 60pt),
  inset: 6pt,
  align: (right + top, left + top, right + top, right + top, right + top, right + top),
  stroke: (x, y) => (
    top:    if y == 0 { 0.75pt + navy } else { 0pt },
    bottom: if y == 0 { 0.4pt + rule } else { 0.3pt + rgb("#e5e7eb") },
  ),
  fill: (x, y) => if y == 0 { surf } else { none },
  table.header(
    [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("FIPS")]],
    [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("County")]],
    [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Tracts")]],
    [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Leases")]],
    [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("Critical")]],
    [#text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[#upper("High")]],
  ),
  ..data.counties_scanned.map(c => (
    text(size: 8pt, font: mono)[#c.county_fips],
    text(size: 8.5pt)[#c.county_name],
    text(size: 8pt)[#str(c.tract_count)],
    text(size: 8pt)[#str(c.lease_count)],
    text(size: 8pt, fill: if c.critical_count > 0 { crit } else { ink })[#str(c.critical_count)],
    text(size: 8pt, fill: if c.high_count > 0 { high } else { ink })[#str(c.high_count)],
  )).flatten()
)

#v(12pt)
#text(size: 8pt, fill: warm)[
  Per-tract dossiers below. Each strike links back to the full project deal
  memo in #raw("reports/county_research_<FIPS>/dealmemos/")
  for the complete chain-of-title underlying the recommendation.
]

#pagebreak()

// ═══════════ STRIKES ═══════════
#eyebrow("Ranked strikes")
= Top tracts with curative defects

#if data.strikes.len() == 0 [
  #block(fill: surf, stroke: (left: 2.5pt + navy), inset: 14pt, width: 100%)[
    *No critical or high curative items detected on any scanned county.*
    The deeper-scan window has been processed; either the scrape did not
    surface lease-side recordings (publicsearch.us results were dominated by
    deed releases / mortgage activity), or every chain of title in the
    examined records is clean against the registered rule set.
  ]
] else {
  for s in data.strikes [
    #block(
      breakable: false,
      above: 14pt,
      below: 8pt,
      width: 100%,
    )[
      #line(length: 100%, stroke: 0.4pt + rule)
      #v(6pt)
      #grid(
        columns: (1fr, auto),
        align: (left + top, right + top),
        [
          #text(font: display, size: 14pt, weight: "semibold", fill: navy)[
            #s.tract_label
          ]
          #v(2pt)
          #small[
            #s.county_name County · #s.gross_acres ac ·
            #str(s.lease_count) lease#if s.lease_count != 1 [s] ·
            #str(s.well_count) well#if s.well_count != 1 [s]
            (#str(s.producing_count) producing) ·
            #s.status_label
          ]
        ],
        [
          #if s.recommendation == "BUY" {
            pill("BUY", kind: "buy")
          } else if s.recommendation == "NEGOTIATE" {
            pill("Negotiate", kind: "negotiate")
          } else {
            pill("Walk", kind: "walk")
          }
        ],
      )

      #v(6pt)
      #grid(
        columns: (1fr, 1fr),
        column-gutter: 16pt,
        // Left: findings
        [
          #text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[
            #upper("Findings on tract")
          ]
          #v(3pt)
          #for f in s.findings [
            #grid(
              columns: (55pt, 1fr),
              column-gutter: 8pt,
              pill(f.severity, kind: f.severity),
              text(size: 8.5pt, weight: "semibold")[#f.title],
            )
            #v(2pt)
            #text(size: 7.5pt, font: mono, fill: mute)[#f.rule_id]
            #v(4pt)
          ]
        ],
        // Right: NRI + recommendation
        [
          #text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[
            #upper("NRI if acquired")
          ]
          #v(3pt)
          #text(font: display, size: 18pt, weight: "semibold", fill: navy)[
            #str(calc.round(s.nri_value, digits: 4))
          ]
          #v(4pt)
          #text(size: 7.5pt, weight: "semibold", fill: mute, tracking: 1.1pt)[
            #upper("Recommendation rationale")
          ]
          #v(3pt)
          #for r in s.recommendation_reasons [
            #text(size: 8pt, fill: warm)[· #r]
            #v(1pt)
          ]
          #if s.walkaway_per_ac != none [
            #v(3pt)
            #text(size: 8pt, fill: warm)[
              *Walk-away ceiling:* \$#str(s.walkaway_per_ac)/ac
            ]
          ]
        ],
      )

      #if s.primary_operators.len() > 0 [
        #v(6pt)
        #text(size: 8pt, fill: warm)[
          *Operators on tract:* #s.primary_operators.join(" · ")
        ]
      ]

      #v(4pt)
      #text(size: 7.5pt, fill: mute, style: "italic")[
        Full deal memo at #raw(s.dealmemo_path) — chain of title, lease scans,
        and verification transcripts back the recommendation above.
      ]
    ]
  ]
}

#v(20pt)
#line(length: 100%, stroke: 0.4pt + rule)
#v(8pt)
#text(size: 7.5pt, fill: mute, style: "italic")[
  Recommendations are deterministic from rule-engine output + per-tract
  status. This summary is a Monday-morning triage instrument; underlying
  data and verification transcripts live in the project repositories. Not a
  substitute for a title opinion. — FactTrack v#data.facttrack_version
]
