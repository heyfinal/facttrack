"""Test Multi-Page-View + Go-To-Next-Page strategies on Hanks 1958 lease."""
from __future__ import annotations
import sys
from playwright.sync_api import sync_playwright

sys.path.insert(0, "/home/daniel/facttrack/src")
from facttrack.ingest.publicsearch_docs import _search_and_open_doc


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
            viewport={"width": 1600, "height": 1200},
            accept_downloads=True,
        )
        page = context.new_page()
        captured: list[str] = []

        def on_resp(resp):
            u = resp.url
            if "/files/documents/" in u and "images" in u and (".png" in u or ".jpg" in u):
                if u not in captured:
                    captured.append(u)
        page.on("response", on_resp)

        opened = _search_and_open_doc(page, "1958-58563225", "https://anderson.tx.publicsearch.us")
        print(f"opened={opened}; url={page.url}")
        page.wait_for_timeout(3000)
        print(f"initial captured: {len(captured)}")

        # Try Multi Page View
        mpv = page.locator("button[aria-label='Multi Page View']").first
        if mpv.count() > 0:
            try:
                mpv.click(force=True, timeout=3000)
                page.wait_for_timeout(4000)
                print(f"after MPV click: {len(captured)} urls")
            except Exception as e:
                print(f"MPV click failed: {e}")
        else:
            print("MPV button not found")

        # Try repeated Next-Page clicks
        for i in range(15):
            np = page.locator("button[aria-label='Go To Next Page']").first
            if np.count() == 0:
                print(f"no more next-button at iter {i}")
                break
            try:
                np.click(force=True, timeout=2_000)
                page.wait_for_timeout(1500)
                print(f"next-click {i+1}: {len(captured)} captured")
            except Exception as e:
                print(f"next-click {i+1} failed: {e}")
                break

        print("\nFINAL URLs:")
        for u in captured:
            # show just the doc/image path
            print(" ", u.split("?")[0])
        browser.close()


if __name__ == "__main__":
    main()
