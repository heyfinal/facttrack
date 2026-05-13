"""Discover the publicsearch.us document-view flow via interactive search.

The publicsearch.us SPA needs a real interactive search to render results;
deep-link URLs don't kick off the search itself. We drive the homepage
search box programmatically, then inspect what the detail page exposes.
"""
from __future__ import annotations

import argparse
import json
import sys

from playwright.sync_api import sync_playwright


def discover(slug: str, query: str) -> dict:
    base = f"https://{slug}.tx.publicsearch.us"
    result: dict = {
        "slug": slug,
        "query": query,
        "homepage_url": base,
        "results_url": None,
        "first_result_text": None,
        "detail_url": None,
        "detail_text_sample": None,
        "image_links": [],
        "download_buttons": [],
        "paywall_signals": [],
        "captured_xhr": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
            viewport={"width": 1920, "height": 1080},
            accept_downloads=True,
        )
        page = context.new_page()

        page.on("response", lambda resp: result["captured_xhr"].append({
            "url": resp.url[:200],
            "status": resp.status,
            "ct": (resp.headers.get("content-type") or "")[:60],
        }) if (
            "/api/" in resp.url
            or "documents" in resp.url.lower()
            or "view" in resp.url
            or any(resp.url.lower().endswith(ext) for ext in (".tif", ".tiff", ".png", ".jpg", ".pdf"))
        ) else None)

        # Homepage with search box
        page.goto(base, wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(2000)

        # Find the search input
        search_input = None
        for sel in (
            "input[type='search']",
            "input[placeholder*='Search']",
            "input[placeholder*='search']",
            "input[name*='search']",
            "input[aria-label*='Search']",
        ):
            loc = page.locator(sel).first
            if loc.count() > 0:
                search_input = loc
                break
        if search_input is None:
            result["error"] = "no search input found on homepage"
            browser.close()
            return result

        search_input.fill(query)
        search_input.press("Enter")
        page.wait_for_load_state("networkidle", timeout=20_000)
        page.wait_for_timeout(3000)
        result["results_url"] = page.url

        # Find the first result row + try multiple click strategies.
        # publicsearch.us results rows are not anchors — click handlers are on
        # the <tr> itself (htmx). Click the row, the cell, the inner span,
        # whatever fires the navigation.
        results_rows = page.locator("tr").all()
        first_clicked = False
        for row in results_rows:
            try:
                txt = (row.inner_text() or "").strip()
            except Exception:
                continue
            if not txt or txt.startswith("GRANTOR"):  # header row
                continue
            if query.upper() in txt.upper() or len(txt) > 60:
                result["first_result_text"] = txt[:200]
                # Try clicking strategies in order
                for click_target in (
                    row,                                 # tr itself
                    row.locator("td").nth(2),            # 3rd cell (usually doc type)
                    row.locator("td").first,             # 1st cell
                    row.locator("span, button").first,   # any inner span/button
                ):
                    try:
                        if click_target.count() > 0:
                            click_target.click(force=True, timeout=5_000)
                            page.wait_for_load_state("networkidle", timeout=10_000)
                            if page.url != result["results_url"]:
                                first_clicked = True
                                break
                    except Exception:
                        continue
                if first_clicked:
                    break

        if first_clicked:
            page.wait_for_load_state("networkidle", timeout=20_000)
            page.wait_for_timeout(3000)

        result["detail_url"] = page.url

        # Inspect detail page
        try:
            body_text = (page.locator("body").inner_text() or "")
        except Exception:
            body_text = ""
        result["detail_text_sample"] = body_text[:2000]

        # Image / PDF links
        for a in page.locator("a[href]").all():
            try:
                href = a.get_attribute("href") or ""
                text = (a.inner_text() or "").strip()
            except Exception:
                continue
            href_l = href.lower()
            if any(k in href_l for k in (".tif", ".tiff", ".png", ".jpg", ".pdf", "image", "view", "download", "imageview")):
                result["image_links"].append({"href": href[:200], "text": text[:60]})

        # Download buttons
        for btn in page.locator("button").all():
            try:
                text = (btn.inner_text() or "").strip()
            except Exception:
                continue
            if any(k in text.lower() for k in ("download", "view image", "open image", "view doc", "open doc", "buy", "purchase")):
                try:
                    disabled = bool(btn.get_attribute("disabled"))
                except Exception:
                    disabled = False
                result["download_buttons"].append({"text": text[:60], "disabled": disabled})

        # Paywall signals
        for sig in ("Buy Image", "Purchase", "Pay", "Sign In to Download",
                    "Subscribe", "Credits", "Tokens", "Insufficient",
                    "Add to Cart", "$"):
            if sig in body_text:
                result["paywall_signals"].append(sig)

        browser.close()

    # Dedupe captured xhr by url
    seen = set()
    deduped = []
    for x in result["captured_xhr"]:
        if x["url"] in seen:
            continue
        seen.add(x["url"])
        deduped.append(x)
    result["captured_xhr"] = deduped[:20]

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", default="anderson")
    parser.add_argument("--query", default="SHELL OIL")
    args = parser.parse_args()
    out = discover(args.slug, args.query)
    json.dump(out, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
