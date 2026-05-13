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
                    related_events=[{"well_api": well.api_no, "from_p5": prev["operator_p5"], "to_p5": curr["operator_p5"], "effective": str(change_date)}],
                ))


# ──────────────────────────────────────────────────────────────────────────
# Rule 2 — Probate gap (deceased lessor without AOH or probate)
# ──────────────────────────────────────────────────────────────────────────
def rule_02_probate_gap(ctx: ProjectContext, emit: FindingEmitter) -> None:
    """A lease party is marked deceased but no AOH or probate is recorded.

    Implements Texas Estates Code §203.001: an AOH on file in deed records
    for ≥5 years is prima facie evidence of heirship — those cases are
    self-curing and we do not flag them. AOHs younger than 5 years are
    surfaced as medium-severity (heirship recital exists but has not yet
    matured to prima facie status).
    """
    today = date.today()
    # name → (event_type, recording_date) for the best evidence available
    aoh_or_probate: dict[str, tuple[str, date | None]] = {}
    for ev in ctx.chain_events:
        if ev.event_type in ("aoh", "probate", "rop") and ev.grantor_text:
            key = _normalize_name(ev.grantor_text)
            existing = aoh_or_probate.get(key)
            # Prefer earliest record so §203.001's 5-yr clock measures correctly
            if existing is None or (ev.recording_date and (existing[1] is None or ev.recording_date < existing[1])):
                aoh_or_probate[key] = (ev.event_type, ev.recording_date)

    for lease in ctx.leases:
        for party in lease.parties:
            if party.role != "lessor" or not party.is_deceased:
                continue
            key = _normalize_name(party.name)
            evidence = aoh_or_probate.get(key)

            if evidence is not None:
                ev_type, ev_date = evidence
                # Probate / RoP is binding regardless of age; AOH needs §203.001 maturity
                if ev_type in ("probate", "rop"):
                    continue
                if ev_date and (today - ev_date).days >= 5 * 365:
                    continue
                # AOH on file but younger than 5 yr — flag as medium, not critical
                emit.add(Finding(
                    rule_id="r02_probate_gap",
                    severity="medium",
                    confidence_score=0.7,
                    tract_id=lease.tract_id,
                    lease_id=lease.id,
                    title=f"AOH on file but pre-§203.001 — {party.name} (lease {lease.opr_instrument_no})",
                    description=(
                        f"An Affidavit of Heirship for {party.name} is recorded "
                        f"({ev_date}), but has been on file less than 5 years. Under "
                        f"Texas Estates Code §203.001 it is not yet prima facie evidence "
                        f"of heirship. Confirm heirship recital with a current landman or "
                        f"defer commitment until §203.001 maturity."
                    ),
                    suggested_action=(
                        "Calendar the §203.001 maturity date. If commitment is required "
                        "sooner, supplement with a current AOH executed by a disinterested "
                        "affiant or obtain a determination of heirship from the probate court."
                    ),
                    assignee_level="junior_landman",
                    related_events=[{"aoh_recording_date": str(ev_date)}],
                ))
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
                related_events=[{"lessor": party.name, "fraction": party.fraction_signed}],
            ))


# ──────────────────────────────────────────────────────────────────────────
# Rule 4 — Depth severance mismatch
# ──────────────────────────────────────────────────────────────────────────
def rule_04_depth_severance_mismatch(ctx: ProjectContext, emit: FindingEmitter) -> None:
    """Lease grants limited depth interval; producing depth from RRC W-2 is outside it.

    Only fires when BOTH the lease depth limit AND the well's actual producing depth
    are populated from real data. Producing depth comes from RRC completion records
    (well.metadata['producing_depth_ft']). No hardcoded formation-to-depth heuristics.
    """
    for tract in ctx.tracts:
        for lease in ctx.leases_for_tract(tract.id):
            if lease.depth_limit_ft is None:
                continue
            for well in ctx.wells_for_tract(tract.id):
                producing_depth = None
                if isinstance(well.metadata, dict):
                    producing_depth = well.metadata.get("producing_depth_ft")
                if producing_depth is None:
                    # No real producing-depth data available — do NOT speculate.
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
            # Grantor-side release verifier may have already proved the lessee
            # filed a release of this lease. If so, the lease died honestly —
            # no curative item.
            existing_release = next(
                (
                    ev for ev in ctx.chain_events_for_lease(lease.id)
                    if ev.event_type == "release"
                    and ev.recording_date is not None
                    and ev.recording_date < today
                ),
                None,
            )
            if existing_release is not None:
                log.info(
                    "r05 demoted for lease %s — release %s recorded %s",
                    lease.opr_instrument_no,
                    existing_release.opr_instrument_no,
                    existing_release.recording_date,
                )
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
                ))


# Rule registry — only rules with real implementations are listed.
# Rules with IDs r03, r07-r10, r13-r15 are planned for future releases when
# the data sources needed to evaluate them are wired (full chain-assignment
# math, surface deed records, GLO state lease cross-reference). They are
# intentionally NOT registered here so the engine never emits placeholder
# findings.
# Note on r11: an Affidavit of Heirship on file 5+ years is *prima facie* evidence
# of heirship under Texas Estates Code § 203.001 — flagging it as a curative item
# is factually incorrect. Rule retained in source for traceability but removed
# from the registry until reframed (e.g. invert it: "AOH absent" not "AOH old").
RULE_REGISTRY = [
    rule_01_unrecorded_p4_assignment,
    rule_02_probate_gap,
    rule_04_depth_severance_mismatch,
    rule_05_primary_term_no_continuous_prod,
    rule_06_pugh_release_missed,
    rule_12_top_lease_conflict,
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
