"""Probe pagination behavior on publicsearch.us results page."""
from __future__ import annotations
import argparse, sys
from playwright.sync_api import sync_playwright


def probe(slug: str, dfrom: str, dto: str) -> None:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(viewport={"width": 1400, "height": 1200})
        page = ctx.new_page()
        page.goto(f"https://{slug}.tx.publicsearch.us/search/advanced",
                  wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(2500)

        # Date range
        for sel in ("input[name='recordedDateRange-from']",
                    "input[placeholder*='Start date']",
                    "input[aria-label*='Start']"):
            inp = page.locator(sel).first
            if inp.count() > 0:
                inp.fill(dfrom)
                break
        for sel in ("input[name='recordedDateRange-to']",
                    "input[placeholder*='End date']",
                    "input[aria-label*='End']"):
            inp = page.locator(sel).first
            if inp.count() > 0:
                inp.fill(dto)
                break
        page.locator("button:has-text('Search')").first.click()
        page.wait_for_load_state("networkidle", timeout=30_000)
        page.wait_for_timeout(3000)

        # First page row count
        rows = len(page.locator("tr").all())
        print(f"results page rows visible: {rows}")
        print(f"current URL: {page.url}")

        # Look for pagination affordances
        for sel in (
            "button[aria-label*='Next']",
            "button[aria-label*='page']",
            "[role='navigation']",
            "[class*='pagination']",
            "[class*='Pagination']",
        ):
            els = page.locator(sel).all()[:10]
            for el in els:
                try:
                    txt = (el.inner_text() or "").strip()[:120]
                    aria = el.get_attribute("aria-label") or ""
                    role = el.get_attribute("role") or ""
                    cls = el.get_attribute("class") or ""
                    print(f"  pagination element: {sel} | aria='{aria[:50]}' role='{role}' cls='{cls[:60]}' text='{txt}'")
                except Exception:
                    continue

        # Try the obvious "Next page" / Page-N buttons + tracking
        for label in ("Next", "Next page", "›", ">", "2", "3"):
            btn = page.locator(f"button:has-text('{label}'), a:has-text('{label}')").first
            if btn.count() > 0:
                aria = btn.get_attribute("aria-label") or ""
                disabled = btn.get_attribute("aria-disabled") or ""
                print(f"  found '{label}' button: aria='{aria}' disabled='{disabled}'")

        # Sample one row to see the URL link structure
        row_links = page.locator("tr a, tr [role='link']").all()[:3]
        for r in row_links:
            try:
                h = r.get_attribute("href") or ""
                t = (r.inner_text() or "").strip()[:50]
                print(f"  row link: {t} → {h}")
            except Exception:
                continue

        b.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", default="anderson")
    parser.add_argument("--from", dest="dfrom", default="01/01/2010")
    parser.add_argument("--to", dest="dto", default="05/13/2026")
    args = parser.parse_args()
    probe(args.slug, args.dfrom, args.dto)
