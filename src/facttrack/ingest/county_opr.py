"""County Official Public Records (OPR) scraping.

East-Texas county clerk OPR portals expose:
- Document search by grantor / grantee / type / date range / volume-page
- Image viewing (TIFF / PDF) of the recorded instrument
- Some support full-text OCR search

We use Playwright (headless Chromium) because most OPR platforms (Tyler Tech iDox,
CourthouseDirect, etc.) are heavily JavaScript-driven and don't expose a usable HTTP API.

This module provides a generic scraping framework + county-specific drivers.

Anderson (FIPS 48001) and Houston (FIPS 48225) county OPR access methods are
verified on Phase 1 Day 1; until then this module stubs and logs a clear
"NEEDS DAY-1 VERIFICATION" warning.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Iterator

from playwright.sync_api import Browser, Page, sync_playwright

from facttrack.config import COUNTIES, HTTP
from facttrack.models import ChainEvent, Lease

log = logging.getLogger(__name__)


@dataclass
class OPRSearchCriteria:
    """Narrow the search to a manageable result set."""
    county_fips: str
    grantor: str | None = None
    grantee: str | None = None
    instrument_types: list[str] = field(default_factory=list)
    recorded_from: date | None = None
    recorded_to: date | None = None
    abstract_no: str | None = None
    survey: str | None = None
    section_block: str | None = None
    max_results: int = 500


@dataclass
class OPRDocument:
    county_fips: str
    instrument_no: str | None
    recording_date: date | None
    instrument_type: str | None
    grantor: str | None
    grantee: str | None
    book_page: str | None
    image_url: str | None
    raw_metadata: dict


class CountyOPRScraper:
    """Base class for a per-county OPR scraper.

    Subclasses implement search(), get_document(), and download_image().
    """

    county_fips: str = ""
    platform: str = ""

    def __init__(self, browser: Browser) -> None:
        self._browser = browser

    def _new_page(self) -> Page:
        ctx = self._browser.new_context(
            user_agent=HTTP.user_agent,
            viewport={"width": 1280, "height": 1024},
        )
        ctx.set_default_timeout(int(HTTP.request_timeout_sec * 1000))
        return ctx.new_page()

    def search(self, criteria: OPRSearchCriteria) -> Iterator[OPRDocument]:
        raise NotImplementedError

    def get_document(self, doc: OPRDocument) -> Lease | ChainEvent | None:
        raise NotImplementedError


class TylerTechIDoxScraper(CountyOPRScraper):
    """Generic Tyler Tech iDox / Odyssey OPR scraper.

    Anderson + Houston counties (and many TX counties) run this platform.
    The Phase-1-Day-1 verification step resolves the exact subdomain + form
    selectors for each county and writes them into per-county config.
    """

    platform = "tyler_tech_idox"

    def __init__(self, browser: Browser, base_url: str) -> None:
        super().__init__(browser)
        self._base_url = base_url

    def search(self, criteria: OPRSearchCriteria) -> Iterator[OPRDocument]:
        log.warning(
            "TylerTechIDoxScraper.search() is a stub. "
            "Day-1 verification of %s OPR portal must record concrete form "
            "selectors before this returns real data.",
            COUNTIES.get(criteria.county_fips, "<unknown>")
        )
        # No-op generator until Day-1 verification fills in the selectors.
        return
        yield  # pragma: no cover

    def get_document(self, doc: OPRDocument) -> Lease | ChainEvent | None:
        log.warning(
            "TylerTechIDoxScraper.get_document() is a stub for instrument %s",
            doc.instrument_no,
        )
        return None


class AndersonCountyOPRScraper(TylerTechIDoxScraper):
    county_fips = "48001"

    def __init__(self, browser: Browser) -> None:
        cfg = COUNTIES[self.county_fips]
        super().__init__(browser=browser, base_url=cfg.opr_base_url or "")


class HoustonCountyOPRScraper(TylerTechIDoxScraper):
    county_fips = "48225"

    def __init__(self, browser: Browser) -> None:
        cfg = COUNTIES[self.county_fips]
        super().__init__(browser=browser, base_url=cfg.opr_base_url or "")


SCRAPER_REGISTRY: dict[str, type[CountyOPRScraper]] = {
    "48001": AndersonCountyOPRScraper,
    "48225": HoustonCountyOPRScraper,
}


def get_scraper(county_fips: str, browser: Browser) -> CountyOPRScraper:
    cls = SCRAPER_REGISTRY.get(county_fips)
    if cls is None:
        raise KeyError(f"No OPR scraper registered for county FIPS {county_fips}")
    return cls(browser=browser)


def open_browser():
    """Context manager helper for Playwright; returns a sync_playwright handle."""
    return sync_playwright()


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with open_browser() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for fips in ("48001", "48225"):
                cfg = COUNTIES[fips]
                log.info("County %s (%s) OPR base_url=%r — DAY-1 VERIFICATION REQUIRED",
                         fips, cfg.name, cfg.opr_base_url)
                scraper = get_scraper(fips, browser)
                # iterate over a dummy criteria to confirm wiring works
                list(scraper.search(OPRSearchCriteria(county_fips=fips, max_results=1)))
        finally:
            browser.close()
