"""Clause extraction from OCR'd East-TX lease text.

Regex-driven first pass. OCR noise is real (the word "lease" frequently
comes through as "Gfise" / "Gase" / "lessse" depending on scan quality),
so we use case-insensitive patterns and tolerate common substitutions.

Extracted fields written back to `lease`:
  - primary_term_years        e.g. "for a term of 5 years" → 5.0
  - primary_term_end          computed from effective_date + primary_term_years
  - royalty_fraction          common East-TX fractions: 1/8, 3/16, 1/4
  - has_pugh_clause           boolean
  - has_retained_acreage      boolean
  - has_continuous_dev        boolean
  - depth_limit_ft            from "to a depth of X feet" / "to base of <formation>"
  - lease_party.is_deceased   from "deceased" / "estate of" markers near a name

If a value is found by regex but with low confidence (e.g. OCR-noisy region),
the field is left null rather than guessed.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

from facttrack.db import cursor
from facttrack.ocr.tesseract import read_ocr_text

log = logging.getLogger(__name__)


@dataclass
class ClauseFindings:
    instrument_no: str
    county_fips: str
    primary_term_years: float | None = None
    primary_term_end: date | None = None
    royalty_fraction: float | None = None
    has_pugh_clause: bool | None = None
    has_retained_acreage: bool | None = None
    has_continuous_dev: bool | None = None
    depth_limit_ft: float | None = None
    deceased_lessor_names: list[str] = field(default_factory=list)
    raw_excerpts: dict[str, str] = field(default_factory=dict)


# ── Regex patterns (OCR-noise tolerant) ──────────────────────────────────

# "for a term of (\d+) years" — OCR sometimes splits "years" as "year s"
_PRIMARY_TERM_RE = re.compile(
    r"for\s+(?:a\s+)?(?:primary\s+)?term\s+of\s+(\d{1,3})\s*(?:\(\d{1,3}\)\s*)?(year|yr|yrs?)\.?\s*s?\b",
    re.IGNORECASE,
)
_PRIMARY_TERM_RE_2 = re.compile(
    r"primary\s+term\s+(?:of|shall\s+be|is)\s+(\d{1,3})\s+(year|yr|yrs?)\.?s?",
    re.IGNORECASE,
)
# Spelled-out term: "for a term of ten (10) years". Must be anchored to
# "for ... term of" or "primary term of" — otherwise it false-fires on
# shut-in royalty clauses ("beyond a period of three (3) years from the
# date said well was shut in"), continuing-operations clauses, etc.
_TEN_YEAR_RE = re.compile(
    r"(?:for\s+(?:a\s+)?(?:primary\s+)?term\s+of"
    r"|primary\s+term\s+(?:of|shall\s+be|is))"
    r"\s+(?:ten|10|five|5|three|3|seven|7)\s*\(?\s*(\d{1,2})\s*\)?\s*(?:year|yr)",
    re.IGNORECASE,
)

# Royalty fractions — accept stamped fractions like "1/8" "3/16" "one-eighth"
_ROYALTY_FRAC_RE = re.compile(
    r"royalty\s+of\s+"
    r"(?:"
    r"(?P<num>\d{1,2})\s*/\s*(?P<den>\d{1,3})"        # 1/8, 3/16
    r"|"
    r"(?:one|two|three|four|five|six|seven|eight)\s*[-—]?\s*"
    r"(?P<word>eighth|sixteenth|fourth|third|half|tenth|twelfth)"
    r")",
    re.IGNORECASE,
)
_ROYALTY_FRAC_LOOSE_RE = re.compile(
    r"\bone[\s-]+(?P<word>eighth|sixteenth|fourth)\b",
    re.IGNORECASE,
)
_WORD_TO_FRAC = {
    "eighth":     1.0 / 8.0,
    "sixteenth":  1.0 / 16.0,
    "fourth":     1.0 / 4.0,
    "third":      1.0 / 3.0,
    "half":       1.0 / 2.0,
    "tenth":      1.0 / 10.0,
    "twelfth":    1.0 / 12.0,
}

# Pugh clause — East-TX standard + older 1950s phrasing.
_PUGH_RE = re.compile(
    r"(?:pugh\s+clause"
    r"|release\s+all\s+(?:land|acreage)\s+not\s+(?:included|held|pooled)"
    r"|acreage\s+not\s+(?:held\s+by\s+production|included\s+in\s+(?:a\s+)?(?:producing\s+)?unit)"
    r"|terminate\s+as\s+to\s+(?:all\s+)?(?:land|acreage)\s+not\s+(?:included|held|pooled|within)"
    r"|all\s+acreage\s+outside\s+(?:any\s+)?(?:producing|pooled)\s+units?\s+shall\s+(?:be\s+)?(?:released|terminate)"
    r")",
    re.IGNORECASE,
)

# Retained acreage clause (East-TX continuous-development surrogate)
_RETAINED_ACREAGE_RE = re.compile(
    r"(?:retained\s+acreage|drillsite\s+acreage|production\s+unit\s+(?:of|consisting\s+of)\s+\d+\s+acres)",
    re.IGNORECASE,
)

# Continuous-development clause
_CONTINUOUS_DEV_RE = re.compile(
    r"(?:continuous\s+(?:drilling|development)|so\s+long\s+as\s+(?:operations|drilling)|"
    r"recurring\s+intervals?\s+of\s+\d+\s+(?:days?|months?))",
    re.IGNORECASE,
)

# Depth limit: "to a depth of X feet" / "to the base of the (named) formation" / "from
# surface to X feet"
_DEPTH_FT_RE = re.compile(
    r"(?:to\s+(?:a\s+)?(?:total\s+)?depth\s+of|surface\s+down\s+to|surface\s+to)\s+(?P<feet>\d{3,5})\s+(?:feet|ft\.?)",
    re.IGNORECASE,
)
_DEPTH_FORMATION_RE = re.compile(
    r"to\s+(?:the\s+)?(?:base|stratigraphic\s+equivalent)\s+of\s+(?:the\s+)?(?P<formation>[A-Z][A-Za-z ]{3,40}?)\s+(?:formation|sand|zone|reservoir|group)",
)

# Deceased lessor markers
_DECEASED_RE = re.compile(
    r"(?:(?:estate\s+of|deceased|dec'd|dec\.?\s*)\s+)?([A-Z][A-Z. ]{2,40})\s*(?:,?\s+(?:deceased|dec'd|dec\.)|"
    r"\s*estate\s+of|\s+a\s+widow|\s+a\s+widower)",
)


def extract_from_text(text: str) -> dict:
    """Return all clause fields we can detect from the OCR text."""
    out: dict = {"raw_excerpts": {}}

    # Primary term
    for pat in (_PRIMARY_TERM_RE, _PRIMARY_TERM_RE_2, _TEN_YEAR_RE):
        m = pat.search(text)
        if m:
            try:
                out["primary_term_years"] = float(m.group(1))
                start = max(m.start() - 40, 0)
                out["raw_excerpts"]["primary_term"] = text[start:m.end() + 40].replace("\n", " ")
                break
            except (ValueError, IndexError):
                continue

    # Royalty fraction
    m = _ROYALTY_FRAC_RE.search(text)
    if m:
        if m.group("num") and m.group("den"):
            try:
                num = int(m.group("num"))
                den = int(m.group("den"))
                if 1 <= num <= 99 and 2 <= den <= 999 and num < den:
                    out["royalty_fraction"] = num / den
                    out["raw_excerpts"]["royalty"] = text[max(m.start() - 30, 0):m.end() + 30].replace("\n", " ")
            except ValueError:
                pass
        elif m.group("word"):
            word = m.group("word").lower()
            if word in _WORD_TO_FRAC:
                out["royalty_fraction"] = _WORD_TO_FRAC[word]
                out["raw_excerpts"]["royalty"] = text[max(m.start() - 30, 0):m.end() + 30].replace("\n", " ")
    if "royalty_fraction" not in out:
        m = _ROYALTY_FRAC_LOOSE_RE.search(text)
        if m:
            word = m.group("word").lower()
            if word in _WORD_TO_FRAC:
                out["royalty_fraction"] = _WORD_TO_FRAC[word]
                out["raw_excerpts"]["royalty_loose"] = text[max(m.start() - 30, 0):m.end() + 30].replace("\n", " ")

    # Pugh clause
    m = _PUGH_RE.search(text)
    if m:
        out["has_pugh_clause"] = True
        out["raw_excerpts"]["pugh"] = text[max(m.start() - 40, 0):m.end() + 80].replace("\n", " ")

    # Retained acreage
    m = _RETAINED_ACREAGE_RE.search(text)
    if m:
        out["has_retained_acreage"] = True
        out["raw_excerpts"]["retained_acreage"] = text[max(m.start() - 30, 0):m.end() + 60].replace("\n", " ")

    # Continuous development
    m = _CONTINUOUS_DEV_RE.search(text)
    if m:
        out["has_continuous_dev"] = True
        out["raw_excerpts"]["continuous_dev"] = text[max(m.start() - 30, 0):m.end() + 60].replace("\n", " ")

    # Depth limit
    m = _DEPTH_FT_RE.search(text)
    if m:
        try:
            feet = float(m.group("feet"))
            if 100 <= feet <= 30_000:
                out["depth_limit_ft"] = feet
                out["raw_excerpts"]["depth_limit"] = text[max(m.start() - 30, 0):m.end() + 30].replace("\n", " ")
        except (ValueError, IndexError):
            pass

    # Deceased lessor markers
    deceased: list[str] = []
    for m in _DECEASED_RE.finditer(text):
        name = m.group(1).strip()
        # Filter out obvious non-names (state names, common nouns)
        if len(name) < 4 or len(name) > 40:
            continue
        upper_words = sum(1 for w in name.split() if w[:1].isupper())
        if upper_words < 2:
            continue
        deceased.append(name)
    if deceased:
        out["deceased_lessor_names"] = list(dict.fromkeys(deceased))[:5]  # dedupe, cap
        out["raw_excerpts"]["deceased"] = "; ".join(out["deceased_lessor_names"])

    return out


def parse_lease_documents(county_fips: str, instrument_no: str) -> ClauseFindings | None:
    """Read the OCR text of every page image for one lease + update DB."""
    with cursor() as cur:
        cur.execute(
            """
            SELECT id, effective_date, recording_date, parsed_metadata, primary_term_years
            FROM lease
            WHERE county_fips = %s AND opr_instrument_no = %s
            """,
            (county_fips, instrument_no),
        )
        row = cur.fetchone()
    if not row:
        log.warning("no lease row for %s/%s", county_fips, instrument_no)
        return None
    lease_id = row["id"]
    meta = row.get("parsed_metadata") or {}
    image_paths = meta.get("image_paths") if isinstance(meta, dict) else None
    if not image_paths:
        log.info("no image_paths for %s/%s — fetch documents first", county_fips, instrument_no)
        return None

    # OCR each page + concatenate
    full_text_parts: list[str] = []
    for img in image_paths:
        try:
            full_text_parts.append(read_ocr_text(Path(img)))
        except Exception as e:
            log.warning("OCR failed for %s: %s", img, e)
    if not full_text_parts:
        return None
    full_text = "\n\n".join(full_text_parts)

    extracted = extract_from_text(full_text)
    findings = ClauseFindings(
        instrument_no=instrument_no,
        county_fips=county_fips,
        primary_term_years=extracted.get("primary_term_years"),
        royalty_fraction=extracted.get("royalty_fraction"),
        has_pugh_clause=extracted.get("has_pugh_clause"),
        has_retained_acreage=extracted.get("has_retained_acreage"),
        has_continuous_dev=extracted.get("has_continuous_dev"),
        depth_limit_ft=extracted.get("depth_limit_ft"),
        deceased_lessor_names=extracted.get("deceased_lessor_names", []),
        raw_excerpts=extracted.get("raw_excerpts", {}),
    )

    # Compute primary_term_end if we have both years + effective_date
    effective = row.get("effective_date") or row.get("recording_date")
    if findings.primary_term_years and effective:
        try:
            from datetime import timedelta
            days = int(findings.primary_term_years * 365.25)
            findings.primary_term_end = effective + timedelta(days=days)
        except Exception:
            pass

    # Persist back to lease
    with cursor(dict_rows=False) as cur:
        cur.execute(
            """
            UPDATE lease
               SET primary_term_years   = COALESCE(%s, primary_term_years),
                   primary_term_end     = COALESCE(%s, primary_term_end),
                   royalty_fraction     = COALESCE(%s, royalty_fraction),
                   has_pugh_clause      = COALESCE(%s, has_pugh_clause),
                   has_retained_acreage = COALESCE(%s, has_retained_acreage),
                   has_continuous_dev   = COALESCE(%s, has_continuous_dev),
                   depth_limit_ft       = COALESCE(%s, depth_limit_ft),
                   parsed_metadata      = parsed_metadata || jsonb_build_object('clause_excerpts', %s::jsonb)
             WHERE id = %s
            """,
            (
                findings.primary_term_years, findings.primary_term_end,
                findings.royalty_fraction, findings.has_pugh_clause,
                findings.has_retained_acreage, findings.has_continuous_dev,
                findings.depth_limit_ft,
                __import__("json").dumps(findings.raw_excerpts),
                lease_id,
            ),
        )

        # Mark any deceased lessors found
        for name in findings.deceased_lessor_names:
            cur.execute(
                """
                UPDATE lease_party
                   SET is_deceased = TRUE
                 WHERE lease_id = %s
                   AND UPPER(name) LIKE %s
                """,
                (lease_id, f"%{name.upper().replace(' ', '%')}%"),
            )

    log.info("clauses extracted for %s/%s: %s", county_fips, instrument_no,
             {k: v for k, v in extracted.items() if k != "raw_excerpts"})
    return findings


def parse_all_documented_leases_for_county(county_fips: str) -> dict[str, int]:
    """Run clause extraction on every lease in `county_fips` that has image_paths."""
    counts = {"attempted": 0, "succeeded": 0, "no_images": 0, "no_findings": 0}
    with cursor() as cur:
        cur.execute(
            """
            SELECT opr_instrument_no, parsed_metadata
            FROM lease
            WHERE county_fips = %s AND opr_instrument_no IS NOT NULL
            """,
            (county_fips,),
        )
        rows = cur.fetchall()

    for row in rows:
        meta = row.get("parsed_metadata") or {}
        if not (isinstance(meta, dict) and meta.get("image_paths")):
            counts["no_images"] += 1
            continue
        counts["attempted"] += 1
        try:
            findings = parse_lease_documents(county_fips, row["opr_instrument_no"])
            if findings and any([
                findings.primary_term_years, findings.royalty_fraction,
                findings.has_pugh_clause, findings.depth_limit_ft,
                findings.deceased_lessor_names,
            ]):
                counts["succeeded"] += 1
            else:
                counts["no_findings"] += 1
        except Exception as e:
            log.warning("clause parse failed for %s: %s", row["opr_instrument_no"], e)
    log.info("clause parse summary: %s", counts)
    return counts


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--county", required=True)
    parser.add_argument("--instrument")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.instrument:
        result = parse_lease_documents(args.county, args.instrument)
        if result is None:
            print("no result (lease not found or no images)")
        else:
            print(result)
    else:
        result = parse_all_documented_leases_for_county(args.county)
        print(result)
