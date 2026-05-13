"""Parse the RRC Wellbore Query Data CSV dump and upsert wells into Postgres.

The CSV is published as `OG_WELLBORE_EWA_Report.csv` (~450 MB). The first row
is the header; subsequent rows are wellbore records with these key columns
(documented by RRC; column names may have spaces stripped or case-shifted):

- API_NO           14-char API number (state-county-unique)
- DISTRICT         RRC district number (e.g. "06" for East Texas)
- COUNTY_NAME      county name (full text, not FIPS)
- OPERATOR_NUMBER  P-5 operator number
- OPERATOR_NAME    P-5 operator name
- LEASE_NAME       lease name
- WELL_NUMBER      well number on the lease
- FIELD_NAME       field name
- FIELD_NUMBER     RRC field number
- LATITUDE         surface latitude (decimal)
- LONGITUDE        surface longitude (decimal)
- WELL_STATUS      well status code
- SPUD_DATE        spud date
- COMPLETION_DATE  completion date

We filter to the counties we care about (Anderson, Leon, etc.) on the fly,
then upsert into `operator` + `well`. The full dump has ~250k+ wells, so
filtering early is essential.
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


# RRC uses the full county name in the dump, not FIPS. We map FIPS → name
# for callers that pass FIPS; the parser also accepts a list of names directly.
COUNTY_FIPS_TO_NAME: dict[str, str] = {
    "48001": "ANDERSON",
    "48289": "LEON",
    "48161": "FREESTONE",
    "48423": "SMITH",
    "48347": "NACOGDOCHES",
    "48313": "MADISON",
    "48471": "WALKER",
    "48225": "HOUSTON",
}


@dataclass
class WellRow:
    api_no: str
    rrc_district: str | None
    county_name: str | None
    operator_p5: int | None
    operator_name: str | None
    lease_name: str | None
    well_no: str | None
    field_name: str | None
    surface_lat: float | None
    surface_lon: float | None
    status: str | None
    spud_date: date | None
    completion_date: date | None


def _key(col: str) -> str:
    """Normalize a CSV column header to a stable key."""
    return re.sub(r"[^a-z0-9]", "", (col or "").lower())


_COL_API = {"apino", "apinumber", "api"}
_COL_DISTRICT = {"district", "districtno", "districtnumber"}
_COL_COUNTY = {"countyname", "county"}
_COL_OP_P5 = {"operatorno", "operatornumber", "operatorp5", "p5"}
_COL_OP_NAME = {"operatorname", "operator"}
_COL_LEASE = {"leasename", "lease"}
_COL_WELL = {"wellno", "wellnumber", "well"}
_COL_FIELD = {"fieldname", "field"}
_COL_LAT = {"latitude", "lat"}
_COL_LON = {"longitude", "lon", "long"}
_COL_STATUS = {"wellstatus", "status"}
_COL_SPUD = {"spuddate"}
_COL_COMPLETION = {"completiondate", "completion"}


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_float(s) -> float | None:
    if s is None or s == "":
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _parse_int(s) -> int | None:
    if s is None or s == "":
        return None
    try:
        return int(re.sub(r"\D", "", str(s)) or "0") or None
    except (TypeError, ValueError):
        return None


def _row_to_well(row: dict, col_index: dict) -> WellRow | None:
    api = row.get(col_index["api"]) if col_index["api"] else None
    if not api:
        return None
    api_str = re.sub(r"\D", "", str(api))
    if len(api_str) < 8:
        return None
    api_str = api_str.zfill(14)
    return WellRow(
        api_no=api_str[:14],
        rrc_district=(row.get(col_index["district"]) or "").strip() or None,
        county_name=(row.get(col_index["county"]) or "").strip().upper() or None,
        operator_p5=_parse_int(row.get(col_index["op_p5"])),
        operator_name=(row.get(col_index["op_name"]) or "").strip() or None,
        lease_name=(row.get(col_index["lease"]) or "").strip() or None,
        well_no=(row.get(col_index["well"]) or "").strip() or None,
        field_name=(row.get(col_index["field"]) or "").strip() or None,
        surface_lat=_parse_float(row.get(col_index["lat"])),
        surface_lon=_parse_float(row.get(col_index["lon"])),
        status=(row.get(col_index["status"]) or "").strip() or None,
        spud_date=_parse_date(row.get(col_index["spud"])),
        completion_date=_parse_date(row.get(col_index["completion"])),
    )


def _build_col_index(headers: list[str]) -> dict[str, str | None]:
    """Map our canonical column names to the actual header strings."""
    by_norm = {_key(h): h for h in headers}
    def pick(candidates: set[str]) -> str | None:
        for c in candidates:
            if c in by_norm:
                return by_norm[c]
        return None
    return {
        "api":        pick(_COL_API),
        "district":   pick(_COL_DISTRICT),
        "county":     pick(_COL_COUNTY),
        "op_p5":      pick(_COL_OP_P5),
        "op_name":    pick(_COL_OP_NAME),
        "lease":      pick(_COL_LEASE),
        "well":       pick(_COL_WELL),
        "field":      pick(_COL_FIELD),
        "lat":        pick(_COL_LAT),
        "lon":        pick(_COL_LON),
        "status":     pick(_COL_STATUS),
        "spud":       pick(_COL_SPUD),
        "completion": pick(_COL_COMPLETION),
    }


def iter_wells_for_counties(path: Path, county_names: set[str]) -> Iterator[WellRow]:
    """Stream-parse the dump, yielding wells whose `county_name` is in the set."""
    county_names_upper = {n.upper() for n in county_names}
    opener = zipfile.ZipFile(path) if zipfile.is_zipfile(path) else None
    if opener is not None:
        # The dump is sometimes a zip-wrapped CSV.
        names = opener.namelist()
        csv_name = next((n for n in names if n.lower().endswith(".csv")), None)
        if not csv_name:
            raise RuntimeError(f"zip {path} contains no .csv")
        stream = io.TextIOWrapper(opener.open(csv_name), encoding="latin-1", errors="replace")
    else:
        stream = open(path, "r", encoding="latin-1", errors="replace", newline="")

    try:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            return
        col_index = _build_col_index(list(reader.fieldnames))
        if not col_index["api"] or not col_index["county"]:
            raise RuntimeError(
                f"CSV missing required columns; headers seen: {list(reader.fieldnames)[:20]}"
            )
        for row in reader:
            county = (row.get(col_index["county"]) or "").strip().upper()
            if county not in county_names_upper:
                continue
            well = _row_to_well(row, col_index)
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
    county_name_to_fips = {v: k for k, v in COUNTY_FIPS_TO_NAME.items()}

    with cursor(dict_rows=False) as cur:
        for w in wells:
            # Ensure the operator exists first (FK)
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

            # Insert the well
            fips = county_name_to_fips.get((w.county_name or "").upper())
            cur.execute(
                """
                INSERT INTO well (api_no, rrc_district, county_fips, operator_p5,
                                  lease_name, well_no, field_name, surface_lat,
                                  surface_lon, status, spud_date, completion_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (api_no) DO UPDATE
                  SET operator_p5 = COALESCE(EXCLUDED.operator_p5, well.operator_p5),
                      status = EXCLUDED.status,
                      lease_name = COALESCE(EXCLUDED.lease_name, well.lease_name),
                      last_seen_at = now()
                """,
                (
                    w.api_no, w.rrc_district, fips, w.operator_p5,
                    w.lease_name, w.well_no, w.field_name,
                    w.surface_lat, w.surface_lon, w.status,
                    w.spud_date, w.completion_date,
                ),
            )
            counts["wells"] += 1
    log.info("ingest complete: %d wells, %d unique operators", counts["wells"], counts["operators"])
    return counts


def ingest_wellbore_dump(path: Path, county_fips_list: list[str]) -> dict[str, int]:
    """High-level: stream-parse the dump filtered to county FIPS list, upsert."""
    county_names = {COUNTY_FIPS_TO_NAME[f] for f in county_fips_list if f in COUNTY_FIPS_TO_NAME}
    if not county_names:
        raise ValueError(f"None of {county_fips_list} are mapped to RRC county names")
    return upsert_wells_and_operators(iter_wells_for_counties(path, county_names))


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True, help="path to OG_WELLBORE_EWA_Report.csv")
    parser.add_argument("--counties", required=True, help="comma-separated county FIPS list")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = ingest_wellbore_dump(Path(args.path), args.counties.split(","))
    print(f"upserted: {result}")
