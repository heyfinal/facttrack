"""Probe publicsearch.us advanced search for a document-type / category filter
that we can use to scrape only oil-and-gas instruments instead of pulling
every mortgage and lien release in the date window.

Dumps the form structure of /search/advanced for the configured county.
"""
from __future__ import annotations
import argparse, json, sys
from playwright.sync_api import sync_playwright


def probe(slug: str) -> dict:
    out: dict = {"slug": slug, "selects": [], "doc_type_options": [], "buttons": []}
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(viewport={"width": 1400, "height": 1000})
        page = ctx.new_page()
        page.goto(f"https://{slug}.tx.publicsearch.us/search/advanced",
                  wait_until="networkidle", timeout=30_000)
        page.wait_for_timeout(2500)

        # All <select> elements
        for sel in page.locator("select").all()[:30]:
            try:
                name = sel.get_attribute("name") or ""
                aria = sel.get_attribute("aria-label") or ""
                opts = [o.inner_text().strip() for o in sel.locator("option").all()[:80]]
                out["selects"].append({"name": name, "aria": aria, "options": opts[:30]})
                if any(kw in (name + aria).lower() for kw in
                       ("doc", "type", "category", "instrument")):
                    out["doc_type_options"].append({"name": name, "aria": aria,
                                                    "options": opts})
            except Exception as e:
                out["selects"].append({"error": str(e)})

        # Multi-selects via React (often rendered as button group, listbox)
        for el in page.locator("[role='combobox'], [role='listbox']").all()[:20]:
            try:
                txt = (el.inner_text() or "")[:120]
                aria = el.get_attribute("aria-label") or ""
                out["selects"].append({"role-combobox": True, "text": txt, "aria": aria})
            except Exception:
                continue

        # Common publicsearch.us pattern: a "Document Type" multi-select that's
        # actually a search-as-you-type chip input.
        for inp in page.locator("input[type='text']").all()[:30]:
            try:
                name = inp.get_attribute("name") or ""
                ph = inp.get_attribute("placeholder") or ""
                aria = inp.get_attribute("aria-label") or ""
                if any(kw in (name + ph + aria).lower() for kw in
                       ("doc", "type", "category", "instrument")):
                    out["doc_type_options"].append(
                        {"input_name": name, "placeholder": ph, "aria": aria})
            except Exception:
                continue

        # Button labels around the form
        for btn in page.locator("button").all()[:60]:
            try:
                t = (btn.inner_text() or "").strip()[:60]
                if t:
                    out["buttons"].append(t)
            except Exception:
                continue

        b.close()
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", default="leon")
    args = parser.parse_args()
    json.dump(probe(args.slug), sys.stdout, indent=2)
    sys.stdout.write("\n")
