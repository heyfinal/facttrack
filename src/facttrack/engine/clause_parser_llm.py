"""LLM-assisted clause extraction via OpenRouter.

The regex clause_parser hits ~14% on primary term and 0% on Pugh-clause on
the 1990-2010 Anderson dataset because typewritten / scanned-image leases
phrase clauses inconsistently. This module hands each lease's OCR text to
an LLM with a strict JSON-output schema and asks for the same fields:

    primary_term_years, primary_term_end_estimate,
    royalty_fraction,   royalty_fraction_decimal,
    has_pugh_clause,    has_retained_acreage,
    has_continuous_dev,
    depth_limit_ft,
    deceased_lessor_names: [...],
    notes: free-form caveat

Only writes a column if it's currently NULL (preserves regex hits we trust).
Stores a per-lease audit blob in parsed_metadata so the source of each value
is traceable.

Uses a cheap fast model (default: deepseek/deepseek-chat). Override via
FT_LLM_MODEL env.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from facttrack.config import PATHS
from facttrack.db import cursor

log = logging.getLogger(__name__)


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = os.environ.get("FT_LLM_MODEL", "deepseek/deepseek-chat")
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")


_SYSTEM_PROMPT = """You are a 25-year East-Texas oil-and-gas landman.
You read scanned-and-OCR'd lease text — typewritten, with substantial OCR
noise — and extract the standard chain-of-title clauses with the precision
of someone who has signed a thousand title opinions.

Return ONLY a JSON object matching the schema. Do not include prose around
the JSON. If a field is unclear in the text, return null for that field and
add a short note in `notes`."""


_USER_PROMPT_TEMPLATE = """Extract these fields from the OCR text below and return strict JSON:

{{
  "primary_term_years":     number | null,    // e.g. 3, 5, 10. Look for "for a term of N years" or "primary term shall be N years"
  "royalty_fraction":       number | null,    // decimal — 0.125 for 1/8, 0.1875 for 3/16, 0.25 for 1/4
  "has_pugh_clause":        true | false | null,  // language requiring release of acreage not held by production
  "has_retained_acreage":   true | false | null,  // language defining what acreage is held by a producing well/unit
  "has_continuous_dev":     true | false | null,  // continuous-development / drilling clause
  "depth_limit_ft":         number | null,    // numeric depth in feet, only if specifically stated in surface-to-X feet language
  "deceased_lessor_names":  [string],         // any lessor explicitly indicated as deceased, "estate of", "executor of", etc.
  "notes":                  string            // 1-2 sentences flagging OCR concerns or ambiguity
}}

OCR text:
=== BEGIN ===
{ocr_text}
=== END ===
"""


@dataclass
class LLMExtraction:
    primary_term_years: float | None = None
    primary_term_end_estimate: date | None = None
    royalty_fraction: float | None = None
    has_pugh_clause: bool | None = None
    has_retained_acreage: bool | None = None
    has_continuous_dev: bool | None = None
    depth_limit_ft: float | None = None
    deceased_lessor_names: list[str] = None
    notes: str = ""
    model: str = ""
    raw_response: dict = None


def _call_openrouter(messages: list[dict], model: str = DEFAULT_MODEL,
                    max_retries: int = 3) -> dict:
    if not API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY env not set")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": 800,
        "response_format": {"type": "json_object"},
    }
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=90.0) as client:
                resp = client.post(
                    OPENROUTER_URL,
                    headers={
                        "Authorization": f"Bearer {API_KEY}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://github.com/heyfinal/facttrack",
                        "X-Title": "FactTrack",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            last_err = e
            log.warning("OpenRouter retry %d/%d: %s", attempt, max_retries, e)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"OpenRouter failed after {max_retries} attempts: {last_err}")


def _parse_response(raw: dict) -> dict:
    try:
        msg = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"bad response shape: {e}; raw={raw}") from e
    # response_format may not be honored by every provider; strip code fences
    s = msg.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return json.loads(s)


def extract_for_lease(county_fips: str, instrument_no: str,
                     model: str = DEFAULT_MODEL) -> LLMExtraction | None:
    """Pull a lease's OCR text from disk + ask the LLM to extract clauses."""
    cache_dir = PATHS.cache / "lease_images" / county_fips
    if not cache_dir.exists():
        log.warning("no cache dir for county %s", county_fips)
        return None
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", instrument_no)
    lease_dir = cache_dir / safe_id
    ocr_dir = lease_dir / "_ocr"
    if not ocr_dir.exists():
        log.info("no OCR cache for %s/%s", county_fips, instrument_no)
        return None
    pages = sorted(ocr_dir.glob("page_*.txt"))
    if not pages:
        return None
    text_parts = []
    for p in pages:
        try:
            text_parts.append(p.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            log.warning("read fail for %s: %s", p, e)
    full_text = "\n".join(text_parts)[:14_000]  # cap to keep prompt small

    raw = _call_openrouter(
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _USER_PROMPT_TEMPLATE.format(ocr_text=full_text)},
        ],
        model=model,
    )
    parsed = _parse_response(raw)

    ex = LLMExtraction(model=model, raw_response=raw)
    pt = parsed.get("primary_term_years")
    if isinstance(pt, (int, float)) and 0 < pt < 100:
        ex.primary_term_years = float(pt)
    rf = parsed.get("royalty_fraction")
    if isinstance(rf, (int, float)) and 0 < rf < 1:
        ex.royalty_fraction = float(rf)
    for k in ("has_pugh_clause", "has_retained_acreage", "has_continuous_dev"):
        v = parsed.get(k)
        if isinstance(v, bool):
            setattr(ex, k, v)
    dl = parsed.get("depth_limit_ft")
    if isinstance(dl, (int, float)) and 100 <= dl <= 50_000:
        ex.depth_limit_ft = float(dl)
    dn = parsed.get("deceased_lessor_names") or []
    if isinstance(dn, list):
        ex.deceased_lessor_names = [str(n).strip() for n in dn if isinstance(n, str) and n.strip()]
    ex.notes = str(parsed.get("notes") or "")[:500]
    return ex


def persist_extraction(lease_id: int, ex: LLMExtraction) -> None:
    """Only write into NULL columns; preserve regex hits."""
    if ex is None:
        return
    audit = {
        "source": "llm",
        "model": ex.model,
        "primary_term_years": ex.primary_term_years,
        "royalty_fraction": ex.royalty_fraction,
        "has_pugh_clause": ex.has_pugh_clause,
        "has_retained_acreage": ex.has_retained_acreage,
        "has_continuous_dev": ex.has_continuous_dev,
        "depth_limit_ft": ex.depth_limit_ft,
        "deceased_lessor_names": ex.deceased_lessor_names or [],
        "notes": ex.notes,
    }
    # Compute primary_term_end if we have a term + effective/recording date
    primary_term_end = None
    if ex.primary_term_years:
        with cursor() as cur:
            cur.execute(
                "SELECT effective_date, recording_date FROM facttrack.lease WHERE id=%s",
                (lease_id,),
            )
            row = cur.fetchone()
        if row:
            anchor = row.get("effective_date") or row.get("recording_date")
            if anchor:
                primary_term_end = anchor + timedelta(days=int(ex.primary_term_years * 365.25))

    with cursor(dict_rows=False) as cur:
        cur.execute(
            """
            UPDATE facttrack.lease
               SET primary_term_years    = COALESCE(primary_term_years, %s),
                   primary_term_end      = COALESCE(primary_term_end,   %s),
                   royalty_fraction      = COALESCE(royalty_fraction,   %s),
                   has_pugh_clause       = COALESCE(has_pugh_clause,    %s),
                   has_retained_acreage  = COALESCE(has_retained_acreage,%s),
                   has_continuous_dev    = COALESCE(has_continuous_dev, %s),
                   depth_limit_ft        = COALESCE(depth_limit_ft,     %s),
                   parsed_metadata       = parsed_metadata || jsonb_build_object('llm_extraction', %s::jsonb)
             WHERE id = %s
            """,
            (
                ex.primary_term_years,
                primary_term_end,
                ex.royalty_fraction,
                ex.has_pugh_clause,
                ex.has_retained_acreage,
                ex.has_continuous_dev,
                ex.depth_limit_ft,
                json.dumps(audit, default=str),
                lease_id,
            ),
        )
        # Mark deceased lessors if any
        for name in ex.deceased_lessor_names or []:
            cur.execute(
                """
                UPDATE facttrack.lease_party
                   SET is_deceased = TRUE
                 WHERE lease_id = %s AND UPPER(name) LIKE %s
                """,
                (lease_id, f"%{name.upper().replace(' ', '%')}%"),
            )


def run_for_county(county_fips: str, only_missing: bool = True,
                  model: str = DEFAULT_MODEL,
                  audit_path: Path | None = None) -> dict:
    """Run LLM extraction on every lease in the county whose primary_term_end
    is NULL (i.e., where regex didn't catch it). If only_missing=False,
    run on every lease."""
    where = ["county_fips = %s"]
    params = [county_fips]
    if only_missing:
        where.append(
            "(primary_term_years IS NULL OR has_pugh_clause IS NULL "
            "OR depth_limit_ft IS NULL)"
        )
    with cursor() as cur:
        cur.execute(
            f"SELECT id, opr_instrument_no FROM facttrack.lease "
            f"WHERE {' AND '.join(where)} AND opr_instrument_no IS NOT NULL",
            params,
        )
        targets = cur.fetchall()

    audit_lines = [
        f"=== llm_clause_extractor: county={county_fips} model={model} ===",
        f"target leases (only_missing={only_missing}): {len(targets)}",
        "",
    ]
    counts = {"checked": 0, "extracted": 0, "no_ocr": 0, "failed": 0}
    for row in targets:
        counts["checked"] += 1
        inst = row["opr_instrument_no"]
        try:
            ex = extract_for_lease(county_fips, inst, model=model)
        except Exception as e:
            counts["failed"] += 1
            audit_lines.append(f"FAIL {inst}: {e}")
            continue
        if ex is None:
            counts["no_ocr"] += 1
            audit_lines.append(f"SKIP {inst}: no OCR cache")
            continue
        counts["extracted"] += 1
        persist_extraction(row["id"], ex)
        audit_lines.append(
            f"OK {inst}: term={ex.primary_term_years} roy={ex.royalty_fraction} "
            f"pugh={ex.has_pugh_clause} depth={ex.depth_limit_ft} "
            f"deceased={ex.deceased_lessor_names}"
        )
        if ex.notes:
            audit_lines.append(f"   notes: {ex.notes[:200]}")
        time.sleep(0.2)  # gentle pacing

    audit_lines.append("")
    audit_lines.append("=== summary ===")
    audit_lines.append(json.dumps(counts, indent=2))
    if audit_path:
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_text("\n".join(audit_lines), encoding="utf-8")
        log.info("wrote LLM audit trail → %s", audit_path)
    log.info("LLM extraction complete: %s", counts)
    return counts


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--county", required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--all", action="store_true",
                        help="run on every lease, not just leases missing a clause")
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    counts = run_for_county(args.county, only_missing=(not args.all),
                           model=args.model, audit_path=args.audit)
    print(json.dumps(counts, indent=2))
