"""Probe publicsearch.us doc viewer for multi-page navigation primitives.

Drives one known doc page, then enumerates everything that might indicate
page count and pagination affordances:
  - text containing "of N"
  - button selectors near the viewer
  - keyboard event handler hints
  - thumbnail rail children
  - response URLs captured for image pages over a 5-second observation window

Run via:
  PYTHONPATH=src .venv/bin/python scripts/probe_doc_viewer_pagination.py \
      --slug anderson --instrument 1958-58563225
"""
from __future__ import annotations

import argparse
import json
import sys

from playwright.sync_api import sync_playwright


def probe(slug: str, instrument: str) -> dict:
    base = f"https://{slug}.tx.publicsearch.us"
    result: dict = {
        "slug": slug,
        "instrument": instrument,
        "page_count_text_candidates": [],
        "button_candidates": [],
        "image_urls_initial": [],
        "image_urls_after_keypress": [],
        "image_urls_after_thumbnail_clicks": [],
        "thumb_rail_children": 0,
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1200})
        page = context.new_page()

        captured: list[str] = []

        def on_resp(resp):
            u = resp.url
            if "/files/documents/" in u and "images" in u and (".png" in u or ".jpg" in u):
                if u not in captured:
                    captured.append(u)
        page.on("response", on_resp)

        page.goto(base, wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(1500)
        # search
        for sel in ("input[type='search']", "input[placeholder*='Search']"):
            loc = page.locator(sel).first
            if loc.count() > 0:
                loc.fill(instrument)
                loc.press("Enter")
                break
        page.wait_for_load_state("networkidle", timeout=20_000)
        page.wait_for_timeout(2500)
        for row in page.locator("tr").all():
            try:
                if instrument in (row.inner_text() or ""):
                    row.click(force=True, timeout=5_000)
                    break
            except Exception:
                continue
        page.wait_for_load_state("networkidle", timeout=15_000)
        page.wait_for_timeout(3000)

        # Page count text candidates
        body = page.locator("body").inner_text() or ""
        for chunk in body.splitlines():
            if " of " in chunk.lower() and any(c.isdigit() for c in chunk):
                result["page_count_text_candidates"].append(chunk.strip()[:120])
        result["page_count_text_candidates"] = result["page_count_text_candidates"][:20]

        # Button candidates
        for btn in page.locator("button").all()[:50]:
            try:
                txt = (btn.inner_text() or "").strip()
                aria = btn.get_attribute("aria-label") or ""
                title = btn.get_attribute("title") or ""
                if txt or aria or title:
                    result["button_candidates"].append({"text": txt[:40], "aria": aria[:40], "title": title[:40]})
            except Exception:
                continue

        result["image_urls_initial"] = list(captured)

        # Try keyboard navigation
        captured_before = set(captured)
        for _ in range(8):
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(800)
        for _ in range(4):
            page.keyboard.press("PageDown")
            page.wait_for_timeout(800)
        new_urls = [u for u in captured if u not in captured_before]
        result["image_urls_after_keypress"] = new_urls

        # Try thumbnail rail clicks
        thumb_selectors = (
            "[class*='thumb']",
            "[class*='Thumb']",
            "[role='listitem']",
            "li[class*='page']",
        )
        captured_before_thumb = set(captured)
        for sel in thumb_selectors:
            thumbs = page.locator(sel).all()
            if thumbs:
                result["thumb_rail_children"] = len(thumbs)
                for t in thumbs[:15]:
                    try:
                        t.scroll_into_view_if_needed(timeout=2_000)
                        t.click(force=True, timeout=2_000)
                        page.wait_for_timeout(700)
                    except Exception:
                        continue
                break
        result["image_urls_after_thumbnail_clicks"] = [
            u for u in captured if u not in captured_before_thumb
        ]

        browser.close()

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", default="anderson")
    parser.add_argument("--instrument", default="1958-58563225")
    args = parser.parse_args()
    out = probe(args.slug, args.instrument)
    json.dump(out, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
