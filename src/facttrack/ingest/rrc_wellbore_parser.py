"""Parse the RRC Wellbore Query Data CSV dump and upsert wells into Postgres.

The dump is the file shipped as `OG_WELLBORE_EWA_Report.csv` inside the RRC
GoAnywhere MFT zip. It is a HEADERLESS, POSITIONAL, 59-column CSV. Column
positions verified by inspecting the first rows of the 2026-05-13 snapshot.

Column index → meaning (1-indexed positions from inspection):
  1  rrc_district             "06"
  2  rrc_county_code          "001"  (NOT the same as FIPS)
  3  rrc_lease_id_8char       "00100001"  (county + lease sequence)
  4  county_name              "ANDERSON"
  5  well_type_code           "O"/"G" (oil/gas/inj/etc)
  6  lease_name               "7-11 RANCH -B-"
  7  lease_id_num             "16481001"
  8  field_name               "CAYUGA"
  9  field_number             "04411"
 10  well_number              "   1\t"
 12  operator_name            "SUPREME ENERGY COMPANY  INC."
 13  operator_p5              "830589"
 14  classification           "Land Well"
 16  4-digit_field_id_or_depth
 17  yyyymm_last_report
 19  status                   "SHUT IN" / "PRODUCING"
 20  yyyymm_status_change
 28  rrc_internal_well_seq    10-digit (e.g. "4644117776")
 29  yyyymmdd_completion      "19840112"
 30  yyyymmdd_first_prod      "19631205"
 31  yyyymmdd_spud            "19631027"

We synthesize a 14-char primary key (our schema requires CHAR(14)) from
(district + 8-char-rrc-id + 3-char-pad), e.g. "06_00100001_TX". The real
TX 14-digit API is not in this dump and would require joining a separate
file ('Statewide API Data' from the MFT page).

RRC county code (not FIPS) → state FIPS mapping for the East-TX counties
we care about. RRC uses its own 3-digit county numbering scheme.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Iterator

from facttrack.db import cursor

log = logging.getLogger(__name__)


# Column index constants (0-based)
C_DISTRICT       = 0
C_COUNTY_CODE    = 1   # RRC county code, NOT FIPS
C_RRC_LEASE_ID   = 2
C_COUNTY_NAME    = 3   # full uppercase, e.g. "ANDERSON"
C_WELL_TYPE      = 4
C_LEASE_NAME     = 5
C_FIELD_NAME     = 7
C_WELL_NUMBER    = 9
C_OPERATOR_NAME  = 11
C_OPERATOR_P5    = 12
C_CLASSIFICATION = 13
C_STATUS         = 18
C_RRC_WELL_SEQ   = 27
C_COMPLETION_DT  = 28  # yyyymmdd
C_FIRST_PROD_DT  = 29  # yyyymmdd
C_SPUD_DT        = 30  # yyyymmdd

# RRC county code → state FIPS for the East-TX counties we support.
# (RRC numbers counties alphabetically with no relation to FIPS.)
RRC_COUNTY_TO_FIPS: dict[str, str] = {
    "001": "48001",  # Anderson — both Texas + RRC use "001" here
    "145": "48289",  # Leon
    "081": "48161",  # Freestone
    "212": "48423",  # Smith
    "174": "48347",  # Nacogdoches
    "159": "48313",  # Madison
    "227": "48471",  # Walker
    "113": "48225",  # Houston
}
# Also accept by county-name uppercase, since the dump prints both.
COUNTY_NAMES = {
    "ANDERSON": "48001",
    "LEON": "48289",
    "FREESTONE": "48161",
    "SMITH": "48423",
    "NACOGDOCHES": "48347",
    "MADISON": "48313",
    "WALKER": "48471",
    "HOUSTON": "48225",
}


@dataclass
class WellRow:
    rrc_well_key: str           # synthesized 14-char primary key
    rrc_district: str | None
    county_fips: str | None
    operator_p5: int | None
    operator_name: str | None
    lease_name: str | None
    well_no: str | None
    field_name: str | None
    well_type: str | None
    classification: str | None
    status: str | None
    spud_date: date | None
    completion_date: date | None
    first_prod_date: date | None


def _parse_yyyymmdd(s: str) -> date | None:
    s = (s or "").strip()
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


def _strip_quoted(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    return s.strip()


def _parse_int(s: str) -> int | None:
    s = (s or "").strip().strip('"')
    if not s:
        return None
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    val = int(digits)
    return val if val > 0 else None


def _row_to_well(cells: list[str]) -> WellRow | None:
    if len(cells) < 31:
        return None
    district = _strip_quoted(cells[C_DISTRICT])
    rrc_lease_id = _strip_quoted(cells[C_RRC_LEASE_ID])
    county_name = _strip_quoted(cells[C_COUNTY_NAME]).upper()
    if not rrc_lease_id or not district:
        return None
    fips = COUNTY_NAMES.get(county_name)
    well_no = _strip_quoted(cells[C_WELL_NUMBER]).replace("\t", "").strip()
    # Synthesized 14-char primary key: "TX" + district(2) + rrc_lease_id(8) + well_no_pad(2)
    well_no_compact = re.sub(r"\D", "", well_no)[-2:].zfill(2) if well_no else "00"
    rrc_well_key = (f"TX{district[:2].zfill(2)}{rrc_lease_id[:8].zfill(8)}{well_no_compact}")[:14]
    return WellRow(
        rrc_well_key=rrc_well_key,
        rrc_district=district[:2],
        county_fips=fips,
        operator_p5=_parse_int(cells[C_OPERATOR_P5]),
        operator_name=_strip_quoted(cells[C_OPERATOR_NAME]) or None,
        lease_name=_strip_quoted(cells[C_LEASE_NAME]) or None,
        well_no=well_no or None,
        field_name=_strip_quoted(cells[C_FIELD_NAME]) or None,
        well_type=_strip_quoted(cells[C_WELL_TYPE]) or None,
        classification=_strip_quoted(cells[C_CLASSIFICATION]) or None,
        status=_strip_quoted(cells[C_STATUS]) or None,
        spud_date=_parse_yyyymmdd(_strip_quoted(cells[C_SPUD_DT])),
        completion_date=_parse_yyyymmdd(_strip_quoted(cells[C_COMPLETION_DT])),
        first_prod_date=_parse_yyyymmdd(_strip_quoted(cells[C_FIRST_PROD_DT])),
    )


def iter_wells_for_counties(path: Path, county_fips_list: list[str]) -> Iterator[WellRow]:
    """Stream-parse the dump, yielding wells in the target counties (by FIPS)."""
    name_set = {n for n, f in COUNTY_NAMES.items() if f in set(county_fips_list)}
    if not name_set:
        raise ValueError(f"none of {county_fips_list} are in COUNTY_NAMES")

    opener = zipfile.ZipFile(path) if zipfile.is_zipfile(path) else None
    if opener is not None:
        csv_name = next((n for n in opener.namelist() if n.lower().endswith(".csv")), None)
        if not csv_name:
            raise RuntimeError(f"zip {path} contains no .csv")
        stream = io.TextIOWrapper(opener.open(csv_name), encoding="latin-1", errors="replace")
    else:
        stream = open(path, "r", encoding="latin-1", errors="replace", newline="")

    try:
        reader = csv.reader(stream)
        for cells in reader:
            if len(cells) < C_COUNTY_NAME + 1:
                continue
            county = _strip_quoted(cells[C_COUNTY_NAME]).upper()
            if county not in name_set:
                continue
            well = _row_to_well(cells)
            if well is not None:
                yield well
    finally:
        try:
            stream.close()
        except Exception:
            pass
        if opener is not None:
            opener.close()


def upsert_wells_and_operators(wells: Iterator[WellRow]) -> dict[str, int]:
    counts = {"wells": 0, "operators": 0}
    seen_operators: set[int] = set()
    with cursor(dict_rows=False) as cur:
        for w in wells:
            if w.operator_p5 and w.operator_p5 not in seen_operators:
                cur.execute(
                    """
                    INSERT INTO operator (rrc_p5_number, name, status, last_seen_at)
                    VALUES (%s, %s, %s, now())
                    ON CONFLICT (rrc_p5_number) DO UPDATE
                      SET name = COALESCE(EXCLUDED.name, operator.name),
                          last_seen_at = now()
                    """,
                    (w.operator_p5, w.operator_name or f"P5#{w.operator_p5}", None),
                )
                counts["operators"] += 1
                seen_operators.add(w.operator_p5)

            cur.execute(
                """
                INSERT INTO well (api_no, rrc_district, county_fips, operator_p5,
                                  lease_name, well_no, field_name, status,
                                  spud_date, completion_date, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (api_no) DO UPDATE
                  SET operator_p5 = COALESCE(EXCLUDED.operator_p5, well.operator_p5),
                      status = EXCLUDED.status,
                      lease_name = COALESCE(EXCLUDED.lease_name, well.lease_name),
                      field_name = COALESCE(EXCLUDED.field_name, well.field_name),
                      last_seen_at = now()
                """,
                (
                    w.rrc_well_key, w.rrc_district, w.county_fips, w.operator_p5,
                    w.lease_name, w.well_no, w.field_name, w.status,
                    w.spud_date, w.completion_date,
                    '{"source": "rrc_wellbore_query_csv", "well_type": "'
                    + (w.well_type or "") + '", "classification": "'
                    + (w.classification or "") + '"}',
                ),
            )
            counts["wells"] += 1
    log.info("upserted %d wells, %d operators", counts["wells"], counts["operators"])
    return counts


def ingest_wellbore_dump(path: Path, county_fips_list: list[str]) -> dict[str, int]:
    return upsert_wells_and_operators(iter_wells_for_counties(path, county_fips_list))


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--counties", required=True, help="comma-separated FIPS list")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = ingest_wellbore_dump(Path(args.path), args.counties.split(","))
    print(f"upserted: {result}")
