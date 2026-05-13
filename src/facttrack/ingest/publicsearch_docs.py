"""publicsearch.us document-image fetcher.

For each lease / chain-event row in the DB that came from publicsearch.us, we:
  1. Search the instrument by document number (which publicsearch indexes)
  2. Click the matching result row to land on `/doc/{doc_id}`
  3. Scrape the signed PNG image URL(s) for every page of the document
  4. Download the PNGs to ~/facttrack/cache/lease_images/{county_fips}/{instrument_no}/

These signed URLs (`?exp=&sig=`) expire ~24 hours after issuance, so we
download promptly and cache. Anonymous access is sufficient — no sign-in
required. The "Add to Cart" button on the detail page is for printable
PDF / multi-doc exports and does NOT gate the per-page image view.

Each downloaded PNG is referenced from the `lease.parsed_metadata` or
`chain_event.parsed_metadata` JSON (key `image_paths`).
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

from facttrack.config import HTTP, PATHS, ensure_dirs
from facttrack.db import cursor
from facttrack.ingest.publicsearch import PUBLICSEARCH_TX_COUNTIES

log = logging.getLogger(__name__)


@dataclass
class DocFetchResult:
    county_fips: str
    instrument_no: str
    doc_id: str | None
    image_paths: list[Path]
    page_count: int
    error: str | None = None


def _county_image_dir(county_fips: str) -> Path:
    base = PATHS.cache / "lease_images" / county_fips
    base.mkdir(parents=True, exist_ok=True)
    return base


def _search_and_open_doc(page: Page, instrument_no: str, base_url: str) -> bool:
    """Drive the homepage search, click the matching row. Returns True on /doc/ landing."""
    page.goto(base_url, wait_until="networkidle", timeout=30_000)
    page.wait_for_timeout(1500)
    search_input = None
    for sel in (
        "input[type='search']",
        "input[placeholder*='Search']",
        "input[aria-label*='Search']",
        "input[name*='search']",
    ):
        loc = page.locator(sel).first
        if loc.count() > 0:
            search_input = loc
            break
    if search_input is None:
        return False
    search_input.fill(instrument_no)
    search_input.press("Enter")
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeout:
        pass
    page.wait_for_timeout(2500)

    # Click the row that contains our instrument number.
    for row in page.locator("tr").all():
        try:
            txt = (row.inner_text() or "")
        except Exception:
            continue
        if instrument_no not in txt:
            continue
        # Direct row click — htmx navigation
        for target in (row, row.locator("td").nth(2), row.locator("td").first):
            try:
                if target.count() > 0:
                    target.click(force=True, timeout=5_000)
                    page.wait_for_load_state("networkidle", timeout=15_000)
                    if "/doc/" in page.url:
                        return True
            except Exception:
                continue
    return False


def _collect_image_urls(page: Page, captured_xhr: list[str]) -> list[str]:
    """Find all signed image URLs for every page of the document.

    publicsearch.us viewer behavior:
      - Default view is single-page; the main image is rendered at full
        resolution (`_N.png` ≈ 200-300KB) — tesseract OCRs it cleanly.
      - "Go To Next Page" (aria-label='Go To Next Page') advances and
        fires a fresh XHR for the next full-res image. Repeat until the
        button is disabled or the URL list stops growing.
      - "Multi Page View" loads every page in parallel but as 300-pixel
        thumbnails (`_N_r-300.png` ≈ 60-75KB) which are too low-res for
        tesseract — used only as a page-count probe / fallback.

    Strategy: click Next Page until no new image XHR appears for 2 cycles
    (or 20 clicks max — protects against runaway). Fall back to MPV to
    discover page count if Next Page is absent.
    """
    urls: list[str] = list(captured_xhr)

    max_clicks = 20
    stagnant_streak = 0
    for click_idx in range(max_clicks):
        np = page.locator("button[aria-label='Go To Next Page']").first
        if np.count() == 0:
            break
        # is the button disabled?
        try:
            disabled = np.get_attribute("disabled")
            aria_disabled = np.get_attribute("aria-disabled") == "true"
            if disabled is not None or aria_disabled:
                break
        except Exception:
            pass
        before = len(captured_xhr)
        try:
            np.click(force=True, timeout=3_000)
            page.wait_for_timeout(1500)
        except Exception:
            break
        if len(captured_xhr) == before:
            stagnant_streak += 1
            if stagnant_streak >= 2:
                break
        else:
            stagnant_streak = 0
            urls = list(captured_xhr)

    # If we got just one page (no Next-Page found), try MPV as a fallback
    # — at least we'll have low-res page-count evidence.
    full_res_count = sum(1 for u in urls if "_r-300" not in u)
    if full_res_count <= 1:
        mpv = page.locator("button[aria-label='Multi Page View']").first
        if mpv.count() > 0:
            try:
                mpv.click(force=True, timeout=3_000)
                page.wait_for_timeout(4500)
                urls = list(captured_xhr)
            except Exception as e:
                log.debug("MPV fallback failed: %s", e)

    # De-dupe: if we have both `_N.png` and `_N_r-300.png` for the same page,
    # prefer the full-res. The base image_id is the digits before `_N`.
    full_res_pages: dict[str, str] = {}
    r300_pages: dict[str, str] = {}
    other: list[str] = []
    for u in urls:
        m = re.search(r"(?P<imgid>\d+)_(?P<page>\d+)(?P<r300>_r-300)?\.(?:png|jpg)", u)
        if not m:
            other.append(u)
            continue
        key = f"{m.group('imgid')}_p{m.group('page')}"
        if m.group("r300"):
            r300_pages[key] = u
        else:
            full_res_pages[key] = u
    deduped: list[str] = []
    # full-res first
    for k, u in sorted(full_res_pages.items()):
        deduped.append(u)
    # r-300 fallback for pages not in full-res
    for k, u in sorted(r300_pages.items()):
        if k not in full_res_pages:
            deduped.append(u)
    deduped.extend(other)
    return deduped


def _download_signed_image_via_context(context, url: str, dest: Path) -> int:
    """Download a signed image URL using the live Playwright context — session
    cookies + referer are preserved automatically. httpx outside the context
    receives 'Unauthorized.' for the same URL.

    Uses a 60s timeout to accommodate large multi-page PNG fetches when the
    publicsearch.us CDN is under load. Includes a single retry."""
    last_err: Exception | None = None
    for attempt in (1, 2):
        try:
            resp = context.request.get(url, timeout=60_000)
            if resp.status != 200:
                raise RuntimeError(f"image download returned {resp.status} from {url[:120]}")
            body = resp.body()
            if len(body) < 100 or not (body.startswith(b"\x89PNG") or body.startswith(b"\xff\xd8")):
                raise RuntimeError(f"image content invalid ({len(body)} bytes; first bytes={body[:30]!r})")
            dest.write_bytes(body)
            return len(body)
        except Exception as e:
            last_err = e
            if attempt == 1:
                log.info("download retry for %s: %s", url[:80], e)
                continue
    raise last_err  # type: ignore[misc]


def fetch_document_images(
    county_fips: str,
    instrument_no: str,
    *,
    headless: bool = True,
) -> DocFetchResult:
    slug = PUBLICSEARCH_TX_COUNTIES.get(county_fips)
    if not slug:
        raise KeyError(f"county {county_fips} not on publicsearch.us")
    base = f"https://{slug}.tx.publicsearch.us"
    image_dir = _county_image_dir(county_fips) / re.sub(r"[^A-Za-z0-9._-]", "_", instrument_no)
    image_dir.mkdir(parents=True, exist_ok=True)

    image_paths: list[Path] = []
    doc_id: str | None = None
    error: str | None = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            context = browser.new_context(
                user_agent=HTTP.user_agent,
                viewport={"width": 1920, "height": 1080},
                accept_downloads=True,
            )
            page = context.new_page()

            captured_image_urls: list[str] = []

            def on_resp(resp):
                u = resp.url
                if "/files/documents/" in u and "images" in u and (".png" in u or ".jpg" in u):
                    if u not in captured_image_urls:
                        captured_image_urls.append(u)
            page.on("response", on_resp)

            opened = _search_and_open_doc(page, instrument_no, base)
            if not opened:
                browser.close()
                return DocFetchResult(
                    county_fips=county_fips, instrument_no=instrument_no,
                    doc_id=None, image_paths=[], page_count=0,
                    error="could not navigate to /doc/ page",
                )

            m = re.search(r"/doc/([^/?#]+)", page.url)
            doc_id = m.group(1) if m else None
            page.wait_for_timeout(2500)

            # Gather URLs from DOM + captured XHR (Multi-Page View clicked inside)
            img_urls = _collect_image_urls(page, captured_image_urls)

            # Download EACH image WHILE THE SESSION IS LIVE.
            # Use the page-number embedded in the URL (`..._N.png` or `..._N_r-300.png`)
            # as the filename so multi-page docs read in correct page order downstream.
            for url in img_urls:
                ext = ".png" if ".png" in url else (".jpg" if ".jpg" in url else ".bin")
                m = re.search(r"_(\d+)(?:_r-\d+)?\.(?:png|jpg)", url)
                page_no = int(m.group(1)) if m else len(image_paths) + 1
                is_r300 = "_r-300" in url
                suffix = "_r300" if is_r300 else ""
                dest = image_dir / f"page_{page_no:02d}{suffix}{ext}"
                try:
                    size = _download_signed_image_via_context(context, url, dest)
                    log.info("downloaded %s (%d bytes) → %s", urlparse(url).path[:60], size, dest)
                    image_paths.append(dest)
                except Exception as e:
                    log.warning("image download failed for %s: %s", url[:80], e)
                    error = error or f"download failed: {e}"
        finally:
            browser.close()

    # Persist image path reference into lease.parsed_metadata
    if image_paths:
        with cursor(dict_rows=False) as cur:
            cur.execute(
                """
                UPDATE lease
                   SET parsed_metadata = jsonb_set(
                         parsed_metadata,
                         '{image_paths}',
                         to_jsonb(%s::text[]),
                         true
                       )
                 WHERE county_fips = %s AND opr_instrument_no = %s
                """,
                ([str(p) for p in image_paths], county_fips, instrument_no),
            )

    return DocFetchResult(
        county_fips=county_fips,
        instrument_no=instrument_no,
        doc_id=doc_id,
        image_paths=image_paths,
        page_count=len(image_paths),
        error=error,
    )


def fetch_all_leases_for_county(
    county_fips: str,
    *,
    max_leases: int = 50,
    sleep_between_sec: float = 1.5,
) -> dict[str, int]:
    """Fetch document images for every lease in `county_fips` that doesn't already have them."""
    counts = {"attempted": 0, "succeeded": 0, "failed": 0, "skipped_already_have": 0}
    with cursor() as cur:
        cur.execute(
            """
            SELECT opr_instrument_no, parsed_metadata
            FROM lease
            WHERE county_fips = %s AND opr_instrument_no IS NOT NULL
            ORDER BY recording_date DESC NULLS LAST
            LIMIT %s
            """,
            (county_fips, max_leases),
        )
        rows = cur.fetchall()

    for row in rows:
        instrument_no = row["opr_instrument_no"]
        meta = row.get("parsed_metadata") or {}
        if isinstance(meta, dict) and meta.get("image_paths"):
            counts["skipped_already_have"] += 1
            continue
        counts["attempted"] += 1
        try:
            result = fetch_document_images(county_fips, instrument_no)
            if result.image_paths:
                counts["succeeded"] += 1
                log.info("[%s] %s → %d pages", county_fips, instrument_no, result.page_count)
            else:
                counts["failed"] += 1
                log.warning("[%s] %s → no images (%s)", county_fips, instrument_no, result.error)
        except Exception as e:
            counts["failed"] += 1
            log.warning("[%s] %s → exception: %s", county_fips, instrument_no, e)
        time.sleep(sleep_between_sec)

    log.info("fetch summary: %s", counts)
    return counts


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--county", required=True)
    parser.add_argument("--max", type=int, default=20)
    parser.add_argument("--instrument", help="fetch a single instrument instead of bulk")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.instrument:
        result = fetch_document_images(args.county, args.instrument)
        print(json.dumps({
            "instrument": result.instrument_no,
            "doc_id": result.doc_id,
            "page_count": result.page_count,
            "image_paths": [str(p) for p in result.image_paths],
            "error": result.error,
        }, indent=2))
    else:
        result = fetch_all_leases_for_county(args.county, max_leases=args.max)
        print(json.dumps(result, indent=2))
