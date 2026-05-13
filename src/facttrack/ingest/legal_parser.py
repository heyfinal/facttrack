"""Parse East-Texas legal descriptions from OPR records into canonical tract rows.

East-TX legal descriptions are written in survey/abstract format, not PLSS sections
(unlike most US states). Example:
    "C W HANKS SVY A-1112 11.23 ACS"
    "JOHN DURST SVY A-30 296.5 ACS"
    "ANDERSON COUNTY SCHOOL LAND SVY A-68 124.5 ACS"

A single legal can reference multiple tracts ("TR 1", "TR 2") and multiple
surveys. We extract one tract per (survey_name, abstract_no) combination.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from facttrack.db import cursor

log = logging.getLogger(__name__)


# Patterns observed in publicsearch.us-rendered Anderson legals:
#   "C W HANKS SVY A-1112 11.23 ACS"
#   "D STILTS SVY A-726 120.0 ACS"
#   "ANDERSON COUNTY SCHOOL LAND SVY A-68 122.0 ACS"
#   "JOHN DURST SVY A-30 JOHN SHERIDAN SVY A-956 & L SHERIDAN SVY A-904 296.5 ACS & JOHN DURST SVY 20.0 & 55.6 ACS"
_SURVEY_TOKEN = r"[A-Z][A-Z .&'-]{2,60}?"
_ABSTRACT_TOKEN = r"A-?\s*\d{1,5}[A-Z]?"
_ACRES_TOKEN = r"(\d{1,5}(?:\.\d{1,4})?)\s*(?:ACS?|AC\.?)\b"

# Primary form: <SURVEY NAME> SVY <ABSTRACT> [<ACRES> ACS]
_TRACT_RE = re.compile(
    r"(?P<survey>" + _SURVEY_TOKEN + r")\s+SVY\b\s*(?P<abstract>" + _ABSTRACT_TOKEN + r")?\s*(?:(?P<acres>\d{1,5}(?:\.\d{1,4})?)\s*ACS?)?",
    re.IGNORECASE,
)


@dataclass
class ParsedTract:
    survey_name: str
    abstract_no: str | None
    acres: float | None

    def label(self, county_name: str) -> str:
        parts = [_title_case_survey(self.survey_name) + " Survey"]
        if self.abstract_no:
            parts.append(self.abstract_no)
        if self.acres:
            parts.append(f"{self.acres:g} ac")
        return f"{county_name} Co — " + " · ".join(parts)


# Words that should drop out of survey names entirely (acreage tokens that
# leaked through the regex when a legal description was unusually formatted).
_SURVEY_DROP_TOKENS = {"ACS", "AC"}


def _title_case_survey(s: str) -> str:
    """Title-case a survey name without mangling Mc/Mac prefixes or single
    capital-letter initials. East-Texas surveys often carry names like
    'McKinzie', 'McKinney and Williams', or 'D Stilts' — Python's str.title()
    breaks all of these."""
    cleaned_words: list[str] = []
    for word in s.split():
        upper = word.upper()
        if upper in _SURVEY_DROP_TOKENS:
            continue
        if len(word) == 1:
            cleaned_words.append(word.upper())
            continue
        if upper.startswith("MC") and len(word) > 2:
            cleaned_words.append("Mc" + word[2:].capitalize())
        elif upper.startswith("MAC") and len(word) > 3 and word[3].isalpha():
            cleaned_words.append("Mac" + word[3:].capitalize())
        elif upper in {"AND", "OF", "THE"}:
            cleaned_words.append(word.lower())
        else:
            cleaned_words.append(word.capitalize())
    return " ".join(cleaned_words)


def parse_legal(legal: str) -> list[ParsedTract]:
    """Extract every (survey, abstract, acres) triplet from one legal description."""
    if not legal:
        return []
    found: list[ParsedTract] = []
    for m in _TRACT_RE.finditer(legal):
        survey = re.sub(r"\s+", " ", m.group("survey").strip())
        abstract = m.group("abstract")
        if abstract:
            abstract = "A-" + re.sub(r"^A-?\s*", "", abstract, flags=re.IGNORECASE).strip()
        acres_str = m.group("acres")
        acres = float(acres_str) if acres_str else None
        # Filter false-positives: survey name must contain at least one alphabetic char besides "TR"
        clean = re.sub(r"[^A-Z]", "", survey.upper())
        if not clean or clean.startswith("TR") and len(clean) <= 4:
            continue
        # Avoid duplicate tracts in same legal
        key = (survey.upper(), abstract or "")
        if any(p.survey_name.upper() == survey.upper() and (p.abstract_no or "") == (abstract or "") for p in found):
            continue
        found.append(ParsedTract(survey_name=survey, abstract_no=abstract, acres=acres))
    return found


def upsert_tract(parsed: ParsedTract, county_fips: str, county_name: str) -> int:
    """Insert or fetch the tract by (county, survey, abstract)."""
    label = parsed.label(county_name)
    with cursor(dict_rows=False) as cur:
        # We use the unique key (county_fips, abstract_no, survey_name, block_no, section_no)
        # where block_no/section_no are NULL for survey/abstract addressing.
        cur.execute(
            """
            INSERT INTO tract (county_fips, survey_name, abstract_no, label, gross_acres, metadata)
            VALUES (%s, %s, %s, %s, %s, '{"source": "publicsearch.us legal parsing"}'::jsonb)
            ON CONFLICT (county_fips, abstract_no, survey_name, block_no, section_no) DO UPDATE
              SET gross_acres = COALESCE(EXCLUDED.gross_acres, tract.gross_acres),
                  label = EXCLUDED.label
            RETURNING id
            """,
            (county_fips, parsed.survey_name, parsed.abstract_no, label, parsed.acres),
        )
        return int(cur.fetchone()[0])


def assign_leases_to_tracts(county_fips: str, county_name: str) -> dict[str, int]:
    """Run after publicsearch.us ingest: parse every lease's legal description, upsert
    the referenced tracts, and link leases to their first tract."""
    counts = {"leases_seen": 0, "leases_linked": 0, "tracts_upserted": 0, "leases_no_legal": 0}
    seen_tracts: set[tuple[str, str | None]] = set()
    with cursor(dict_rows=True) as cur:
        cur.execute(
            """
            SELECT id, parsed_metadata->>'legal' AS legal
            FROM lease
            WHERE county_fips = %s AND tract_id IS NULL
            """,
            (county_fips,),
        )
        rows = cur.fetchall()
    for r in rows:
        counts["leases_seen"] += 1
        legal = r["legal"]
        if not legal:
            counts["leases_no_legal"] += 1
            continue
        parsed_tracts = parse_legal(legal)
        if not parsed_tracts:
            counts["leases_no_legal"] += 1
            continue
        first = parsed_tracts[0]
        tract_id = upsert_tract(first, county_fips, county_name)
        key = (first.survey_name.upper(), first.abstract_no or "")
        if key not in seen_tracts:
            seen_tracts.add(key)
            counts["tracts_upserted"] += 1
        with cursor(dict_rows=False) as cur:
            cur.execute(
                "UPDATE lease SET tract_id = %s WHERE id = %s",
                (tract_id, r["id"]),
            )
        counts["leases_linked"] += 1
    log.info("legal parsing complete: %s", counts)
    return counts


def create_or_get_project_for_county(county_fips: str, county_name: str) -> str:
    """Create the per-county research project + link every tract to it."""
    project_id = f"county_research_{county_fips}"
    with cursor(dict_rows=False) as cur:
        cur.execute(
            """
            INSERT INTO project (id, label, customer_label, notes)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label
            """,
            (
                project_id,
                f"{county_name} County Research — All Tracts",
                f"{county_name} County, TX — public records analysis",
                "Auto-generated research project covering every tract with parsed OPR records.",
            ),
        )
        cur.execute(
            """
            INSERT INTO project_tract (project_id, tract_id)
            SELECT %s, id FROM tract WHERE county_fips = %s
            ON CONFLICT DO NOTHING
            """,
            (project_id, county_fips),
        )
    return project_id


if __name__ == "__main__":  # pragma: no cover
    import argparse
    import logging
    parser = argparse.ArgumentParser()
    parser.add_argument("--county-fips", required=True)
    parser.add_argument("--county-name", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    counts = assign_leases_to_tracts(args.county_fips, args.county_name)
    pid = create_or_get_project_for_county(args.county_fips, args.county_name)
    print(f"Project: {pid}")
    print(f"Counts: {counts}")
