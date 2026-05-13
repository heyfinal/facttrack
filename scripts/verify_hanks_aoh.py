"""Search Anderson County publicsearch.us for any AOH / probate / record-of-probate
recorded against C.W. Hanks (or Estate of C.W. Hanks). If anything shows up, the
r02_probate_gap finding on lease 1958-58563225 is a false positive.

Run via:  PYTHONPATH=src .venv/bin/python scripts/verify_hanks_aoh.py
"""
from __future__ import annotations

import sys
from playwright.sync_api import sync_playwright


def search(query: str) -> list[dict]:
    """Run a publicsearch.us search and return all result rows."""
    rows: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        page.goto("https://anderson.tx.publicsearch.us", wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(1500)

        si = None
        for sel in ("input[type='search']", "input[placeholder*='Search']"):
            loc = page.locator(sel).first
            if loc.count() > 0:
                si = loc
                break
        if si is None:
            print(f"  no search input found for {query!r}")
            browser.close()
            return rows
        si.fill(query)
        si.press("Enter")
        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        page.wait_for_timeout(2500)

        for tr in page.locator("tr").all():
            try:
                txt = (tr.inner_text() or "").strip()
            except Exception:
                continue
            if not txt or txt.startswith("GRANTOR"):
                continue
            # Skip header / empty rows; capture rows that look like result rows
            if "\t" in txt or "  " in txt:
                rows.append({"text": " | ".join(txt.split()[:25])[:300]})
        browser.close()
    return rows


def main() -> int:
    queries = [
        "HANKS C W",
        "Hanks Estate",
        "C W Hanks deceased",
        "Hanks affidavit",
        "Hanks probate",
        "Zula Hanks",
    ]
    found_anything = False
    for q in queries:
        print(f"\n=== Searching Anderson for: {q!r} ===")
        rows = search(q)
        if not rows:
            print("  (no rows)")
        else:
            found_anything = True
            for r in rows[:25]:
                print(f"  → {r['text']}")
    print()
    if not found_anything:
        print("CONCLUSION: no Hanks-related instruments found on publicsearch.us Anderson.")
        print("  r02_probate_gap finding stands.")
    else:
        print("CONCLUSION: review the rows above. Any AOH, probate, or record-of-probate")
        print("naming C.W. Hanks would invalidate r02_probate_gap.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
