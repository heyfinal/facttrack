"""Canonical pydantic data models for FactTrack entities.

These are the wire-format types used by ingestors, the engine, and renderers.
They are NOT 1:1 with the SQL schema — SQL has stricter constraints and audit fields.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "high", "medium", "low"]
AssigneeLevel = Literal["junior_landman", "senior_landman", "attorney_referral", "operator_action"]
EventType = Literal[
    "assignment", "release", "ratification", "extension",
    "top_lease", "aoh", "probate", "rop", "orri_creation", "orri_release", "pooled_unit"
]
PartyRole = Literal["lessor", "lessee", "witness"]
CurativeStatus = Literal["open", "in_progress", "awaiting_doc", "closed", "wontfix"]


class Tract(BaseModel):
    county_fips: str
    abstract_no: str | None = None
    survey_name: str | None = None
    block_no: str | None = None
    section_no: str | None = None
    label: str
    gross_acres: float | None = None
    centroid_lat: float | None = None
    centroid_lon: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Operator(BaseModel):
    rrc_p5_number: int
    name: str
    address: str | None = None
    status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Well(BaseModel):
    api_no: str  # 14-char
    rrc_district: str | None = None
    county_fips: str | None = None
    operator_p5: int | None = None
    lease_name: str | None = None
    well_no: str | None = None
    field_name: str | None = None
    surface_lat: float | None = None
    surface_lon: float | None = None
    spud_date: date | None = None
    completion_date: date | None = None
    status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WellOperatorHistory(BaseModel):
    api_no: str
    operator_p5: int
    effective_date: date
    end_date: date | None = None
    source: str = "rrc_p4"


class ProductionMonthly(BaseModel):
    api_no: str
    period: date
    oil_bbl: float = 0.0
    gas_mcf: float = 0.0
    water_bbl: float = 0.0
    days_on: int | None = None
    source: str = "rrc_pr"


class LeaseParty(BaseModel):
    role: PartyRole
    name: str
    fraction_signed: float | None = None
    is_deceased: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Lease(BaseModel):
    county_fips: str
    opr_volume: str | None = None
    opr_page: str | None = None
    opr_instrument_no: str | None = None
    recording_date: date | None = None
    lessor_text: str | None = None
    lessee_text: str | None = None
    effective_date: date | None = None
    primary_term_years: float | None = None
    primary_term_end: date | None = None
    royalty_fraction: float | None = None
    has_pugh_clause: bool | None = None
    has_retained_acreage: bool | None = None
    has_continuous_dev: bool | None = None
    depth_limit_ft: float | None = None
    raw_clause_text: str | None = None
    parsed_metadata: dict[str, Any] = Field(default_factory=dict)
    confidence_score: float | None = None
    parties: list[LeaseParty] = Field(default_factory=list)


class ChainEvent(BaseModel):
    county_fips: str
    opr_instrument_no: str | None = None
    recording_date: date | None = None
    event_type: EventType
    grantor_text: str | None = None
    grantee_text: str | None = None
    references_lease_id: int | None = None
    raw_text: str | None = None
    parsed_metadata: dict[str, Any] = Field(default_factory=dict)
    confidence_score: float | None = None


class CurativeItem(BaseModel):
    project_id: str
    tract_id: int | None = None
    lease_id: int | None = None
    rule_id: str
    severity: Severity
    confidence_score: float
    dollar_impact_low: float | None = None
    dollar_impact_high: float | None = None
    title: str
    description: str
    suggested_action: str
    assignee_level: AssigneeLevel | None = None
    status: CurativeStatus = "open"
    deadline: date | None = None
    related_events: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    detected_at: datetime = Field(default_factory=datetime.utcnow)


__all__ = [
    "Tract", "Operator", "Well", "WellOperatorHistory", "ProductionMonthly",
    "Lease", "LeaseParty", "ChainEvent", "CurativeItem",
    "Severity", "AssigneeLevel", "EventType", "PartyRole", "CurativeStatus",
]
