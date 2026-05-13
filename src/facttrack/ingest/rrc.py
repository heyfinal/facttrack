"""TX Railroad Commission ingestion.

The RRC publishes three categories of public data we need:

1. Online query interface (HTML/CSV) — useful for tract-scoped queries
   - Production Data Query (PR data): well-by-well monthly production
   - Operator Information (P-5): operator name, P5 number, address, status
   - Operator History (P-4): operator-of-record changes per well
   - Well Search by Operator / County / Field
2. Monthly bulk download dumps (https://mft.rrc.texas.gov/) — preferred for backfill
3. GIS feature services — well surface locations, lease polygons

This module focuses on (1) for now; the bulk dump path is a separate ingest path.

NOTE: 2025–2026 RRC site migration moved many old `webapps.rrc.texas.gov/EWA/*` URLs.
The new query entry is at https://webapps.rrc.texas.gov/oil-and-gas/research-and-statistics/
We use httpx with conservative rate limiting and exponential backoff.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Iterable

import httpx
from tenacity import (
    retry, retry_if_exception_type, stop_after_attempt, wait_exponential
)

from facttrack.config import HTTP
from facttrack.models import (
    Operator, ProductionMonthly, Well, WellOperatorHistory
)

log = logging.getLogger(__name__)

RRC_BASE = "https://webapps.rrc.texas.gov"
RRC_PUBLIC_QUERY = f"{RRC_BASE}/PDQ"  # Production Data Query (post-migration)
RRC_OG_RESEARCH = "https://www.rrc.texas.gov/oil-and-gas/research-and-statistics/"


class RRCError(Exception):
    """Raised when an RRC request fails after retries or returns malformed data."""


@dataclass
class RateLimiter:
    """Simple sync token-bucket; enforces a minimum interval between requests."""
    per_sec: float
    _last_call: float = 0.0

    def wait(self) -> None:
        if self.per_sec <= 0:
            return
        interval = 1.0 / self.per_sec
        now = time.monotonic()
        gap = now - self._last_call
        if gap < interval:
            time.sleep(interval - gap)
        self._last_call = time.monotonic()


class RRCClient:
    """Synchronous HTTP client for RRC online queries.

    Holds one httpx.Client with retry + rate-limit middleware.
    Designed to be re-used across an ingestion run.
    """

    def __init__(self, base_url: str = RRC_BASE, rate_limit_per_sec: float | None = None) -> None:
        self._base_url = base_url
        self._rl = RateLimiter(per_sec=rate_limit_per_sec or HTTP.rrc_rate_limit_per_sec)
        self._client = httpx.Client(
            headers={"User-Agent": HTTP.user_agent, "Accept": "text/html,application/xhtml+xml,*/*"},
            timeout=HTTP.request_timeout_sec,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "RRCClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, RRCError)),
        wait=wait_exponential(multiplier=1.5, min=2, max=30),
        stop=stop_after_attempt(HTTP.max_retries),
        reraise=True,
    )
    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        self._rl.wait()
        url = path if path.startswith("http") else f"{self._base_url}{path}"
        resp = self._client.get(url, params=params)
        if resp.status_code == 200:
            return resp
        if resp.status_code in (404,):
            # 404 is a real answer for a missing API/well — don't retry forever
            raise RRCError(f"RRC 404 for {url} params={params}")
        # 5xx, 429 → retry via tenacity
        resp.raise_for_status()
        return resp

    # ── Health probe ────────────────────────────────────────────────────
    def health(self) -> bool:
        try:
            r = self._get(RRC_OG_RESEARCH)
            return "Texas" in r.text or "rrc.texas.gov" in r.text
        except Exception as e:
            log.warning("RRC health probe failed: %s", e)
            return False

    # ── Iterators for downstream ingestion ──────────────────────────────
    def iter_wells_by_county(self, county_fips: str) -> Iterable[Well]:
        """STUB: not yet implemented — wire to RRC well search by county.

        TX RRC well search uses RRC district + county name (not FIPS).
        Future work in Phase 1 Day 2: map FIPS → RRC district + county-name input
        and parse the HTML result table.
        """
        raise NotImplementedError(
            "iter_wells_by_county is not yet wired; using bulk-dump path "
            "instead during Phase 1 backfill."
        )
        yield  # pragma: no cover

    def iter_production_for_api(self, api_no: str) -> Iterable[ProductionMonthly]:
        """STUB: parse RRC PR data for one API number."""
        raise NotImplementedError(
            "iter_production_for_api not yet wired; uses RRC online PDQ form."
        )
        yield  # pragma: no cover

    def iter_p4_history_for_api(self, api_no: str) -> Iterable[WellOperatorHistory]:
        """STUB: parse RRC P-4 operator-of-record history for one API."""
        raise NotImplementedError(
            "iter_p4_history_for_api not yet wired; uses RRC online operator-history form."
        )
        yield  # pragma: no cover

    def lookup_operator_p5(self, p5_number: int) -> Operator | None:
        """STUB: parse the RRC P-5 operator lookup page for one P5 number."""
        raise NotImplementedError(
            "lookup_operator_p5 not yet wired; uses RRC online P-5 search form."
        )


# Async variant kept minimal for now; sync path is the primary.
async def health_async() -> bool:
    async with httpx.AsyncClient(headers={"User-Agent": HTTP.user_agent}, timeout=HTTP.request_timeout_sec) as c:
        try:
            r = await c.get(RRC_OG_RESEARCH)
            return r.status_code == 200
        except Exception:
            return False


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with RRCClient() as client:
        ok = client.health()
        print(f"RRC reachable: {ok}")
