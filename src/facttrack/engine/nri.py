"""Net Revenue Interest calculation for a single tract.

The standard East-Texas working-interest-side math:

    Lessor royalty (RI)   = parsed lease royalty_fraction        (e.g. 0.125)
    Burdens (ORRIs)       = sum of ORRI rates encumbering the WI (e.g. 0.05)
    Lessee NRI            = WI_share × (1 − RI − ORRIs)

When the operator (Monument) is acquiring the leasehold from an existing
lessee, the "WI_share" is the fraction of the working interest Monument is
buying. Default 100%. The result is what Monument actually receives per
barrel produced after the chain burdens come out.

Royalty stacking and ORRI math here are deterministic — the boss can argue
with the numbers, which is exactly what an effective deal memo requires.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

from facttrack.engine.context import ChainEventRow, LeaseRow

log = logging.getLogger(__name__)


@dataclass
class RoyaltyStack:
    """One row per burden against the working interest."""
    party: str
    kind: str          # "lessor_royalty" | "orri" | "carried"
    rate: float        # decimal (0.125, not 12.5)
    source: str        # instrument or chain reference for audit


@dataclass
class NRIComputation:
    tract_label: str
    wi_share: float
    lessor_royalty: float
    orri_burden: float
    other_burden: float
    nri: float
    stack: list[RoyaltyStack] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def compute_nri_for_tract(
    tract_label: str,
    leases: list[LeaseRow],
    chain_events: Iterable[ChainEventRow],
    wi_share: float = 1.0,
) -> NRIComputation:
    """Compute NRI to a hypothetical operator-side buyer of `wi_share` of the
    leasehold on this tract.

    If multiple leases are on the same tract (rare for our current data), the
    lessor royalty is taken as the maximum — that's the worst-case burden
    Monument would have to honor. Conservative; tell the bosses if you flip
    to weighted average.
    """
    notes: list[str] = []
    stack: list[RoyaltyStack] = []

    # Lessor royalty — worst case across leases on tract
    lessor_rates = [
        (le, float(le.royalty_fraction))
        for le in leases
        if le.royalty_fraction is not None
    ]
    if not lessor_rates:
        lessor_royalty = 0.125
        notes.append(
            "Lessor royalty not extracted from any lease; default 1/8 (12.5%) "
            "assumed. Verify against original lease before committing."
        )
        stack.append(RoyaltyStack(
            party="lessor (assumed)",
            kind="lessor_royalty",
            rate=0.125,
            source="default — clause not extracted",
        ))
    else:
        worst_le, lessor_royalty = max(lessor_rates, key=lambda x: x[1])
        for le, rate in lessor_rates:
            stack.append(RoyaltyStack(
                party=(le.lessor_text or "unknown lessor"),
                kind="lessor_royalty",
                rate=rate,
                source=f"lease {le.opr_instrument_no or le.id}",
            ))
        if len(lessor_rates) > 1:
            notes.append(
                f"Multiple leases of record; using worst-case (highest) royalty "
                f"{lessor_royalty:.4f} from lease {worst_le.opr_instrument_no}. "
                f"Switch to weighted average if WI is partial."
            )

    # ORRIs from chain events
    orri_burden = 0.0
    lease_ids = {le.id for le in leases}
    for ev in chain_events:
        if ev.event_type != "orri_creation":
            continue
        if ev.references_lease_id not in lease_ids:
            continue
        meta = ev.parsed_metadata or {}
        rate = meta.get("orri_rate")
        if rate is None:
            continue
        try:
            rate_f = float(rate)
        except (TypeError, ValueError):
            continue
        orri_burden += rate_f
        stack.append(RoyaltyStack(
            party=ev.grantee_text or "unknown ORRI holder",
            kind="orri",
            rate=rate_f,
            source=f"chain event {ev.opr_instrument_no} recorded {ev.recording_date}",
        ))

    other_burden = 0.0  # placeholder for carried interests, NPRI, etc.

    nri = wi_share * (1.0 - lessor_royalty - orri_burden - other_burden)
    nri = max(0.0, min(1.0, nri))  # bound

    return NRIComputation(
        tract_label=tract_label,
        wi_share=wi_share,
        lessor_royalty=lessor_royalty,
        orri_burden=orri_burden,
        other_burden=other_burden,
        nri=nri,
        stack=stack,
        notes=notes,
    )
