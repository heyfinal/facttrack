"""ProjectContext — the in-memory snapshot of a project the rules operate on.

We load all the entities a project touches into a single context object so
rule classes don't each re-query the database. This keeps rule code pure +
testable and the DB pressure flat regardless of rule count.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from facttrack.db import cursor

log = logging.getLogger(__name__)


@dataclass
class TractRow:
    id: int
    county_fips: str
    abstract_no: str | None
    survey_name: str | None
    block_no: str | None
    section_no: str | None
    label: str
    gross_acres: float | None
    centroid_lat: float | None
    centroid_lon: float | None
    metadata: dict[str, Any]


@dataclass
class LeasePartyRow:
    id: int
    role: str
    name: str
    fraction_signed: float | None
    is_deceased: bool | None
    metadata: dict[str, Any]


@dataclass
class LeaseRow:
    id: int
    tract_id: int | None
    county_fips: str
    opr_instrument_no: str | None
    recording_date: Any | None
    lessor_text: str | None
    lessee_text: str | None
    effective_date: Any | None
    primary_term_years: float | None
    primary_term_end: Any | None
    royalty_fraction: float | None
    has_pugh_clause: bool | None
    has_retained_acreage: bool | None
    has_continuous_dev: bool | None
    depth_limit_ft: float | None
    raw_clause_text: str | None
    parsed_metadata: dict[str, Any]
    confidence_score: float | None
    parties: list[LeasePartyRow] = field(default_factory=list)


@dataclass
class ChainEventRow:
    id: int
    county_fips: str
    opr_instrument_no: str | None
    recording_date: Any | None
    event_type: str
    grantor_text: str | None
    grantee_text: str | None
    references_lease_id: int | None
    raw_text: str | None
    parsed_metadata: dict[str, Any]


@dataclass
class WellRow:
    api_no: str
    county_fips: str | None
    operator_p5: int | None
    lease_name: str | None
    well_no: str | None
    surface_lat: float | None
    surface_lon: float | None
    spud_date: Any | None
    completion_date: Any | None
    status: str | None
    metadata: dict[str, Any]
    operator_history: list[dict[str, Any]] = field(default_factory=list)
    production_monthly: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class OperatorRow:
    rrc_p5_number: int
    name: str
    address: str | None
    status: str | None
    metadata: dict[str, Any]


@dataclass
class ProjectContext:
    project_id: str
    label: str
    tracts: list[TractRow] = field(default_factory=list)
    leases: list[LeaseRow] = field(default_factory=list)
    chain_events: list[ChainEventRow] = field(default_factory=list)
    wells: list[WellRow] = field(default_factory=list)
    operators_by_p5: dict[int, OperatorRow] = field(default_factory=dict)

    def leases_for_tract(self, tract_id: int) -> list[LeaseRow]:
        return [le for le in self.leases if le.tract_id == tract_id]

    def chain_events_for_lease(self, lease_id: int) -> list[ChainEventRow]:
        return [ev for ev in self.chain_events if ev.references_lease_id == lease_id]

    def wells_for_tract(self, tract_id: int) -> list[WellRow]:
        return [
            w for w in self.wells
            if w.county_fips == self._tract_county(tract_id)
        ]

    def _tract_county(self, tract_id: int) -> str | None:
        for t in self.tracts:
            if t.id == tract_id:
                return t.county_fips
        return None


def load_project(project_id: str) -> ProjectContext:
    """Eager-load everything a project needs."""
    with cursor() as cur:
        cur.execute("SELECT id, label FROM project WHERE id = %s", (project_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"project_id {project_id!r} not found")
        ctx = ProjectContext(project_id=row["id"], label=row["label"])

        cur.execute(
            """
            SELECT t.id, t.county_fips, t.abstract_no, t.survey_name, t.block_no,
                   t.section_no, t.label, t.gross_acres, t.centroid_lat, t.centroid_lon, t.metadata
            FROM project_tract pt
            JOIN tract t ON t.id = pt.tract_id
            WHERE pt.project_id = %s
            """,
            (project_id,),
        )
        ctx.tracts = [TractRow(**dict(r)) for r in cur.fetchall()]

        tract_ids = [t.id for t in ctx.tracts]
        if not tract_ids:
            return ctx

        cur.execute(
            """
            SELECT id, tract_id, county_fips, opr_instrument_no, recording_date,
                   lessor_text, lessee_text, effective_date, primary_term_years,
                   primary_term_end, royalty_fraction, has_pugh_clause,
                   has_retained_acreage, has_continuous_dev, depth_limit_ft,
                   raw_clause_text, parsed_metadata, confidence_score
            FROM lease
            WHERE tract_id = ANY(%s)
            """,
            (tract_ids,),
        )
        leases = [LeaseRow(**dict(r)) for r in cur.fetchall()]

        lease_ids = [le.id for le in leases]
        if lease_ids:
            cur.execute(
                """
                SELECT id, lease_id, role, name, fraction_signed, is_deceased, metadata
                FROM lease_party WHERE lease_id = ANY(%s)
                """,
                (lease_ids,),
            )
            party_map: dict[int, list[LeasePartyRow]] = {}
            for r in cur.fetchall():
                lid = r["lease_id"]
                d = dict(r)
                d.pop("lease_id")
                party_map.setdefault(lid, []).append(LeasePartyRow(**d))
            for le in leases:
                le.parties = party_map.get(le.id, [])
        ctx.leases = leases

        county_set = {t.county_fips for t in ctx.tracts}
        if county_set:
            cur.execute(
                """
                SELECT id, county_fips, opr_instrument_no, recording_date, event_type,
                       grantor_text, grantee_text, references_lease_id, raw_text, parsed_metadata
                FROM chain_event
                WHERE county_fips = ANY(%s)
                ORDER BY recording_date NULLS LAST
                """,
                (list(county_set),),
            )
            ctx.chain_events = [ChainEventRow(**dict(r)) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT api_no, county_fips, operator_p5, lease_name, well_no,
                       surface_lat, surface_lon, spud_date, completion_date, status, metadata
                FROM well
                WHERE county_fips = ANY(%s)
                """,
                (list(county_set),),
            )
            wells = [WellRow(**dict(r)) for r in cur.fetchall()]
            apis = [w.api_no for w in wells]
            if apis:
                cur.execute(
                    """
                    SELECT api_no, operator_p5, effective_date, end_date, source
                    FROM well_operator_history WHERE api_no = ANY(%s)
                    ORDER BY api_no, effective_date
                    """,
                    (apis,),
                )
                hist_map: dict[str, list[dict[str, Any]]] = {}
                for r in cur.fetchall():
                    hist_map.setdefault(r["api_no"], []).append(dict(r))
                cur.execute(
                    """
                    SELECT api_no, period, oil_bbl, gas_mcf, water_bbl, days_on
                    FROM well_production_monthly WHERE api_no = ANY(%s)
                    ORDER BY api_no, period
                    """,
                    (apis,),
                )
                prod_map: dict[str, list[dict[str, Any]]] = {}
                for r in cur.fetchall():
                    prod_map.setdefault(r["api_no"], []).append(dict(r))
                for w in wells:
                    w.operator_history = hist_map.get(w.api_no, [])
                    w.production_monthly = prod_map.get(w.api_no, [])
            ctx.wells = wells

        # Operators referenced by wells
        op_ids = {w.operator_p5 for w in ctx.wells if w.operator_p5}
        op_ids |= {h["operator_p5"] for w in ctx.wells for h in w.operator_history if h.get("operator_p5")}
        if op_ids:
            cur.execute(
                """
                SELECT rrc_p5_number, name, address, status, metadata
                FROM operator WHERE rrc_p5_number = ANY(%s)
                """,
                (list(op_ids),),
            )
            for r in cur.fetchall():
                ctx.operators_by_p5[r["rrc_p5_number"]] = OperatorRow(**dict(r))

    log.info(
        "loaded project=%s tracts=%d leases=%d events=%d wells=%d ops=%d",
        ctx.project_id, len(ctx.tracts), len(ctx.leases),
        len(ctx.chain_events), len(ctx.wells), len(ctx.operators_by_p5),
    )
    return ctx
