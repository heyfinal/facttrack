"""Probe the 'Filter Document Types' combobox on publicsearch.us advanced
search — list every option offered so we can map our O&G categories to it."""
from __future__ import annotations
import argparse, json, sys
from playwright.sync_api import sync_playwright


def probe(slug: str) -> dict:
    out: dict = {"slug": slug, "options": [], "raw_panel_text": ""}
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(viewport={"width": 1400, "height": 1200})
        page = ctx.new_page()
        page.goto(f"https://{slug}.tx.publicsearch.us/search/advanced",
                  wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(2500)

        # Click the Filter Document Types combobox
        cb = page.locator("[aria-label='Filter Document Types']").first
        if cb.count() == 0:
            out["error"] = "filter combobox not found"
            b.close()
            return out
        try:
            cb.click()
            page.wait_for_timeout(1500)
        except Exception as e:
            out["error"] = f"click failed: {e}"
            b.close()
            return out

        # Try typing nothing to expand all
        try:
            cb.focus()
            page.keyboard.press("ArrowDown")
            page.wait_for_timeout(800)
        except Exception:
            pass

        # Capture all visible listbox option text
        for el in page.locator("[role='option']").all()[:200]:
            try:
                t = (el.inner_text() or "").strip()
                if t:
                    out["options"].append(t)
            except Exception:
                continue

        # Type partial filter terms to expose the full list
        for term in ("OIL", "LEASE", "ASSIGNMENT", "RELEASE", "AOH",
                     "AFFIDAVIT", "HEIRSHIP", "PROBATE", "ROYALTY",
                     "POOLED", "RATIFICATION", "MEMORANDUM", "TOP"):
            try:
                cb.fill("")
                cb.type(term, delay=30)
                page.wait_for_timeout(800)
            except Exception:
                continue
            for el in page.locator("[role='option']").all()[:50]:
                try:
                    t = (el.inner_text() or "").strip()
                    if t and t not in out["options"]:
                        out["options"].append(t)
                except Exception:
                    continue

        # Grab the side-panel raw text for debugging
        try:
            out["raw_panel_text"] = (page.locator("body").inner_text() or "")[:3000]
        except Exception:
            pass
        b.close()
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", default="leon")
    args = parser.parse_args()
    out = probe(args.slug)
    out["options"] = sorted(set(out["options"]))
    print(json.dumps(out, indent=2))
