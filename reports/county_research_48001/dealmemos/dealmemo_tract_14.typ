// FactTrack — Acquisition Deal Memo (1-page)
// Per the senior-landman spec: header bar / current status / ownership & NRI
// / curative scorecard / competitive landscape / recommendation.
//
// Compile with:
//   typst compile dealmemo.typ output.pdf --input data=payload.json

#let data = json(sys.inputs.data)

// Design tokens (mirror main report)
#let ink   = rgb("#0a1929")
#let navy  = rgb("#0a2540")
#let warm  = rgb("#3b4252")
#let mute  = rgb("#6a7280")
#let rule  = rgb("#c9ced6")
#let surf  = rgb("#f5f7fb")
#let paper = rgb("#fdfcfa")
#let crit  = rgb("#b3261e")
#let high  = rgb("#b86e00")
#let med   = rgb("#8a6d00")
#let ok    = rgb("#2e7d32")

#let display = ("EB Garamond 12", "EB Garamond", "Liberation Serif", "Georgia")
#let body    = ("IBM Plex Sans", "Inter", "Liberation Sans", "DejaVu Sans")
#let mono    = ("IBM Plex Mono", "Liberation Mono", "DejaVu Sans Mono")

#set page(
  paper: "us-letter",
  flipped: true,
  margin: (top: 0.45in, bottom: 0.4in, left: 0.5in, right: 0.5in),
  fill: paper,
)
#set text(font: body, size: 8.5pt, fill: ink)
#set par(leading: 0.55em, justify: false)

#let eyebrow(s) = text(font: body, size: 7pt, weight: "semibold", fill: mute,
  tracking: 1.5pt)[#upper(s)]

#let pill(label, kind: "outline") = {
  let bg = none; let fg = warm; let bd = rule
  if kind == "critical"       { bg = rgb("#fff5f4"); fg = crit; bd = crit }
  else if kind == "high"      { bg = rgb("#fff8ec"); fg = high; bd = high }
  else if kind == "medium"    { bg = rgb("#fffbe6"); fg = med;  bd = rgb("#b9941b") }
  else if kind == "low"       { bg = rgb("#f3f9f3"); fg = ok;   bd = ok }
  else if kind == "buy"       { bg = ok;   fg = white; bd = ok }
  else if kind == "negotiate" { bg = high; fg = white; bd = high }
  else if kind == "walk"      { bg = crit; fg = white; bd = crit }
  else if kind == "none"      { bg = white; fg = ok; bd = ok }
  box(fill: bg, stroke: 0.6pt + bd, inset: (x: 5pt, y: 2pt), radius: 2pt)[
    #text(font: body, size: 7pt, weight: "bold", tracking: 0.6pt, fill: fg)[
      #upper(label)
    ]
  ]
}

#let dl(content) = grid(
  columns: (auto, 1fr),
  column-gutter: 8pt,
  row-gutter: 3pt,
  ..content,
)

// ─── HEADER BAR ────────────────────────────────────────────────────────
#block(
  width: 100%,
  fill: navy,
  inset: (x: 14pt, y: 10pt),
  radius: 2pt,
)[
  #grid(
    columns: (1fr, auto),
    align: (left + horizon, right + horizon),
    [
      #text(font: display, size: 8.5pt, weight: "semibold", fill: white,
            tracking: 4pt)[#upper("FactTrack")]
      #h(8pt)
      #text(font: body, size: 7pt, fill: rgb("#a8b4c4"), tracking: 1.4pt)[
        #upper("Acquisition Deal Memo")
      ]
      #v(2pt)
      #text(font: display, size: 16pt, weight: "semibold", fill: white)[
        #data.tract.label
      ]
    ],
    [
      #text(font: body, size: 7pt, fill: rgb("#a8b4c4"), tracking: 1.4pt)[
        #upper("Recommendation")
      ]
      #v(2pt)
      #if data.recommendation.recommendation == "BUY" {
        pill("BUY", kind: "buy")
      } else if data.recommendation.recommendation == "NEGOTIATE" {
        pill("Negotiate", kind: "negotiate")
      } else {
        pill("Walk", kind: "walk")
      }
    ],
  )
]

#v(8pt)

#grid(
  columns: (auto, 1fr, auto),
  column-gutter: 14pt,
  align: (left, left, right),
  text(size: 8pt, fill: warm)[
    *Abstract:* #data.tract.abstract_no
    #h(10pt) *Survey:* #data.tract.survey_name
    #h(10pt) *Acres:* #data.tract.gross_acres
    #h(10pt) *County:* #data.tract.county_name
  ],
  [],
  text(size: 7.5pt, fill: mute)[
    Prepared #data.generated_at  ·  #data.prepared_by
  ],
)

#v(6pt)
#line(length: 100%, stroke: 0.5pt + rule)
#v(8pt)

// ─── TWO-COLUMN BODY: STATUS+NRI ON LEFT, SCORECARD+COMP ON RIGHT ─────
#grid(
  columns: (1fr, 1fr),
  column-gutter: 18pt,
  // ============ LEFT COLUMN ============
  [
    #eyebrow("Current status")
    #v(4pt)
    #block(
      fill: surf,
      stroke: (left: 2.5pt + navy),
      inset: 10pt,
      width: 100%,
    )[
      #text(font: display, size: 12pt, weight: "semibold", fill: navy)[
        #data.current_status.status
      ]
      #v(3pt)
      #text(size: 8pt, fill: warm)[#data.current_status.rationale]
      #v(5pt)
      #grid(
        columns: (auto, auto, auto, auto),
        column-gutter: 14pt,
        text(size: 7.5pt)[
          *Wells on tract:* #str(data.current_status.well_count)
        ],
        text(size: 7.5pt)[
          *Producing:* #str(data.current_status.producing_count)
        ],
        text(size: 7.5pt)[
          *Shut-in:* #str(data.current_status.shutin_count)
        ],
        text(size: 7.5pt)[
          *Last spud:* #if data.current_status.last_spud != none [#data.current_status.last_spud] else [—]
        ],
      )
      #if data.current_status.primary_operators.len() > 0 [
        #v(3pt)
        #text(size: 7.5pt, fill: warm)[*Operators:* #data.current_status.primary_operators.join(" · ")]
      ]
    ]

    #v(10pt)
    #eyebrow("Ownership & NRI to Monument as lessee")
    #v(4pt)
    #block(
      stroke: 0.5pt + rule,
      inset: 10pt,
      width: 100%,
    )[
      #grid(
        columns: (auto, 1fr),
        column-gutter: 14pt,
        row-gutter: 4pt,
        text(size: 8pt, fill: warm)[Working interest acquired],
        text(size: 9pt, weight: "semibold")[#str(calc.round(data.nri.wi_share * 100, digits: 2))%],

        text(size: 8pt, fill: warm)[Lessor royalty (worst case)],
        text(size: 9pt)[#str(calc.round(data.nri.lessor_royalty * 100, digits: 4))%],

        text(size: 8pt, fill: warm)[ORRI burden],
        text(size: 9pt)[#str(calc.round(data.nri.orri_burden * 100, digits: 4))%],

        text(size: 8pt, fill: warm)[Other burdens],
        text(size: 9pt)[#str(calc.round(data.nri.other_burden * 100, digits: 4))%],
      )
      #v(6pt)
      #line(length: 100%, stroke: 0.4pt + rule)
      #v(4pt)
      #grid(
        columns: (auto, 1fr),
        column-gutter: 14pt,
        text(size: 9pt, weight: "semibold", fill: navy)[NRI TO MONUMENT],
        text(font: display, size: 16pt, weight: "semibold", fill: navy)[
          #str(calc.round(data.nri.value, digits: 6))
        ],
      )
      #v(2pt)
      #text(size: 7.5pt, fill: mute)[
        = WI × (1 − lessor royalty − ORRI burden − other burdens)
      ]
      #if data.nri.notes.len() > 0 [
        #v(6pt)
        #for n in data.nri.notes [
          #text(size: 7pt, style: "italic", fill: warm)[· #n]
          #v(2pt)
        ]
      ]
    ]

    #v(8pt)
    #eyebrow("Lease(s) of record")
    #v(4pt)
    #for le in data.leases [
      #block(width: 100%, below: 4pt)[
        #text(size: 8pt, weight: "semibold", fill: navy)[#le.instrument]
        #h(8pt)
        #text(size: 7.5pt, fill: mute)[recorded #le.recorded]
        #v(1pt)
        #text(size: 7.5pt, fill: warm)[
          #le.lessor → #le.lessee · royalty #le.royalty · term ends #le.primary_term_end
        ]
      ]
    ]
  ],
  // ============ RIGHT COLUMN ============
  [
    #eyebrow("Curative scorecard")
    #v(4pt)
    #block(
      stroke: 0.5pt + rule,
      inset: 10pt,
      width: 100%,
    )[
      #grid(
        columns: (auto, auto, auto, auto, 1fr),
        column-gutter: 12pt,
        align: (left + horizon, left + horizon, left + horizon, left + horizon, right + horizon),
        text(size: 8pt, fill: warm)[Tier],
        [
          #if data.curative_scorecard.tier == "HIGH" [
            #pill("High", kind: "critical")
          ] else if data.curative_scorecard.tier == "MEDIUM" [
            #pill("Medium", kind: "high")
          ] else if data.curative_scorecard.tier == "LOW" [
            #pill("Low", kind: "medium")
          ] else [
            #pill("None", kind: "none")
          ]
        ],
        text(size: 8pt, fill: warm)[Critical / High / Med],
        text(size: 9pt, weight: "semibold")[
          #str(data.curative_scorecard.crit_count) / #str(data.curative_scorecard.high_count) / #str(data.curative_scorecard.medium_count)
        ],
        [],
      )
      #v(6pt)
      #line(length: 100%, stroke: 0.4pt + rule)
      #v(6pt)
      #grid(
        columns: (auto, 1fr),
        column-gutter: 14pt,
        row-gutter: 4pt,
        text(size: 8pt, fill: warm)[Estimated effort],
        text(size: 9pt, weight: "semibold")[
          #if data.curative_scorecard.hours_lo > 0 [
            #str(data.curative_scorecard.hours_lo)–#str(data.curative_scorecard.hours_hi) hr
          ] else [
            —
          ]
        ],
        text(size: 8pt, fill: warm)[Recording fees],
        text(size: 9pt, weight: "semibold")[
          #if data.curative_scorecard.recording_cost > 0 [
            ~\$#str(data.curative_scorecard.recording_cost)
          ] else [—]
        ],
      )
      #if data.curative_scorecard.items.len() > 0 [
        #v(6pt)
        #line(length: 100%, stroke: 0.4pt + rule)
        #v(4pt)
        #for it in data.curative_scorecard.items [
          #grid(
            columns: (50pt, 1fr),
            column-gutter: 8pt,
            pill(it.severity, kind: it.severity),
            [
              #text(size: 8pt, weight: "semibold")[#it.title]
              #v(1pt)
              #text(size: 7.5pt, fill: warm)[#it.effort]
              #v(1pt)
              #text(size: 7pt, fill: mute, font: mono)[#it.rule_id]
            ],
          )
          #v(3pt)
        ]
      ] else [
        #v(6pt)
        #text(size: 8pt, style: "italic", fill: mute)[
          No curative items found on this tract against the current rule set.
        ]
      ]
    ]

    #v(10pt)
    #eyebrow("Competitive landscape")
    #v(4pt)
    #block(
      stroke: 0.5pt + rule,
      inset: 10pt,
      width: 100%,
    )[
      #text(size: 8pt, fill: warm, weight: "semibold")[Prior / current lessees on tract:]
      #v(2pt)
      #for p in data.competitive.prior_lessees [
        #text(size: 7.5pt)[
          · #p.instrument (rec #p.recorded): *#p.lessee*
        ]
        #v(1pt)
      ]
      #if data.competitive.releases.len() > 0 [
        #v(4pt)
        #text(size: 8pt, fill: warm, weight: "semibold")[Recorded releases (chain clear back through):]
        #v(2pt)
        #for r in data.competitive.releases [
          #text(size: 7.5pt)[
            · #r.instrument by *#r.by* on #r.recorded
          ]
          #v(1pt)
        ]
      ]
      #if data.competitive.top_leases.len() > 0 [
        #v(4pt)
        #text(size: 8pt, fill: crit, weight: "semibold")[⚠ Competing top-lease(s) of record:]
        #v(2pt)
        #for t in data.competitive.top_leases [
          #text(size: 7.5pt, fill: crit)[
            · #t.instrument by *#t.by* on #t.recorded
          ]
          #v(1pt)
        ]
      ]
    ]

    #v(10pt)
    #eyebrow("Recommendation")
    #v(4pt)
    #block(
      fill: surf,
      stroke: (left: 3pt + (
        if data.recommendation.recommendation == "BUY" { ok }
        else if data.recommendation.recommendation == "NEGOTIATE" { high }
        else { crit }
      )),
      inset: 12pt,
      width: 100%,
    )[
      #text(font: display, size: 18pt, weight: "semibold", fill: navy)[
        #data.recommendation.recommendation
      ]
      #v(4pt)
      #for r in data.recommendation.reasons [
        #text(size: 8.5pt, fill: warm)[· #r]
        #v(2pt)
      ]
      #if data.recommendation.walkaway_bonus_ceiling_per_ac != none [
        #v(4pt)
        #line(length: 100%, stroke: 0.4pt + rule)
        #v(4pt)
        #grid(
          columns: (auto, 1fr),
          column-gutter: 14pt,
          text(size: 8pt, fill: warm)[*Walk-away bonus ceiling*],
          text(font: display, size: 13pt, weight: "semibold", fill: navy)[
            \$#str(data.recommendation.walkaway_bonus_ceiling_per_ac)/ac
          ],
        )
      ]
    ]
  ],
)

#v(1fr)
#line(length: 100%, stroke: 0.3pt + rule)
#v(4pt)
#text(size: 7pt, fill: mute, style: "italic")[
  Decision memo synthesizes the FactTrack project dossier (#data.project_id).
  Underlying chain of title, lease-image scans, audit transcripts, and rule-engine
  rationale are filed under the same project — flip to the dossier for source
  citations on any line item above. Not a substitute for a title opinion.
]
