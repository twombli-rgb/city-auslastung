#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import csv
import datetime as dt
import re
from pathlib import Path
from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

URL = "https://www.stadt-zuerich.ch/de/stadtleben/sport-und-erholung/sport-und-badeanlagen/hallenbaeder/city.html"
CSV_PATH = Path("swiftbar_city_auslastung.csv")  # stored in repo

# time window (local Zurich time)
ACTIVE_START_HOUR = 7
ACTIVE_END_HOUR = 22

TIMEOUT_MS = 8000
RENDER_BUFFER_MS = 1500
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

def in_active_window(now_zh: dt.datetime) -> bool:
    s = now_zh.replace(hour=ACTIVE_START_HOUR, minute=0, second=0, microsecond=0)
    e = now_zh.replace(hour=ACTIVE_END_HOUR, minute=0, second=0, microsecond=0)
    return s <= now_zh <= e

def extract_first_number(text: str):
    for rx in NUMBER_REGEXES:
        m = re.search(rx, text, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1).strip()
    return None

def append_csv(path: Path, timestamp: dt.datetime, value: str):
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp", "value"])
        w.writerow([timestamp.isoformat(timespec="seconds"), value])

def main():
    now_zh = dt.datetime.now(ZoneInfo("Europe/Zurich"))
    if not in_active_window(now_zh):
        # do nothing outside 07:00–22:00 local time
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=UA)
            page.goto(URL, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
            page.wait_for_timeout(RENDER_BUFFER_MS)

            # Try specific selectors first
            for sel in SELECTOR_CANDIDATES:
                el = page.query_selector(sel)
                if not el:
                    continue
                t = (el.inner_text() or "").strip()
                num = extract_first_number(t)
                if num:
                    append_csv(CSV_PATH, now_zh, num)
                    return

            # Fallback: search entire document
            body = page.inner_text("body")
            num = extract_first_number(body or "")
            if not num:
                raise RuntimeError("no number found")
            append_csv(CSV_PATH, now_zh, num)

        finally:
            browser.close()

if __name__ == "__main__":
    main()
