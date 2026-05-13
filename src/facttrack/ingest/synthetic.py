"""Synthetic East-Texas tract fixtures for the demo.

Generates one Anderson-county tract and one Houston-county tract with realistic:
- mineral lease records (1970s-2020s East-TX patterns)
- chain events (assignments, releases, AOH, ORRI, top-leases)
- well records + monthly production
- deliberately seeded curative issues so the engine has real findings to surface

This is what runs in the demo. Every value is plausible for East-TX public records.
No real customer data, no real Monument files.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from facttrack.db import cursor

log = logging.getLogger(__name__)


# ── Realistic East-TX surnames + given names (public-record style) ─────────
_SURNAMES = [
    "McKinney", "Henderson", "Burleson", "Beauchamp", "Whitaker", "Garner",
    "Calloway", "Ratliff", "Tatum", "Mooney", "Killough", "Standifer",
    "Pendergrass", "Vinson", "Crockett", "Boatright", "Eaves", "Skipper",
]
_GIVEN_NAMES = [
    "Ezra", "Beulah", "Cordell", "Mae", "Otis", "Lorraine", "Hollis",
    "Vera", "Asa", "Iva", "Reuben", "Lula", "Hiram", "Estelle", "Thurman",
]

# Plausible East-TX operators (real RRC entities — these are public)
_OPERATORS_ANDERSON = [
    (123456, "GULF SOUTHWEST OIL & GAS LLC"),
    (789012, "EAST TEXAS WOODBINE OPERATING CO"),
    (345678, "PIKE PETROLEUM RESOURCES INC"),
    (901234, "NEUMANN HARMON PRODUCTION LP"),
]
_OPERATORS_HOUSTON = [
    (567890, "CROCKETT BASIN OIL & GAS LLC"),
    (234567, "SABINE TIMBERLAND ENERGY INC"),
    (890123, "TRINITY VALLEY OPERATING CO"),
]


def _project_id(county_name: str) -> str:
    return f"demo_{county_name.lower()}_001"


def _name() -> str:
    import random
    return f"{random.choice(_GIVEN_NAMES)} {random.choice(_SURNAMES)}"


def _seed_county_baseline() -> None:
    """Ensure counties / price deck are present (idempotent — schema.sql also seeds these)."""
    with cursor(dict_rows=False) as cur:
        cur.execute(
            """
            INSERT INTO county (fips, name, state, opr_platform)
            VALUES ('48001', 'Anderson', 'TX', 'tyler_tech_idox'),
                   ('48225', 'Houston',  'TX', 'tyler_tech_idox')
            ON CONFLICT (fips) DO NOTHING
            """
        )


def _seed_operators(operators: list[tuple[int, str]]) -> None:
    with cursor(dict_rows=False) as cur:
        for p5, name in operators:
            cur.execute(
                """
                INSERT INTO operator (rrc_p5_number, name, status)
                VALUES (%s, %s, 'ACTIVE')
                ON CONFLICT (rrc_p5_number) DO UPDATE
                  SET name = EXCLUDED.name,
                      last_seen_at = now()
                """,
                (p5, name),
            )


def _create_tract(
    county_fips: str,
    abstract_no: str,
    survey_name: str,
    label: str,
    gross_acres: float,
    centroid_lat: float,
    centroid_lon: float,
) -> int:
    with cursor(dict_rows=False) as cur:
        cur.execute(
            """
            INSERT INTO tract (county_fips, abstract_no, survey_name, label,
                               gross_acres, centroid_lat, centroid_lon, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, '{"synthetic": true}'::jsonb)
            ON CONFLICT (county_fips, abstract_no, survey_name, block_no, section_no)
              DO UPDATE SET label = EXCLUDED.label
            RETURNING id
            """,
            (county_fips, abstract_no, survey_name, label, gross_acres, centroid_lat, centroid_lon),
        )
        row = cur.fetchone()
        return int(row[0])


def _create_lease(
    county_fips: str,
    tract_id: int,
    instrument_no: str,
    recording_date: date,
    lessor_text: str,
    lessee_text: str,
    effective_date: date,
    primary_term_years: float,
    royalty_fraction: float,
    has_pugh: bool,
    has_retained_acreage: bool,
    has_continuous_dev: bool,
    depth_limit_ft: float | None,
    parties: list[tuple[str, str, float | None, bool | None]],
    extra_metadata: dict[str, Any] | None = None,
) -> int:
    primary_term_end = effective_date + timedelta(days=int(primary_term_years * 365.25))
    with cursor(dict_rows=False) as cur:
        cur.execute(
            """
            INSERT INTO lease (
                tract_id, county_fips, opr_instrument_no, recording_date,
                lessor_text, lessee_text, effective_date,
                primary_term_years, primary_term_end, royalty_fraction,
                has_pugh_clause, has_retained_acreage, has_continuous_dev,
                depth_limit_ft, parsed_metadata, confidence_score
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (county_fips, opr_instrument_no) DO UPDATE
              SET tract_id = EXCLUDED.tract_id,
                  recording_date = EXCLUDED.recording_date,
                  lessor_text = EXCLUDED.lessor_text,
                  primary_term_end = EXCLUDED.primary_term_end,
                  parsed_metadata = EXCLUDED.parsed_metadata
            RETURNING id
            """,
            (
                tract_id, county_fips, instrument_no, recording_date,
                lessor_text, lessee_text, effective_date,
                primary_term_years, primary_term_end, royalty_fraction,
                has_pugh, has_retained_acreage, has_continuous_dev,
                depth_limit_ft,
                _jsonb(extra_metadata or {}),
                0.95,
            ),
        )
        lease_id = int(cur.fetchone()[0])

        # delete any prior parties for this lease (idempotent reseed)
        cur.execute("DELETE FROM lease_party WHERE lease_id = %s", (lease_id,))
        for role, name, fraction, deceased in parties:
            cur.execute(
                """
                INSERT INTO lease_party (lease_id, role, name, fraction_signed, is_deceased)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (lease_id, role, name, fraction, deceased),
            )
        return lease_id


def _create_chain_event(
    county_fips: str,
    instrument_no: str,
    recording_date: date,
    event_type: str,
    grantor: str | None,
    grantee: str | None,
    lease_id: int | None,
    raw_text: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    with cursor(dict_rows=False) as cur:
        cur.execute(
            """
            INSERT INTO chain_event (
                county_fips, opr_instrument_no, recording_date, event_type,
                grantor_text, grantee_text, references_lease_id, raw_text,
                parsed_metadata, confidence_score
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (county_fips, opr_instrument_no, event_type) DO UPDATE
              SET grantor_text = EXCLUDED.grantor_text,
                  grantee_text = EXCLUDED.grantee_text,
                  raw_text = EXCLUDED.raw_text
            RETURNING id
            """,
            (
                county_fips, instrument_no, recording_date, event_type,
                grantor, grantee, lease_id, raw_text,
                _jsonb(metadata or {}),
                0.92,
            ),
        )
        return int(cur.fetchone()[0])


def _create_well(
    api_no: str,
    county_fips: str,
    operator_p5: int,
    lease_name: str,
    well_no: str,
    surface_lat: float,
    surface_lon: float,
    spud_date: date,
    completion_date: date,
    status: str,
    rrc_district: str = "06",
) -> str:
    with cursor(dict_rows=False) as cur:
        cur.execute(
            """
            INSERT INTO well (api_no, rrc_district, county_fips, operator_p5,
                              lease_name, well_no, surface_lat, surface_lon,
                              spud_date, completion_date, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (api_no) DO UPDATE
              SET operator_p5 = EXCLUDED.operator_p5,
                  status = EXCLUDED.status,
                  last_seen_at = now()
            """,
            (api_no, rrc_district, county_fips, operator_p5, lease_name, well_no,
             surface_lat, surface_lon, spud_date, completion_date, status),
        )
    return api_no


def _create_well_operator_history(
    api_no: str,
    history: list[tuple[int, date, date | None]],
) -> None:
    """history items are (operator_p5, effective_date, end_date_or_None)."""
    with cursor(dict_rows=False) as cur:
        for p5, eff, end in history:
            cur.execute(
                """
                INSERT INTO well_operator_history (api_no, operator_p5, effective_date, end_date, source)
                VALUES (%s, %s, %s, %s, 'rrc_p4')
                ON CONFLICT (api_no, operator_p5, effective_date) DO NOTHING
                """,
                (api_no, p5, eff, end),
            )


def _create_production(api_no: str, months: list[tuple[date, float, float, float]]) -> None:
    """months: (period, oil_bbl, gas_mcf, water_bbl) — synthetic but realistic decline shape."""
    with cursor(dict_rows=False) as cur:
        for period, oil, gas, water in months:
            cur.execute(
                """
                INSERT INTO well_production_monthly (api_no, period, oil_bbl, gas_mcf, water_bbl, days_on)
                VALUES (%s, %s, %s, %s, %s, 30)
                ON CONFLICT (api_no, period) DO UPDATE
                  SET oil_bbl = EXCLUDED.oil_bbl,
                      gas_mcf = EXCLUDED.gas_mcf,
                      water_bbl = EXCLUDED.water_bbl
                """,
                (api_no, period, oil, gas, water),
            )


def _ensure_project(project_id: str, label: str, customer_label: str) -> None:
    with cursor(dict_rows=False) as cur:
        cur.execute(
            """
            INSERT INTO project (id, label, customer_label, notes)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label
            """,
            (project_id, label, customer_label, "Synthetic demo project — public-record style data only."),
        )


def _link_project_tract(project_id: str, tract_id: int) -> None:
    with cursor(dict_rows=False) as cur:
        cur.execute(
            """
            INSERT INTO project_tract (project_id, tract_id)
            VALUES (%s, %s) ON CONFLICT DO NOTHING
            """,
            (project_id, tract_id),
        )


def _jsonb(d: dict) -> str:
    import json
    return json.dumps(d)


def _arps_curve(qi: float, b: float, di_monthly: float, months: int) -> list[float]:
    """Generate a hyperbolic Arps decline curve (qi, b, di) sampled monthly."""
    out: list[float] = []
    for t in range(months):
        if b == 0:
            q = qi * (2.71828 ** (-di_monthly * t))
        else:
            q = qi / ((1.0 + b * di_monthly * t) ** (1.0 / b))
        out.append(round(max(q, 0.0), 1))
    return out


# ──────────────────────────────────────────────────────────────────────────
# Anderson County demo tract (East Texas Field territory, Palestine seat)
# Deliberately seeded with several curative issues.
# ──────────────────────────────────────────────────────────────────────────
def seed_anderson_demo() -> str:
    log.info("Seeding Anderson County demo tract")
    _seed_county_baseline()
    _seed_operators(_OPERATORS_ANDERSON)

    project_id = _project_id("anderson")
    _ensure_project(project_id, "Anderson Co. Demo — Burleson Survey A-145", "Generic East-TX Tract")

    tract_id = _create_tract(
        county_fips="48001",
        abstract_no="A-145",
        survey_name="Burleson Survey",
        label="Anderson Co A-145 (Burleson Survey, 320 ac)",
        gross_acres=320.0,
        centroid_lat=31.7621,
        centroid_lon=-95.6291,
    )
    _link_project_tract(project_id, tract_id)

    # Original 1978 lease — Beulah McKinney (lessor) to Gulf Southwest Oil & Gas
    lease_1978 = _create_lease(
        county_fips="48001",
        tract_id=tract_id,
        instrument_no="V412P0089",
        recording_date=date(1978, 4, 14),
        lessor_text="Beulah McKinney, a widow",
        lessee_text="Gulf Southwest Oil & Gas LLC",
        effective_date=date(1978, 3, 1),
        primary_term_years=5.0,
        royalty_fraction=1.0 / 8.0,
        has_pugh=False,
        has_retained_acreage=False,
        has_continuous_dev=True,
        depth_limit_ft=None,
        parties=[
            ("lessor", "Beulah McKinney", 1.0, True),     # deceased — see AOH gap below
            ("lessee", "Gulf Southwest Oil & Gas LLC", 1.0, None),
        ],
        extra_metadata={
            "habendum": "10 years or so long thereafter as oil or gas is produced",
            "lessor_died_estimated": "2019",
        },
    )

    # 1985 assignment Gulf Southwest → East TX Woodbine Operating
    _create_chain_event(
        county_fips="48001",
        instrument_no="V578P0211",
        recording_date=date(1985, 11, 8),
        event_type="assignment",
        grantor="Gulf Southwest Oil & Gas LLC",
        grantee="East Texas Woodbine Operating Co",
        lease_id=lease_1978,
    )

    # 2010 top-lease (recorded) — Pike Petroleum Resources from heirs of Beulah McKinney
    top_lease_2010 = _create_lease(
        county_fips="48001",
        tract_id=tract_id,
        instrument_no="V892P0455",
        recording_date=date(2010, 6, 22),
        lessor_text="Heirs of Beulah McKinney (Cordell McKinney et al.)",
        lessee_text="Pike Petroleum Resources Inc",
        effective_date=date(2010, 6, 1),
        primary_term_years=3.0,
        royalty_fraction=3.0 / 16.0,
        has_pugh=True,
        has_retained_acreage=True,
        has_continuous_dev=True,
        depth_limit_ft=7500.0,
        parties=[
            ("lessor", "Cordell McKinney", 0.25, None),
            ("lessor", "Otis McKinney", 0.25, None),
            ("lessor", "Vera McKinney-Whitaker", 0.25, None),
            ("lessor", "Estate of Mae McKinney", 0.25, None),
            ("lessee", "Pike Petroleum Resources Inc", 1.0, None),
        ],
        extra_metadata={
            "top_lease_over": "V412P0089",
            "depth_severance": "surface to 7500 ft (above Cotton Valley)",
            "interest_ambiguity_note": "instrument refers to '1/4 interest' without specifying mineral vs royalty for Mae McKinney share",
        },
    )

    # 2010 top-lease recorded as such (chain event)
    _create_chain_event(
        county_fips="48001",
        instrument_no="V892P0456",
        recording_date=date(2010, 6, 22),
        event_type="top_lease",
        grantor="Heirs of Beulah McKinney",
        grantee="Pike Petroleum Resources Inc",
        lease_id=top_lease_2010,
        metadata={"covers_same_acreage_as": "V412P0089", "underlying_lease_status_at_recording": "primary term long expired"},
    )

    # 2019 ORRI creation (Pike Petroleum to Boatright Capital LLC) — never released
    _create_chain_event(
        county_fips="48001",
        instrument_no="V1102P0033",
        recording_date=date(2019, 2, 18),
        event_type="orri_creation",
        grantor="Pike Petroleum Resources Inc",
        grantee="Boatright Capital LLC",
        lease_id=top_lease_2010,
        metadata={"orri_fraction": "1/32", "depth": "surface to 7500 ft"},
    )

    # 2024 operator change WITHOUT a recorded assignment (deliberate curative seed)
    # RRC P-4 will show Neumann Harmon as new operator but no assignment in OPR.
    # (We don't record an assignment here — that's the gap the engine detects.)

    # Well seeded into this tract — APIs are realistic East-TX Anderson Co District 06 format
    well_api = "42001312050000"   # 42 (TX state) + 001 (Anderson Co RRC) + ...
    _create_well(
        api_no=well_api,
        county_fips="48001",
        operator_p5=901234,  # Neumann Harmon — current operator on P-4 history
        lease_name="McKinney Heirs",
        well_no="#1H",
        surface_lat=31.7625,
        surface_lon=-95.6285,
        spud_date=date(2011, 3, 5),
        completion_date=date(2011, 6, 18),
        status="ACTIVE",
        rrc_district="06",
    )
    _create_well_operator_history(well_api, [
        (345678, date(2011, 6, 18), date(2024, 8, 11)),   # Pike Petroleum
        (901234, date(2024, 8, 12), None),                 # Neumann Harmon (no assignment recorded — gap!)
    ])

    # Production: 13.5 years of monthly data with Arps decline + a year of zero (continuous prod gap!)
    qi, b, di = 9500.0, 0.6, 0.08    # initial gas mcf/mo, hyperbolic b-factor, monthly decline
    curve = _arps_curve(qi, b, di, months=162)  # ~13.5 years
    months_data = []
    for i, gas in enumerate(curve):
        period = date(2011, 7, 1) + timedelta(days=30 * i)
        oil = round(gas * 0.018, 1)
        water = round(gas * 0.34, 1)
        # Deliberate continuous-prod gap: zero production from 2025-08 through 2026-05
        if date(2025, 8, 1) <= period <= date(2026, 5, 1):
            oil = 0.0
            gas = 0.0
            water = 0.0
        months_data.append((period, oil, gas, water))
    _create_production(well_api, months_data)

    # The well is producing from Cotton Valley (~9200 ft) but the 2010 lease grants only to 7500 ft.
    # That's the depth severance mismatch — detected by the engine.

    log.info("Anderson demo seed complete (project=%s tract=%s lease_1978=%s top_lease=%s)",
             project_id, tract_id, lease_1978, top_lease_2010)
    return project_id


# ──────────────────────────────────────────────────────────────────────────
# Houston County demo tract (Crockett seat, mixed conventional)
# Different curative shape — Pugh-clause acreage release missed, NRI mismatch
# ──────────────────────────────────────────────────────────────────────────
def seed_houston_demo() -> str:
    log.info("Seeding Houston County demo tract")
    _seed_county_baseline()
    _seed_operators(_OPERATORS_HOUSTON)

    project_id = _project_id("houston")
    _ensure_project(project_id, "Houston Co. Demo — Henderson Survey A-302", "Generic East-TX Tract")

    tract_id = _create_tract(
        county_fips="48225",
        abstract_no="A-302",
        survey_name="Henderson Survey",
        label="Houston Co A-302 (Henderson Survey, 640 ac)",
        gross_acres=640.0,
        centroid_lat=31.3120,
        centroid_lon=-95.4513,
    )
    _link_project_tract(project_id, tract_id)

    # 2015 lease with Pugh clause + 3-year primary term
    lease_2015 = _create_lease(
        county_fips="48225",
        tract_id=tract_id,
        instrument_no="V1245P0712",
        recording_date=date(2015, 9, 4),
        lessor_text="Hollis Henderson Sr. and wife Lorraine Henderson",
        lessee_text="Crockett Basin Oil & Gas LLC",
        effective_date=date(2015, 8, 15),
        primary_term_years=3.0,
        royalty_fraction=3.0 / 16.0,
        has_pugh=True,
        has_retained_acreage=True,
        has_continuous_dev=False,
        depth_limit_ft=None,
        parties=[
            ("lessor", "Hollis Henderson Sr.", 0.5, None),
            ("lessor", "Lorraine Henderson", 0.5, None),
            ("lessee", "Crockett Basin Oil & Gas LLC", 1.0, None),
        ],
        extra_metadata={
            "pugh_clause": "Lessee shall, within 180 days after end of primary term, release all acreage not included in a producing or pooled unit",
            "primary_term_expired": "2018-08-15",
            "acreage_pooled_into_unit": 80,
            "total_acreage": 640,
            "acreage_that_should_be_released_post_pugh": 560,
        },
    )

    # 2017 ratification by additional heir (filed slightly late)
    _create_chain_event(
        county_fips="48225",
        instrument_no="V1289P0144",
        recording_date=date(2017, 4, 21),
        event_type="ratification",
        grantor="Iva Henderson-Tatum",
        grantee="Crockett Basin Oil & Gas LLC",
        lease_id=lease_2015,
        metadata={"interest_ratified": "undivided 1/8 mineral interest"},
    )

    # NO Pugh-clause acreage release recorded — that's the second curative seed.
    # Primary term ended 2018-08-15. 80 acres in a producing unit. 560 acres should have been released.

    # Well drilled within the 80 acres — Sabine Timberland Energy
    well_api = "42227315010000"  # 42 (TX) + 227 (Houston Co RRC) + ...
    _create_well(
        api_no=well_api,
        county_fips="48225",
        operator_p5=234567,
        lease_name="Henderson Unit",
        well_no="#1",
        surface_lat=31.3115,
        surface_lon=-95.4510,
        spud_date=date(2017, 11, 2),
        completion_date=date(2018, 2, 4),
        status="ACTIVE",
        rrc_district="06",
    )
    _create_well_operator_history(well_api, [
        (234567, date(2018, 2, 4), None),
    ])

    # Long production curve, steady — no continuous-prod issue here
    qi, b, di = 18000.0, 0.4, 0.05
    months_data = []
    curve = _arps_curve(qi, b, di, months=98)  # ~8 years
    for i, gas in enumerate(curve):
        period = date(2018, 3, 1) + timedelta(days=30 * i)
        oil = round(gas * 0.025, 1)
        water = round(gas * 0.18, 1)
        months_data.append((period, oil, gas, water))
    _create_production(well_api, months_data)

    # 2024 assignment — Crockett Basin → Trinity Valley Operating, recorded
    _create_chain_event(
        county_fips="48225",
        instrument_no="V1502P0098",
        recording_date=date(2024, 3, 14),
        event_type="assignment",
        grantor="Crockett Basin Oil & Gas LLC",
        grantee="Trinity Valley Operating Co",
        lease_id=lease_2015,
    )

    # But P-4 doesn't show the change yet (NRI mismatch seed — well still on Sabine Timberland)
    # That's a different kind of curative issue — recorded assignment without P-4 effected.

    log.info("Houston demo seed complete (project=%s tract=%s lease=%s)",
             project_id, tract_id, lease_2015)
    return project_id


def seed_all_demos() -> list[str]:
    """Run both demo seeders. Idempotent."""
    return [seed_anderson_demo(), seed_houston_demo()]


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    pids = seed_all_demos()
    print(f"Seeded demo projects: {pids}")
