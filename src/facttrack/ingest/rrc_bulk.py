"""TX RRC monthly bulk-dump ingest.

The RRC publishes large delimited dumps for operator information (P-5),
production data (PR), wells, and operator history. Documented at:
    https://mft.rrc.texas.gov/
    https://www.rrc.texas.gov/oil-and-gas/research-and-statistics/

The MFT site is the formal hosting location; the underlying datasets are
versioned monthly. We download the relevant zip / dat files, parse them, and
upsert into the canonical schema.

For Phase 1 we ingest:
- Operator master (P-5 dump)
- Statewide wellbore master (limited to county codes we care about)

Production data is large (~5 GB/month) — we filter to wells in our
target counties on parse to keep storage small.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

import httpx

from facttrack.config import HTTP, PATHS, ensure_dirs
from facttrack.models import Operator, Well

log = logging.getLogger(__name__)


# RRC publishes datasets at versioned URLs. The patterns below are documented
# in the RRC data documentation PDFs but URLs occasionally rev — the fetcher
# probes both legacy and current URL patterns.
RRC_OPERATOR_URLS = [
    # Current MFT path (2024+)
    "https://mft.rrc.texas.gov/link/4ee8c1cc-3a36-4ad0-aef2-5b27e0f31b0e",
    # Legacy webcom path (still alive as of mid-2025)
    "https://www.rrc.texas.gov/site/files/operator-data.zip",
]

# RRC district codes for East Texas. Anderson + Houston counties are in District 06.
EAST_TX_RRC_DISTRICTS = ["06"]

# TX county FIPS → RRC county code (RRC uses its own 3-digit county code)
COUNTY_FIPS_TO_RRC_CODE: dict[str, str] = {
    "48001": "001",  # Anderson
    "48225": "227",  # Houston (RRC code 227 — not the same as FIPS 48225)
}


@dataclass
class FetchResult:
    url: str
    ok: bool
    bytes_in: int
    cached_path: Path | None
    error: str | None = None


def fetch_url_to_cache(url: str, cache_name: str | None = None) -> FetchResult:
    """Download `url` once; subsequent calls use the cached file.

    Returns a FetchResult with the cached path so callers can stream-parse.
    """
    ensure_dirs()
    name = cache_name or re.sub(r"[^A-Za-z0-9._-]", "_", url.split("?", 1)[0].rsplit("/", 1)[-1] or "download.bin")
    if not name or name == "_":
        name = "rrc_download.bin"
    cache_path = PATHS.cache / name

    if cache_path.exists() and cache_path.stat().st_size > 0:
        log.info("RRC bulk fetch: cache HIT %s (%d bytes)", cache_path, cache_path.stat().st_size)
        return FetchResult(url=url, ok=True, bytes_in=cache_path.stat().st_size, cached_path=cache_path)

    log.info("RRC bulk fetch: GET %s", url)
    try:
        with httpx.Client(
            headers={"User-Agent": HTTP.user_agent},
            timeout=httpx.Timeout(60.0, connect=15.0),
            follow_redirects=True,
        ) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    log.warning("RRC fetch %s returned %d", url, resp.status_code)
                    return FetchResult(url=url, ok=False, bytes_in=0, cached_path=None,
                                       error=f"HTTP {resp.status_code}")
                tmp_path = cache_path.with_suffix(cache_path.suffix + ".part")
                size = 0
                with open(tmp_path, "wb") as fh:
                    for chunk in resp.iter_bytes(chunk_size=64 * 1024):
                        fh.write(chunk)
                        size += len(chunk)
                tmp_path.rename(cache_path)
                log.info("RRC bulk fetch: wrote %d bytes to %s", size, cache_path)
                return FetchResult(url=url, ok=True, bytes_in=size, cached_path=cache_path)
    except (httpx.HTTPError, OSError) as e:
        log.warning("RRC fetch failed: %s", e)
        return FetchResult(url=url, ok=False, bytes_in=0, cached_path=None, error=str(e))


def try_fetch_operator_dump() -> FetchResult:
    """Try each documented RRC operator-dump URL until one succeeds."""
    last_err = None
    for url in RRC_OPERATOR_URLS:
        result = fetch_url_to_cache(url, cache_name=f"rrc_operator_{re.sub(r'[^A-Za-z0-9]', '_', url[-40:])}.bin")
        if result.ok and result.bytes_in > 1024:  # heuristic — anything under 1KB is probably an error page
            return result
        last_err = result.error
    return FetchResult(url=RRC_OPERATOR_URLS[0], ok=False, bytes_in=0, cached_path=None,
                       error=f"all known operator dump URLs failed; last error: {last_err}")


def parse_operators_from_dump(path: Path) -> Iterator[Operator]:
    """Parse the RRC operator dump. Supports zip and fixed-width / CSV variants.

    The RRC publishes two flavors over the years:
    - Fixed-width 'OPERATOR.TXT' inside a zip
    - CSV with header row (newer datasets)
    We probe the content and dispatch accordingly.
    """
    if not path.exists():
        return

    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if name.lower().endswith((".txt", ".dat", ".csv")):
                    with zf.open(name) as fh:
                        yield from _parse_operator_stream(io.TextIOWrapper(fh, encoding="latin-1", errors="replace"))
                    return
        log.warning("RRC operator zip %s has no parseable files", path)
        return

    # plain file
    with open(path, "r", encoding="latin-1", errors="replace") as fh:
        yield from _parse_operator_stream(fh)


def _parse_operator_stream(stream: Iterable[str]) -> Iterator[Operator]:
    """Probe the first line — CSV (has comma + 'Operator' or 'P5') or fixed-width."""
    first = None
    for line in stream:
        first = line
        break
    if first is None:
        return

    # Heuristic: if the first line looks like a CSV header with known column names, use CSV.
    is_csv = "," in first and re.search(r"(?i)(p[-_ ]?5|operator name|operator_no)", first)

    if is_csv:
        reader = csv.DictReader(
            _prepend_first_line(first, stream),
            delimiter=",",
        )
        for row in reader:
            op = _csv_row_to_operator(row)
            if op is not None:
                yield op
    else:
        # Fixed-width fallback — operator dumps historically had:
        #   p5 number (8 chars), operator name (32 chars), status (3 chars), address...
        # We'll do a forgiving parse and rely on metadata for the rest.
        for line in _prepend_first_line(first, stream):
            line = line.rstrip("\r\n")
            if len(line) < 40:
                continue
            try:
                p5 = int(line[0:8].strip())
            except ValueError:
                continue
            name = line[8:48].strip()
            status = line[48:51].strip() if len(line) > 51 else None
            address = line[51:].strip() if len(line) > 51 else None
            yield Operator(rrc_p5_number=p5, name=name, status=status or None, address=address or None)


def _prepend_first_line(first: str, rest: Iterable[str]) -> Iterator[str]:
    yield first
    yield from rest


_CSV_KEYS_P5 = ("p5_no", "p5", "operator_no", "operator_p5", "operatorp5")
_CSV_KEYS_NAME = ("operator_name", "name", "operatorname")
_CSV_KEYS_STATUS = ("status", "operator_status")
_CSV_KEYS_ADDRESS = ("address", "addr", "operator_address")


def _pick(d: dict, keys: tuple[str, ...]) -> str | None:
    lowered = {k.lower().replace("-", "_").replace(" ", "_"): v for k, v in d.items()}
    for k in keys:
        if k in lowered and lowered[k] not in (None, ""):
            return str(lowered[k]).strip()
    return None


def _csv_row_to_operator(row: dict) -> Operator | None:
    p5_raw = _pick(row, _CSV_KEYS_P5)
    name = _pick(row, _CSV_KEYS_NAME)
    if not p5_raw or not name:
        return None
    try:
        p5 = int(re.sub(r"\D", "", p5_raw))
    except ValueError:
        return None
    return Operator(
        rrc_p5_number=p5,
        name=name,
        status=_pick(row, _CSV_KEYS_STATUS),
        address=_pick(row, _CSV_KEYS_ADDRESS),
    )


def upsert_operators(operators: Iterable[Operator]) -> int:
    from facttrack.db import cursor

    n = 0
    with cursor(dict_rows=False) as cur:
        for op in operators:
            cur.execute(
                """
                INSERT INTO operator (rrc_p5_number, name, address, status, last_seen_at, metadata)
                VALUES (%s, %s, %s, %s, now(), %s)
                ON CONFLICT (rrc_p5_number) DO UPDATE
                SET name = EXCLUDED.name,
                    address = COALESCE(EXCLUDED.address, operator.address),
                    status = COALESCE(EXCLUDED.status, operator.status),
                    last_seen_at = now()
                """,
                (op.rrc_p5_number, op.name, op.address, op.status, "{}"),
            )
            n += 1
            if n % 1000 == 0:
                log.info("upserted %d operators...", n)
    log.info("upserted %d operators total", n)
    return n


def ingest_operators_from_rrc() -> int:
    """High-level: fetch the RRC operator dump (or use cache), parse, upsert.

    Returns the number of rows upserted. Returns 0 if the dump couldn't be
    fetched — the caller should treat that as a soft failure and rely on
    per-record P-5 lookups instead.
    """
    fetched = try_fetch_operator_dump()
    if not fetched.ok or not fetched.cached_path:
        log.warning("Could not fetch RRC operator dump: %s", fetched.error)
        return 0
    return upsert_operators(parse_operators_from_dump(fetched.cached_path))


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = ingest_operators_from_rrc()
    print(f"operators upserted: {n}")
