"""
Watcher — scrapes target Facebook pages for new Reels and queues them in Supabase.
Runs on GitHub Actions every 2 hours.
"""
import os
import time
import random
import json
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright
from db import upsert_reel

TARGET_PAGES: list[str] = json.loads(os.environ["TARGET_PAGES"])


def human_delay(lo=2.0, hi=6.0):
    time.sleep(random.uniform(lo, hi))


def parse_count(text: str) -> int:
    """
    Convert Facebook's display counts to ints.
    e.g. '1.2K' -> 1200, '4.5M' -> 4500000
    """
    if not text:
        return 0
    text = text.strip().upper().replace(",", "")
    try:
        if "K" in text:
            return int(float(text.replace("K", "")) * 1_000)
        if "M" in text:
            return int(float(text.replace("M", "")) * 1_000_000)
        return int(text)
    except ValueError:
        return 0


def scrape_page(page_url: str) -> list[dict]:
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="America/New_York",
            # Remove navigator.webdriver flag
            java_script_enabled=True,
        )

        # Spoof navigator.webdriver = false
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """)

        page = ctx.new_page()

        try:
            print(f"[Watcher] Loading {page_url}")
            page.goto(page_url, wait_until="domcontentloaded", timeout=30_000)
            human_delay(3, 6)

            # Scroll down to trigger lazy-loaded reels
            for _ in range(3):
                page.mouse.wheel(0, random.randint(300, 700))
                human_delay(1.5, 3)

            # Grab all reel links visible on the page
            reel_anchors = page.query_selector_all('a[href*="/reel/"]')
            seen_urls = set()

            for anchor in reel_anchors[:15]:
                href = anchor.get_attribute("href") or ""
                if "/reel/" not in href:
                    continue
                full_url = (
                    "https://www.facebook.com" + href
                    if href.startswith("/")
                    else href.split("?")[0]  # strip query params
                )
                if full_url in seen_urls:
                    continue
                seen_urls.add(full_url)

                # Try to grab visible engagement numbers near this element
                # FB DOM changes frequently — these selectors are best-effort
                views = 0
                likes = 0
                comments = 0
                try:
                    parent = anchor.evaluate_handle(
                        "el => el.closest('[data-visualcompletion]') || el.parentElement"
                    )
                    text_content = parent.as_element().inner_text() if parent else ""
                    lines = [l.strip() for l in text_content.splitlines() if l.strip()]
                    # Heuristic: first number-like token is often views
                    for line in lines:
                        v = parse_count(line)
                        if v > 1000 and views == 0:
                            views = v
                except Exception:
                    pass  # metrics scraping is fragile — zero is fine, ranking still works

                results.append({
                    "reel_url": full_url,
                    "source_page": page_url,
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                })

        except Exception as e:
            print(f"[Watcher] Error on {page_url}: {e}")
        finally:
            browser.close()

    return results


def main():
    total = 0
    for page_url in TARGET_PAGES:
        reels = scrape_page(page_url)
        for r in reels:
            upsert_reel(**r)
        total += len(reels)
        print(f"[Watcher] {page_url} → {len(reels)} reels queued")
        # Don't hammer pages back to back
        if page_url != TARGET_PAGES[-1]:
            human_delay(15, 30)

    print(f"[Watcher] Done. Total reels upserted: {total}")


if __name__ == "__main__":
    main()
