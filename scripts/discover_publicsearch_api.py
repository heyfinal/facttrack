"""Discover the real publicsearch.us API endpoint by intercepting a live search.

Usage:
    python3 scripts/discover_publicsearch_api.py --county-slug anderson
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from playwright.sync_api import sync_playwright


def discover(slug: str) -> None:
    base = f"https://{slug}.tx.publicsearch.us"
    api_calls: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
        )
        page = context.new_page()

        def on_request(req):
            url = req.url
            if "/api/" in url or "search" in url or "/graphql" in url or url.endswith(".json"):
                api_calls.append({
                    "method": req.method,
                    "url": url,
                    "post_data": (req.post_data or "")[:500],
                    "headers": {k: v for k, v in (req.headers or {}).items() if k.lower() in
                                ("content-type", "accept", "x-requested-with", "x-csrf-token", "authorization")},
                })

        def on_response(resp):
            try:
                ct = (resp.headers.get("content-type") or "")
                if "application/json" in ct and (
                    "/api/" in resp.url or "search" in resp.url or "graphql" in resp.url
                ):
                    api_calls.append({
                        "kind": "response",
                        "method": resp.request.method,
                        "url": resp.url,
                        "status": resp.status,
                        "ct": ct,
                        "body_preview": resp.text()[:800] if resp.status < 400 else None,
                    })
            except Exception as e:
                api_calls.append({"kind": "response_err", "err": str(e), "url": resp.url})

        page.on("request", on_request)
        page.on("response", on_response)

        print(f"--- navigating to {base} ---", file=sys.stderr)
        page.goto(base, wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(2_000)

        # Try to use the simple search bar on the landing page
        # The site uses a search input — fill any visible text and submit
        try:
            search_box = page.locator("input[type='search'], input[placeholder*='Search']").first
            if search_box.count() > 0:
                search_box.fill("SMITH")
                search_box.press("Enter")
                page.wait_for_timeout(4_000)
        except Exception as e:
            print(f"simple-search input not found ({e}); trying /search/advanced", file=sys.stderr)

        # If no API call yet, navigate to advanced search and submit
        if not any("/api/" in c.get("url", "") for c in api_calls):
            print("--- trying advanced search ---", file=sys.stderr)
            page.goto(f"{base}/search/advanced", wait_until="networkidle", timeout=30_000)
            page.wait_for_timeout(2_000)
            # Fill the date range (recorded date)
            try:
                from datetime import date, timedelta
                from_str = (date.today() - timedelta(days=30)).strftime("%m/%d/%Y")
                to_str = date.today().strftime("%m/%d/%Y")
                date_inputs = page.locator("input[type='text']").all()
                # The advanced form has multiple date fields — just fill the first two
                for i, inp in enumerate(date_inputs):
                    if i == 0:
                        inp.fill(from_str)
                    elif i == 1:
                        inp.fill(to_str)
                # Click the search button
                page.get_by_role("button", name="Search").click()
                page.wait_for_timeout(5_000)
            except Exception as e:
                print(f"advanced search failed: {e}", file=sys.stderr)

        time.sleep(1)
        browser.close()

    # Dedupe by URL+method
    seen = set()
    unique: list[dict] = []
    for c in api_calls:
        key = (c.get("method"), c.get("url"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)

    json.dump(unique, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":  # pragma: no cover
    parser = argparse.ArgumentParser()
    parser.add_argument("--county-slug", default="anderson")
    args = parser.parse_args()
    discover(args.county_slug)
