#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import csv
import datetime as dt
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

URL = "https://www.stadt-zuerich.ch/de/stadtleben/sport-und-erholung/sport-und-badeanlagen/hallenbaeder/city.html"
CSV_PATH = Path("swiftbar_city_auslastung.csv")

# Active window (Zurich time)
ACTIVE_START_HOUR = 7
ACTIVE_END_HOUR   = 22

# Timeouts (a bit larger in CI)
TIMEOUT_MS       = 20000   # page goto / waits
RENDER_BUFFER_MS = 2500    # give the site some JS time

# Debug: set env SCRAPE_DEBUG=1 to dump screenshot/HTML on failure
DEBUG = os.getenv("SCRAPE_DEBUG") == "1"
ARTIFACT_DIR = Path("artifacts")
ARTIFACT_DIR.mkdir(exist_ok=True)

SELECTOR_CANDIDATES = [
    "#SSD-4_visitornumber",
    "td[id*='visitornumber']",
    "[id*='visitornumber']",
    "[id*='visitor']",
    "[class*='visitor']",
]

NUMBER_REGEXES = [
    r"\b(\d{1,4})\s*%?\b",
    r"Aktuelle\s+Auslastung.*?(\d{1,4})",
]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

COOKIE_BUTTON_TEXTS = [
    "Alle akzeptieren",
    "Akzeptieren",
    "OK",
    "Einverstanden",
    "Zustimmen",
]

def in_active_window(now_zh: dt.datetime) -> bool:
    s = now_zh.replace(hour=ACTIVE_START_HOUR, minute=0, second=0, microsecond=0)
    e = now_zh.replace(hour=ACTIVE_END_HOUR, minute=0, second=0, microsecond=0)
    return s <= now_zh <= e

def extract_first_number(text: str):
    for rx in NUMBER_REGEXES:
        m = re.search(rx, text, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1).strip()
    # As last resort, pick a standalone 1–3 digit number if present
    m = re.search(r"\b(\d{1,3})\b", text)
    return m.group(1) if m else None

def append_csv(path: Path, timestamp: dt.datetime, value: str):
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp", "value"])
        w.writerow([timestamp.isoformat(timespec="seconds"), value])

def try_click_cookies(page):
    for label in COOKIE_BUTTON_TEXTS:
        btn = page.get_by_role("button", name=label, exact=False)
        try:
            if btn and btn.count() > 0:
                btn.first.click(timeout=1500)
                page.wait_for_timeout(300)
                return
        except Exception:
            pass
    # also try generic selectors often used by cookie banners
    for sel in ["button[aria-label*='akzept']", "button[aria-label*='accept']",
                "[id*='consent'] button", "div[role='dialog'] button"]:
        try:
            el = page.query_selector(sel)
            if el:
                el.click(timeout=1500)
                page.wait_for_timeout(300)
                return
        except Exception:
            pass

def save_debug(page, suffix=""):
    if not DEBUG:
        return
    try:
        page.screenshot(path=str(ARTIFACT_DIR / f"page{suffix}.png"), full_page=True)
    except Exception:
        pass
    try:
        html = page.content()
        (ARTIFACT_DIR / f"page{suffix}.html").write_text(html, encoding="utf-8")
    except Exception:
        pass

def main():
    now_zh = dt.datetime.now(ZoneInfo("Europe/Zurich"))
    if not in_active_window(now_zh):
        print("outside active window; skip")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page(
                user_agent=UA,
                locale="de-CH",
                timezone_id="Europe/Zurich",
            )
            page.goto(URL, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
            try_click_cookies(page)

            # wait for either a known selector OR the key text to appear
            try:
                page.wait_for_timeout(RENDER_BUFFER_MS)
                any_selector = None
                for sel in SELECTOR_CANDIDATES:
                    try:
                        page.wait_for_selector(sel, timeout=3000, state="visible")
                        any_selector = sel
                        break
                    except Exception:
                        continue
                if not any_selector:
                    # fall back to content-based wait (appears somewhere on page)
                    page.wait_for_function(
                        """() => /Aktuelle\\s+Auslastung/i.test(document.body.innerText)""",
                        timeout=5000
                    )
            except Exception:
                # keep going; we’ll still try body-based extraction
                pass

            # Strategy 1: direct selectors
            for sel in SELECTOR_CANDIDATES:
                try:
                    el = page.query_selector(sel)
                    if not el:
                        continue
                    t = (el.inner_text() or "").strip()
                    num = extract_first_number(t)
                    if num:
                        append_csv(CSV_PATH, now_zh, num)
                        print(f"ok: {num} via {sel}")
                        return
                except Exception:
                    continue

            # Strategy 2: nearest section around “Aktuelle Auslastung”
            try:
                loc = page.get_by_text("Aktuelle Auslastung", exact=False)
                if loc.count() > 0:
                    handle = loc.nth(0).element_handle()
                    if handle:
                        section_text = handle.evaluate(
                            """(node) => {
                                const root = node.closest('section,div,article') || document;
                                return root.innerText || '';
                            }"""
                        ) or ""
                        num = extract_first_number(section_text)
                        if num:
                            append_csv(CSV_PATH, now_zh, num)
                            print(f"ok: {num} via section")
                            return
            except Exception:
                pass

            # Strategy 3: whole document
            body = page.inner_text("body")
            num = extract_first_number(body or "")
            if num:
                append_csv(CSV_PATH, now_zh, num)
                print(f"ok: {num} via body")
                return

            # If we reach here, dump debug and exit gracefully
            save_debug(page, suffix="-fail")
            print("no number found; skip without error")
            return

        finally:
            browser.close()

if __name__ == "__main__":
    main()
