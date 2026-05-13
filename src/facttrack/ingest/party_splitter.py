"""Backfill `lease_party` rows from `lease.lessor_text` / `lease.lessee_text`.

The publicsearch.us scraper captures lessor/lessee as single text fields. The
curative rule engine needs structured `lease_party` rows (with `is_deceased`)
to evaluate probate-gap (r02) and several other rules. This module is the
bridge.

Detection rules:
  - "ESTATE OF X" / "X ESTATE OF" / "DEC'D" / "DECEASED" anywhere in lessor → is_deceased=TRUE
  - Multiple lessors joined by " AND " / " & " / ", " are split (best-effort)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from facttrack.db import cursor

log = logging.getLogger(__name__)


_DECEASED_MARKERS = re.compile(
    r"\b(estate\s+of|deceased|dec'd|dec\.|widow|widower|surviving\s+spouse|administrator|executor|heirs?\s+of)\b",
    re.IGNORECASE,
)

# A multi-lessor join like "JOHN SMITH AND MARY SMITH" — only split on AND between
# what look like two name-like tokens
_AND_JOIN = re.compile(r"\s+(?:and|&)\s+", re.IGNORECASE)


def _split_lessor_text(raw: str) -> list[str]:
    """Return distinct party names from a single lessor_text or lessee_text."""
    if not raw:
        return []
    raw = raw.strip()
    # Don't split inside "ESTATE OF X" — guard with simple lookahead heuristic
    if _DECEASED_MARKERS.search(raw) and " AND " not in raw.upper():
        return [raw]
    parts = _AND_JOIN.split(raw)
    return [p.strip() for p in parts if p.strip()]


def _is_deceased(name: str) -> bool:
    return bool(_DECEASED_MARKERS.search(name))


def _normalize_clean(name: str) -> str:
    return " ".join(name.upper().split())


@dataclass
class SplitResult:
    lease_id: int
    instrument_no: str
    parties_inserted: int
    deceased_count: int


def backfill_county(county_fips: str) -> dict[str, int]:
    """Create lease_party rows for every lease in `county_fips` that has none yet."""
    totals = {"leases_processed": 0, "leases_skipped_already_has_parties": 0,
              "lessor_rows": 0, "lessee_rows": 0, "deceased_flagged": 0}
    with cursor() as cur:
        cur.execute(
            """
            SELECT l.id, l.opr_instrument_no, l.lessor_text, l.lessee_text,
                   (SELECT count(*) FROM facttrack.lease_party lp WHERE lp.lease_id = l.id) AS party_count
            FROM facttrack.lease l
            WHERE l.county_fips = %s
            """,
            (county_fips,),
        )
        rows = cur.fetchall()

    for row in rows:
        if row["party_count"] > 0:
            totals["leases_skipped_already_has_parties"] += 1
            continue
        lease_id = row["id"]
        totals["leases_processed"] += 1

        with cursor(dict_rows=False) as cur:
            for lessor_name in _split_lessor_text(row.get("lessor_text") or ""):
                clean = _normalize_clean(lessor_name)
                deceased = _is_deceased(clean)
                cur.execute(
                    """
                    INSERT INTO facttrack.lease_party (lease_id, role, name, is_deceased)
                    VALUES (%s, 'lessor', %s, %s)
                    """,
                    (lease_id, clean, deceased),
                )
                totals["lessor_rows"] += 1
                if deceased:
                    totals["deceased_flagged"] += 1
            for lessee_name in _split_lessor_text(row.get("lessee_text") or ""):
                cur.execute(
                    """
                    INSERT INTO facttrack.lease_party (lease_id, role, name, is_deceased)
                    VALUES (%s, 'lessee', %s, %s)
                    """,
                    (lease_id, _normalize_clean(lessee_name), False),
                )
                totals["lessee_rows"] += 1

    log.info("party backfill summary: %s", totals)
    return totals


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--county", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(backfill_county(args.county))
