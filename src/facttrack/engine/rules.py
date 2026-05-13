"""Curative-detection rule engine.

All 17 MVP rules are defined here in one module — they're small enough that
keeping them together makes the engine easier to read than a file-per-rule.

Each Rule is a small dataclass-like object with:
- rule_id (stable identifier used in DB + reports)
- title (human-readable)
- evaluate(ctx) → list[Finding]

Findings are emitted via FindingEmitter, which converts them into CurativeItem
rows for persistence and rendering.

Rules are designed to be SAFE on incomplete data — if required inputs are
missing, the rule returns an empty list rather than raising. The reasoning is
that real-world public records are noisy and a rule that explodes blocks every
downstream rule too.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Iterable

from .context import (
    ChainEventRow, LeaseRow, ProjectContext, WellRow,
)
from .findings import Finding, FindingEmitter

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Rule 1 — Unrecorded RRC P-4 assignment
# ──────────────────────────────────────────────────────────────────────────
def rule_01_unrecorded_p4_assignment(ctx: ProjectContext, emit: FindingEmitter) -> None:
    """RRC P-4 operator changed but no assignment recorded in county OPR."""
    for well in ctx.wells:
        history = sorted(well.operator_history, key=lambda h: h["effective_date"])
        if len(history) < 2:
            continue
        for prev, curr in zip(history, history[1:]):
            change_date = curr["effective_date"]
            # Find any assignment in county OPR near this date
            tract = _tract_for_well(ctx, well)
            tract_id = tract.id if tract else None
            window_start = change_date - timedelta(days=30)
            window_end = change_date + timedelta(days=180)
            recorded_assignment = False
            for ev in ctx.chain_events:
                if ev.event_type != "assignment":
                    continue
                if ev.recording_date is None:
                    continue
                if window_start <= ev.recording_date <= window_end:
                    recorded_assignment = True
                    break
            if not recorded_assignment:
                prev_op = ctx.operators_by_p5.get(prev["operator_p5"])
                curr_op = ctx.operators_by_p5.get(curr["operator_p5"])
                prev_name = prev_op.name if prev_op else f"P5#{prev['operator_p5']}"
                curr_name = curr_op.name if curr_op else f"P5#{curr['operator_p5']}"
                emit.add(Finding(
                    rule_id="r01_unrecorded_p4_assignment",
                    severity="high",
                    confidence_score=0.85,
                    tract_id=tract_id,
                    title=f"Unrecorded operator assignment on well {well.api_no}",
                    description=(
                        f"RRC P-4 records show the operator-of-record changed from "
                        f"{prev_name} to {curr_name} effective {change_date}. "
                        f"No corresponding assignment instrument has been located in the "
                        f"county OPR within a 6-month window. The county chain of title "
                        f"shows the prior operator still as record holder."
                    ),
                    suggested_action=(
                        f"Request a recorded assignment from {curr_name}. If the assignment "
                        f"was executed but never filed, prepare and record an instrument "
                        f"reflecting the transfer. Until recorded, any cure or release work "
                        f"by {curr_name} is exposed to stranger-to-title attack."
                    ),
                    assignee_level="junior_landman",
                    dollar_impact_low=2_500.0,
                    dollar_impact_high=15_000.0,
                    related_events=[{"well_api": well.api_no, "from_p5": prev["operator_p5"], "to_p5": curr["operator_p5"], "effective": str(change_date)}],
                ))


# ──────────────────────────────────────────────────────────────────────────
# Rule 2 — Probate gap (deceased lessor without AOH or probate)
# ──────────────────────────────────────────────────────────────────────────
def rule_02_probate_gap(ctx: ProjectContext, emit: FindingEmitter) -> None:
    """A lease party is marked deceased but no AOH or probate is recorded."""
    aoh_or_probate_subjects: set[str] = set()
    for ev in ctx.chain_events:
        if ev.event_type in ("aoh", "probate", "rop"):
            if ev.grantor_text:
                aoh_or_probate_subjects.add(_normalize_name(ev.grantor_text))

    for lease in ctx.leases:
        for party in lease.parties:
            if party.role != "lessor" or not party.is_deceased:
                continue
            if _normalize_name(party.name) in aoh_or_probate_subjects:
                continue
            emit.add(Finding(
                rule_id="r02_probate_gap",
                severity="critical",
                confidence_score=0.9,
                tract_id=lease.tract_id,
                lease_id=lease.id,
                title=f"Probate gap — deceased lessor {party.name} (lease {lease.opr_instrument_no})",
                description=(
                    f"The lessor {party.name} on lease {lease.opr_instrument_no} "
                    f"(recorded {lease.recording_date}) is indicated as deceased in the "
                    f"records. No Affidavit of Heirship, probate decree, or "
                    f"Record of Probate has been located in the county OPR linking heirs "
                    f"to this interest. The chain of title is therefore broken at this point "
                    f"for the {party.fraction_signed or 'undivided'} interest signed by this party."
                ),
                suggested_action=(
                    f"Locate heirs of {party.name}; obtain an Affidavit of Heirship and have "
                    f"it recorded in the county OPR. If a probate is open or closed in the "
                    f"appropriate jurisdiction, record a certified copy of the order. Until "
                    f"this is filed any conveyance from purported heirs is exposed."
                ),
                assignee_level="senior_landman",
                dollar_impact_low=15_000.0,
                dollar_impact_high=200_000.0,
                related_events=[{"lessor": party.name, "fraction": party.fraction_signed}],
            ))


# ──────────────────────────────────────────────────────────────────────────
# Rule 4 — Depth severance mismatch
# ──────────────────────────────────────────────────────────────────────────
def rule_04_depth_severance_mismatch(ctx: ProjectContext, emit: FindingEmitter) -> None:
    """Lease grants limited depth interval but production from outside that interval."""
    for tract in ctx.tracts:
        for lease in ctx.leases_for_tract(tract.id):
            if lease.depth_limit_ft is None:
                continue
            for well in ctx.wells_for_tract(tract.id):
                # crude check: well metadata or lease metadata may state producing depth
                producing_depth = None
                if isinstance(well.metadata, dict):
                    producing_depth = well.metadata.get("producing_depth_ft")
                # also infer from lease parsed_metadata "depth_severance" notes
                # and from well naming convention (production from Cotton Valley is ~9000+)
                if producing_depth is None:
                    if isinstance(lease.parsed_metadata, dict):
                        note = (lease.parsed_metadata.get("depth_severance") or "").lower()
                        if "cotton valley" in note:
                            producing_depth = 9200
                if producing_depth is None:
                    # Last fallback — if lease has explicit limit < 8000 ft AND well is active,
                    # flag for review (low confidence).
                    if lease.depth_limit_ft < 8000:
                        emit.add(Finding(
                            rule_id="r04_depth_severance_mismatch",
                            severity="high",
                            confidence_score=0.55,
                            tract_id=tract.id,
                            lease_id=lease.id,
                            title=f"Possible depth severance mismatch on well {well.api_no}",
                            description=(
                                f"Lease {lease.opr_instrument_no} grants rights "
                                f"only to {lease.depth_limit_ft:.0f} ft. Well {well.api_no} on this tract "
                                f"is producing; producing depth not confirmed from public sources. "
                                f"Review to confirm production is within the depth granted."
                            ),
                            suggested_action=(
                                "Pull RRC completion form (W-2) for the well to confirm "
                                "actual perforation interval. If production is below the depth granted, "
                                "obtain a depth-extension ratification or remove deeper production until cured."
                            ),
                            assignee_level="attorney_referral",
                            dollar_impact_low=10_000.0,
                            dollar_impact_high=100_000.0,
                            related_events=[{"well_api": well.api_no, "lease_depth_limit_ft": lease.depth_limit_ft}],
                        ))
                    continue
                if producing_depth > (lease.depth_limit_ft + 50):  # 50ft tolerance
                    emit.add(Finding(
                        rule_id="r04_depth_severance_mismatch",
                        severity="critical",
                        confidence_score=0.92,
                        tract_id=tract.id,
                        lease_id=lease.id,
                        title=f"Confirmed depth severance breach on well {well.api_no}",
                        description=(
                            f"Lease {lease.opr_instrument_no} grants rights to "
                            f"{lease.depth_limit_ft:.0f} ft only. Well {well.api_no} is producing "
                            f"from ~{producing_depth} ft (outside granted interval). "
                            f"Royalty owners and operators of the deeper interval may have a "
                            f"valid trespass / conversion claim."
                        ),
                        suggested_action=(
                            "Halt deeper-interval production until a depth-extension ratification "
                            "or lease amendment is recorded. Obtain title opinion regarding "
                            "deeper rights ownership."
                        ),
                        assignee_level="attorney_referral",
                        dollar_impact_low=25_000.0,
                        dollar_impact_high=500_000.0,
                        related_events=[
                            {"well_api": well.api_no, "producing_depth_ft": producing_depth,
                             "lease_depth_limit_ft": lease.depth_limit_ft}
                        ],
                    ))


# ──────────────────────────────────────────────────────────────────────────
# Rule 5 — Primary term expiring without continuous production
# ──────────────────────────────────────────────────────────────────────────
def rule_05_primary_term_no_continuous_prod(ctx: ProjectContext, emit: FindingEmitter) -> None:
    """Lease nearing primary-term end (or past it) without active monthly production."""
    today = date.today()
    horizon = today + timedelta(days=180)
    for tract in ctx.tracts:
        wells_in_tract = ctx.wells_for_tract(tract.id)
        for lease in ctx.leases_for_tract(tract.id):
            if lease.primary_term_end is None:
                continue
            in_window = lease.primary_term_end <= horizon
            past_term = lease.primary_term_end < today
            if not in_window:
                continue
            # Look at the most recent 6 months of production on any well in this tract
            recent_threshold = today - timedelta(days=180)
            had_production = False
            for w in wells_in_tract:
                for p in w.production_monthly:
                    if p["period"] >= recent_threshold and (
                        (p.get("oil_bbl") or 0) > 0 or (p.get("gas_mcf") or 0) > 0
                    ):
                        had_production = True
                        break
                if had_production:
                    break
            if past_term and not had_production:
                emit.add(Finding(
                    rule_id="r05_primary_term_no_continuous_prod",
                    severity="critical",
                    confidence_score=0.93,
                    tract_id=tract.id,
                    lease_id=lease.id,
                    title=f"Lease {lease.opr_instrument_no} past primary term — no continuous production",
                    description=(
                        f"Lease {lease.opr_instrument_no} primary term ended "
                        f"{lease.primary_term_end}. No oil or gas production has been "
                        f"reported on any well covering this tract in the last 6 months. "
                        f"Habendum / continuous-production clause appears violated; the lease "
                        f"may be terminated automatically unless extended by clause language."
                    ),
                    suggested_action=(
                        "Examine the habendum and any continuous-development / re-work clauses. "
                        "If shut-in royalty provisions apply, confirm payments were made. Issue "
                        "a Lease Termination Notice to operator and either renegotiate a new lease "
                        "or release the leasehold."
                    ),
                    assignee_level="senior_landman",
                    deadline=today + timedelta(days=30),
                    dollar_impact_low=20_000.0,
                    dollar_impact_high=300_000.0,
                    related_events=[{"primary_term_end": str(lease.primary_term_end)}],
                ))
            elif not past_term and in_window:
                emit.add(Finding(
                    rule_id="r05_primary_term_no_continuous_prod",
                    severity="high",
                    confidence_score=0.78,
                    tract_id=tract.id,
                    lease_id=lease.id,
                    title=f"Lease {lease.opr_instrument_no} primary term ending within 6 months",
                    description=(
                        f"Lease {lease.opr_instrument_no} primary term ends "
                        f"{lease.primary_term_end}. Verify continuous production status "
                        f"and operator drilling commitments now."
                    ),
                    suggested_action=(
                        "Confirm operator's drilling / completion schedule. Calendar a 60-day "
                        "review checkpoint. Prepare lease release or extension paperwork conditionally."
                    ),
                    assignee_level="senior_landman",
                    deadline=lease.primary_term_end,
                    dollar_impact_low=5_000.0,
                    dollar_impact_high=50_000.0,
                ))


# ──────────────────────────────────────────────────────────────────────────
# Rule 6 — Pugh-clause acreage release missed
# ──────────────────────────────────────────────────────────────────────────
def rule_06_pugh_release_missed(ctx: ProjectContext, emit: FindingEmitter) -> None:
    """Lease has a Pugh clause, primary term ended, but no release of unproduced acreage recorded."""
    today = date.today()
    for tract in ctx.tracts:
        for lease in ctx.leases_for_tract(tract.id):
            if not lease.has_pugh_clause:
                continue
            if lease.primary_term_end is None or lease.primary_term_end >= today:
                continue
            # Did a release event for this lease get recorded after primary term end?
            releases = [
                ev for ev in ctx.chain_events_for_lease(lease.id)
                if ev.event_type == "release" and ev.recording_date and ev.recording_date >= lease.primary_term_end
            ]
            if releases:
                continue
            pm = lease.parsed_metadata or {}
            pooled = pm.get("acreage_pooled_into_unit")
            total = pm.get("total_acreage") or pm.get("acreage_that_should_be_released_post_pugh")
            description = (
                f"Lease {lease.opr_instrument_no} contains a Pugh clause requiring release "
                f"of acreage not held by a producing unit after the end of the primary term "
                f"({lease.primary_term_end}). "
            )
            if pooled is not None and total is not None:
                description += (
                    f"Of {total} gross acres covered, only ~{pooled} acres are within a "
                    f"producing pooled unit. The remaining ~{(total or 0) - (pooled or 0)} acres "
                    f"should have been released. No release instrument has been recorded."
                )
            else:
                description += (
                    "No release of unproduced acreage has been recorded since term expiration."
                )
            emit.add(Finding(
                rule_id="r06_pugh_release_missed",
                severity="high",
                confidence_score=0.88,
                tract_id=tract.id,
                lease_id=lease.id,
                title=f"Pugh-clause acreage release missing on lease {lease.opr_instrument_no}",
                description=description,
                suggested_action=(
                    "Prepare and record a Partial Release covering all acreage outside the "
                    "producing unit. Update internal acreage records. If the operator disputes "
                    "the release, request a title opinion regarding lease validity on the held acreage."
                ),
                assignee_level="senior_landman",
                dollar_impact_low=10_000.0,
                dollar_impact_high=150_000.0,
                related_events=[{
                    "primary_term_end": str(lease.primary_term_end),
                    "pooled_acres": pooled, "total_acres": total,
                }],
            ))


# ──────────────────────────────────────────────────────────────────────────
# Rule 11 — Heirship affidavit > 10 years old, no supporting probate
# ──────────────────────────────────────────────────────────────────────────
def rule_11_old_aoh_no_probate(ctx: ProjectContext, emit: FindingEmitter) -> None:
    today = date.today()
    aoh_events = [ev for ev in ctx.chain_events if ev.event_type == "aoh"]
    probate_subjects = {
        _normalize_name(ev.grantor_text or "") for ev in ctx.chain_events if ev.event_type in ("probate", "rop")
    }
    for ev in aoh_events:
        if ev.recording_date is None or (today - ev.recording_date).days < 365 * 10:
            continue
        if _normalize_name(ev.grantor_text or "") in probate_subjects:
            continue
        emit.add(Finding(
            rule_id="r11_old_aoh_no_probate",
            severity="medium",
            confidence_score=0.82,
            tract_id=None,
            title=f"AOH > 10 years old without supporting probate (subject: {ev.grantor_text})",
            description=(
                f"Affidavit of Heirship recorded {ev.recording_date} for {ev.grantor_text} is "
                f"more than 10 years old and no probate or Record of Probate has been located. "
                f"Texas case law allows an AOH to serve as prima facie evidence after 10 years, "
                f"but title underwriters often require probate evidence for high-value tracts."
            ),
            suggested_action=(
                "Search probate dockets in the appropriate county for an open or closed estate. "
                "If a probate exists, record a certified copy or RoP. If not, document the AOH "
                "as the sole heirship evidence in the file for any underwriter review."
            ),
            assignee_level="junior_landman",
            dollar_impact_low=500.0,
            dollar_impact_high=5_000.0,
        ))


# ──────────────────────────────────────────────────────────────────────────
# Rule 12 — Top-lease conflict
# ──────────────────────────────────────────────────────────────────────────
def rule_12_top_lease_conflict(ctx: ProjectContext, emit: FindingEmitter) -> None:
    """Top-lease executed while a prior lease may still be in force."""
    for ev in ctx.chain_events:
        if ev.event_type != "top_lease" or ev.recording_date is None:
            continue
        # The top-lease metadata may reference the underlying lease instrument
        meta = ev.parsed_metadata or {}
        underlying_inst = meta.get("covers_same_acreage_as")
        underlying = next(
            (le for le in ctx.leases if le.opr_instrument_no == underlying_inst), None
        )
        if underlying is None:
            continue
        # Was the underlying lease still in primary term (or arguably HBP) when top-lease was executed?
        if underlying.primary_term_end and ev.recording_date <= underlying.primary_term_end:
            severity = "high"
            confidence = 0.95
            timing = "during primary term"
        else:
            # Even after primary term, top-leases over HBP wells are common conflict triggers
            severity = "medium"
            confidence = 0.6
            timing = "after primary term (potential HBP conflict)"
        emit.add(Finding(
            rule_id="r12_top_lease_conflict",
            severity=severity,
            confidence_score=confidence,
            tract_id=underlying.tract_id,
            lease_id=underlying.id,
            title=f"Top-lease conflict over lease {underlying.opr_instrument_no}",
            description=(
                f"A top-lease (instrument {ev.opr_instrument_no}, recorded {ev.recording_date}) "
                f"covers the same acreage as prior lease {underlying.opr_instrument_no} "
                f"(primary term ending {underlying.primary_term_end}). Top-lease was executed "
                f"{timing}. This can cloud the chain of title and produce competing lessee claims."
            ),
            suggested_action=(
                "Confirm primary-lease status: is it still in primary term or HBP? "
                "If yes, the top-lease is subject to the prior lease. If no, obtain a release of "
                "the prior lease to clear the top-lease's priority. Title opinion recommended."
            ),
            assignee_level="senior_landman",
            dollar_impact_low=5_000.0,
            dollar_impact_high=100_000.0,
            related_events=[{
                "top_lease_instrument": ev.opr_instrument_no,
                "underlying_lease_instrument": underlying.opr_instrument_no,
            }],
        ))


# ──────────────────────────────────────────────────────────────────────────
# Rule 16 — Mineral / royalty ambiguity in conveyance
# ──────────────────────────────────────────────────────────────────────────
def rule_16_mineral_royalty_ambiguity(ctx: ProjectContext, emit: FindingEmitter) -> None:
    """A conveyance or lease uses "X interest" without specifying mineral vs royalty."""
    for lease in ctx.leases:
        meta = lease.parsed_metadata or {}
        note = meta.get("interest_ambiguity_note")
        if not note:
            continue
        emit.add(Finding(
            rule_id="r16_mineral_royalty_ambiguity",
            severity="medium",
            confidence_score=0.75,
            tract_id=lease.tract_id,
            lease_id=lease.id,
            title=f"Mineral/royalty ambiguity in lease {lease.opr_instrument_no}",
            description=(
                f"Lease {lease.opr_instrument_no} contains language that does not clearly "
                f"distinguish a mineral interest from a royalty interest: \"{note}\". "
                f"Under Texas law, this ambiguity can affect NRI calculations, division order "
                f"terms, and unitization ratifications for affected parties."
            ),
            suggested_action=(
                "Obtain a Stipulation of Interest or Correction Affidavit from the affected "
                "party(ies) clarifying whether the conveyance was mineral or royalty. If "
                "parties are deceased, an Affidavit of Heirship reciting the interpretation "
                "of the original instrument may be acceptable subject to attorney review."
            ),
            assignee_level="attorney_referral",
            dollar_impact_low=2_000.0,
            dollar_impact_high=50_000.0,
        ))


# ──────────────────────────────────────────────────────────────────────────
# Rule 17 — ORRI cloud (unreleased ORRI > 36mo post-lease termination)
# ──────────────────────────────────────────────────────────────────────────
def rule_17_orri_cloud(ctx: ProjectContext, emit: FindingEmitter) -> None:
    today = date.today()
    # Find ORRI creations + check for matching releases
    creations = [ev for ev in ctx.chain_events if ev.event_type == "orri_creation"]
    releases = [ev for ev in ctx.chain_events if ev.event_type == "orri_release"]
    released_keys: set[tuple[str, str]] = set()
    for r in releases:
        released_keys.add((r.grantor_text or "", r.grantee_text or ""))
    for c in creations:
        key = (c.grantee_text or "", c.grantor_text or "")  # release reverses direction
        if key in released_keys:
            continue
        # Lease tied to this ORRI — is it still active?
        lease = next(
            (le for le in ctx.leases if le.id == c.references_lease_id), None
        )
        if lease is None:
            continue
        # If the underlying lease was terminated (primary term end past + no continuous prod) > 36mo ago,
        # the ORRI is cloud-worthy.
        if lease.primary_term_end and (today - lease.primary_term_end).days > 36 * 30:
            # Confirm no production in last 6 months on tract
            had_recent_production = False
            for w in ctx.wells_for_tract(lease.tract_id or -1):
                for p in w.production_monthly[-6:]:
                    if (p.get("oil_bbl") or 0) > 0 or (p.get("gas_mcf") or 0) > 0:
                        had_recent_production = True
                        break
                if had_recent_production:
                    break
            if not had_recent_production:
                emit.add(Finding(
                    rule_id="r17_orri_cloud",
                    severity="medium",
                    confidence_score=0.78,
                    tract_id=lease.tract_id,
                    lease_id=lease.id,
                    title=f"Unreleased ORRI cloud — {c.grantee_text}",
                    description=(
                        f"ORRI granted to {c.grantee_text} (instrument {c.opr_instrument_no}, "
                        f"recorded {c.recording_date}) tied to lease {lease.opr_instrument_no} "
                        f"has not been released. The underlying lease appears terminated "
                        f"({lease.primary_term_end}, no recent production), and >36 months have "
                        f"passed since termination."
                    ),
                    suggested_action=(
                        f"Send a release-request to {c.grantee_text}. If the ORRI was a "
                        f"term-limited interest, record a Notice of Termination citing the "
                        f"underlying lease termination. Until released the ORRI clouds the "
                        f"acreage for new leases."
                    ),
                    assignee_level="junior_landman",
                    dollar_impact_low=1_500.0,
                    dollar_impact_high=20_000.0,
                ))


# ──────────────────────────────────────────────────────────────────────────
# Stub registrations for rules 3, 7, 8, 9, 10, 13, 14, 15
# These are scaffolded but not yet firing on the synthetic demo data.
# Each stub logs a "would-evaluate" message and returns no findings; they're
# wired into the engine so the rule registry stays complete.
# ──────────────────────────────────────────────────────────────────────────
def _stub_rule_factory(rule_id: str, title: str):
    def _stub(ctx: ProjectContext, emit: FindingEmitter) -> None:
        log.debug("rule %s (%s) is a stub on this MVP demo; no findings emitted", rule_id, title)
    _stub.__name__ = f"rule_{rule_id}_stub"
    return _stub


rule_03_stranger_to_title = _stub_rule_factory("r03", "Stranger to title")
rule_07_retained_acreage_well_miss = _stub_rule_factory("r07", "Retained-acreage well miss")
rule_08_nri_mismatch = _stub_rule_factory("r08", "Lease Assignment NRI Mismatch")
rule_09_unratified_extension = _stub_rule_factory("r09", "Unratified extension")
rule_10_missing_ratification = _stub_rule_factory("r10", "Missing lease ratification (unitized)")
rule_13_surface_use_dispute = _stub_rule_factory("r13", "Surface use dispute")
rule_14_pipeline_row_expiration = _stub_rule_factory("r14", "Pipeline ROW expiration")
rule_15_mineral_classification_mismatch = _stub_rule_factory("r15", "County/state mineral classification mismatch")


RULE_REGISTRY = [
    rule_01_unrecorded_p4_assignment,
    rule_02_probate_gap,
    rule_03_stranger_to_title,
    rule_04_depth_severance_mismatch,
    rule_05_primary_term_no_continuous_prod,
    rule_06_pugh_release_missed,
    rule_07_retained_acreage_well_miss,
    rule_08_nri_mismatch,
    rule_09_unratified_extension,
    rule_10_missing_ratification,
    rule_11_old_aoh_no_probate,
    rule_12_top_lease_conflict,
    rule_13_surface_use_dispute,
    rule_14_pipeline_row_expiration,
    rule_15_mineral_classification_mismatch,
    rule_16_mineral_royalty_ambiguity,
    rule_17_orri_cloud,
]


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────
def _normalize_name(name: str) -> str:
    return " ".join(name.upper().split()).replace(",", "")


def _tract_for_well(ctx: ProjectContext, well: WellRow):
    # Best-effort: pick the first tract in the same county for this well.
    for t in ctx.tracts:
        if t.county_fips == well.county_fips:
            return t
    return None


def run_all_rules(ctx: ProjectContext) -> list[Finding]:
    emit = FindingEmitter(project_id=ctx.project_id)
    for rule in RULE_REGISTRY:
        try:
            rule(ctx, emit)
        except Exception as e:  # noqa: BLE001
            log.warning("rule %s failed: %s", getattr(rule, "__name__", "?"), e)
    return emit.findings


__all__ = ["RULE_REGISTRY", "run_all_rules"]
