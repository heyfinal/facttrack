"""TX RRC bulk-data ingest via GoAnywhere MFT (mft.rrc.texas.gov).

The Railroad Commission publishes its bulk datasets through a GoAnywhere MFT
(Managed File Transfer) portal at `mft.rrc.texas.gov`. Each documented
dataset has a shareable `/link/<uuid>` URL that lands on a JSF-driven file
browser. To actually download the file we have to:

  1. Open the link page in a real browser (Playwright)
  2. Select the file row checkbox
  3. Click the "Download" JSF submit
  4. Capture the resulting download response (browser context handles cookies + form state)
  5. Stream the bytes to disk

We do all of this within Playwright's `page.expect_download()` context so
the framework handles the binary response correctly regardless of redirects
or chunked encoding.

This is the only legal automated path the RRC sanctions for bulk data; PDQ
scraping is explicitly prohibited and will get the session terminated.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import (
    Download, Page, TimeoutError as PlaywrightTimeout, sync_playwright,
)

from facttrack.config import HTTP, PATHS, ensure_dirs

log = logging.getLogger(__name__)


# Public RRC bulk-dataset MFT link URLs as published at
# https://www.rrc.texas.gov/resource-center/research/data-sets-available-for-download/
RRC_MFT_LINKS: dict[str, dict] = {
    "wellbore_query": {
        "url": "https://mft.rrc.texas.gov/link/650649b7-e019-4d77-a8e0-d118d6455381",
        "expected_filename_prefix": "OG_WELLBORE_EWA_Report",
        "format": "csv",
        "description": "Wellbore Query Data — all wells, county/operator/lease/field",
    },
    "p5_organization": {
        "url": "https://mft.rrc.texas.gov/link/04652169-eed6-4396-9019-2e270e790f6c",
        "expected_filename_prefix": "OG_P5_ORG",
        "format": "ascii_or_ebcdic",
        "description": "P5 Organization — all RRC-registered operator entities",
    },
    "production_data_query_dump": {
        "url": "https://mft.rrc.texas.gov/link/1f5ddb8d-329a-4459-b7f8-177b4f5ee60d",
        "expected_filename_prefix": "OG_PDQ",
        "format": "csv",
        "description": "Production Data Query Dump — 1993 to current",
    },
    "p4_database": {
        "url": "https://mft.rrc.texas.gov/link/19f9b9c7-2b82-4d7c-8dbd-77145a86d3de",
        "expected_filename_prefix": "OG_P4",
        "format": "ebcdic",
        "description": "Certificate of Authorization P-4 Database",
    },
    "statewide_api_data": {
        "url": "https://mft.rrc.texas.gov/link/701db9a3-32b5-488d-812b-cd6ff7d0fe85",
        "expected_filename_prefix": "OG_API",
        "format": "ascii",
        "description": "Statewide API Data — well API numbers + survey/well numbers",
    },
}


@dataclass
class MFTDownloadResult:
    dataset: str
    filename: str
    bytes_downloaded: int
    local_path: Path


def _find_target_file_row(page: Page, prefix: str) -> str | None:
    """Locate the most recent file row matching the dataset prefix.

    Returns the row's `data-rk` / `id` attribute that we can use to scope
    the checkbox click.
    """
    # The latest non-dated file is named exactly "<prefix>.csv" (or .dat / .txt).
    # All historical snapshots have a YYYY-MM-DD suffix.
    return page.evaluate("""
        (prefix) => {
            const rows = Array.from(document.querySelectorAll('tr'));
            const target = rows.find(r => {
                const txt = r.innerText || '';
                if (!txt.includes(prefix)) return false;
                // exclude historical dated rows
                return !/_20[0-9]{2}-[0-9]{2}-[0-9]{2}\\./.test(txt);
            });
            return target ? (target.id || target.getAttribute('data-rk') || null) : null;
        }
    """, prefix)


def _pick_target_row(page: Page, prefix: str):
    """Return a Playwright Locator for the file row matching `prefix`.

    Prefers the always-latest snapshot (no YYYY-MM-DD suffix); falls back to
    the lexicographically-greatest dated snapshot if absent.
    """
    rows = page.locator("tr").all()
    # Stage 1: exact-latest (no date suffix)
    for row in rows:
        try:
            txt = (row.inner_text() or "").strip()
        except Exception:
            continue
        if prefix not in txt:
            continue
        if re.search(rf"{re.escape(prefix)}_20\d{{2}}-\d{{2}}-\d{{2}}\.", txt):
            continue
        log.info("target row (latest snapshot) matched: %s", txt[:80])
        return row
    # Stage 2: most-recent dated snapshot
    candidates = []
    for row in rows:
        try:
            txt = (row.inner_text() or "").strip()
        except Exception:
            continue
        m = re.search(rf"{re.escape(prefix)}_(20\d{{2}}-\d{{2}}-\d{{2}})\.", txt)
        if m:
            candidates.append((m.group(1), row, txt))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        log.info("target row (latest dated snapshot %s) matched: %s",
                 candidates[0][0], candidates[0][2][:80])
        return candidates[0][1]
    return None


def download_mft_dataset(
    dataset_key: str,
    *,
    headless: bool = True,
    timeout_ms: int = 300_000,
) -> MFTDownloadResult:
    """Download the latest snapshot of an RRC MFT dataset to local cache."""
    if dataset_key not in RRC_MFT_LINKS:
        raise KeyError(
            f"unknown RRC MFT dataset {dataset_key!r}; known: {list(RRC_MFT_LINKS)}"
        )
    spec = RRC_MFT_LINKS[dataset_key]
    ensure_dirs()
    cache_dir = PATHS.cache / "rrc_mft"
    cache_dir.mkdir(parents=True, exist_ok=True)

    log.info("RRC MFT download: %s (%s)", dataset_key, spec["url"])
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            context = browser.new_context(
                user_agent=HTTP.user_agent,
                accept_downloads=True,
                viewport={"width": 1920, "height": 4000},  # tall + wide so GoDrive table fits
            )
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            log.info("loading MFT page %s", spec["url"])
            page.goto(spec["url"], wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(2000)
            log.info("MFT page loaded, %d rows visible", page.locator("tr").count())

            # Find the target row + its checkbox via Playwright locator.
            target_row = _pick_target_row(page, spec["expected_filename_prefix"])
            if target_row is None:
                raise RuntimeError(
                    f"could not locate any file row matching prefix {spec['expected_filename_prefix']}"
                )
            log.info("target row located")

            checkbox = target_row.locator("input[type='checkbox']").first
            if checkbox.count() == 0:
                raise RuntimeError("target row has no checkbox")
            # GoDrive's JSF binding fires on native click; row checkboxes
            # commonly sit in a sticky column off-screen, so we explicitly
            # scroll the row into view and then click via JS (force is not
            # enough — Playwright's actionability check still bounces on
            # off-viewport elements).
            target_row.scroll_into_view_if_needed(timeout=10_000)
            page.wait_for_timeout(500)
            checkbox.evaluate("el => { el.scrollIntoView({block: 'center'}); el.click(); }")
            page.wait_for_timeout(2500)  # let JSF AJAX update the toolbar

            # Strategy 1: click by JSF id pattern (Download buttons share suffix).
            # The button id we saw in Chrome MCP was `j_id_3f:j_id_3f` but JSF
            # regenerates these per session, so we match by the `:j_id_*` pattern
            # AND the visible "Download" label.
            log.info("locating Download button via multiple strategies")
            clicked = False
            with page.expect_download(timeout=timeout_ms) as dl_info:
                # Strategy A: visible text + visible state filter
                for sel in (
                    "button:visible:has-text('Download')",
                    "button[id*='j_id'][type='submit']:has-text('Download')",
                    "input[type='submit'][value='Download']",
                ):
                    try:
                        btn = page.locator(sel).first
                        if btn.count() > 0:
                            btn.click(force=True, timeout=5_000)
                            clicked = True
                            log.info("clicked Download via selector: %s", sel)
                            break
                    except Exception as e:
                        log.info("selector %s failed (%s); trying next", sel, e)
                # Strategy B: JS-driven JSF form submit if click didn't fire
                if not clicked:
                    log.info("falling back to JSF form submission via JavaScript")
                    submitted = page.evaluate("""
                        () => {
                            const btn = Array.from(document.querySelectorAll('button, input[type="submit"]'))
                                .find(el => (el.innerText || el.value || '').trim() === 'Download');
                            if (!btn) return 'no-button-found';
                            // JSF buttons inside a <form> — find the enclosing form
                            let form = btn.closest('form');
                            if (!form) return 'no-form';
                            // Click via JS dispatch
                            btn.click();
                            return 'js-clicked';
                        }
                    """)
                    log.info("JS click result: %s", submitted)
                    clicked = submitted == 'js-clicked'
                if not clicked:
                    raise RuntimeError("could not initiate Download via any strategy")
            download: Download = dl_info.value
            suggested = download.suggested_filename or f"{spec['expected_filename_prefix']}.csv"
            target_path = cache_dir / suggested
            download.save_as(str(target_path))
            size = target_path.stat().st_size if target_path.exists() else 0
            log.info("RRC MFT downloaded %s (%d bytes) → %s", suggested, size, target_path)
            return MFTDownloadResult(
                dataset=dataset_key,
                filename=suggested,
                bytes_downloaded=size,
                local_path=target_path,
            )
        finally:
            browser.close()


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=list(RRC_MFT_LINKS), required=True)
    parser.add_argument("--no-headless", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = download_mft_dataset(args.dataset, headless=not args.no_headless)
    print(f"downloaded {result.filename} ({result.bytes_downloaded:,} bytes) to {result.local_path}")
