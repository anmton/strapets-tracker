"""
Starpets.gg Targeted Hunt Scraper
=================================
Exclusively searches for pets defined in config.json using the site's search bar
to avoid parameter-based bot detection. Sends alerts via ntfy.sh.
"""

import csv
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
import requests

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# Force UTF-8 output on Windows consoles
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Paths ───────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
CSV_PATH = SCRIPT_DIR / "price_history.csv"
CONFIG_PATH = SCRIPT_DIR / "config.json"
SCREENSHOT_DIR = SCRIPT_DIR / "screenshots"

TARGET_URL = "https://starpets.gg"

# ntfy configuration
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")

def send_alert(message):
    """Send a notification to ntfy.sh if a topic is configured."""
    if NTFY_TOPIC:
        try:
            url = f"https://ntfy.sh/{NTFY_TOPIC}"
            headers = {
                "Title": "Starpets Price Alert",
                "Priority": "high",
                "Tags": "money_with_wings,star"
            }
            requests.post(url, data=message.encode('utf-8'), headers=headers)
        except Exception as e:
            print(f"  [WARN] Could not send ntfy notification: {e}")


# ── Helpers ─────────────────────────────────────────────────────────────────
def parse_price(raw: str) -> float | None:
    """Extract a numeric price from strings like '0.08 $', '$1.16', or '0,29 €'."""
    normalized = raw.replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)", normalized)
    if match:
        return float(match.group(1))
    return None


def load_alerts() -> list[dict]:
    """Load alert targets from config.json."""
    if not CONFIG_PATH.exists():
        print(f"[!] Warning: {CONFIG_PATH} not found.")
        return []
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("alerts", [])
    except Exception as e:
        print(f"[!] Error loading config.json: {e}")
        return []


def append_to_csv(items: list[dict]) -> None:
    """Append scraped items to price_history.csv."""
    if not items:
        return
    file_exists = CSV_PATH.exists() and CSV_PATH.stat().st_size > 0
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "pet_name", "price_eur"])
        for item in items:
            writer.writerow([item["timestamp"], item["pet_name"], item["price_eur"]])


# ── Scraping ────────────────────────────────────────────────────────────────
def hunt() -> list[dict]:
    """Search for each pet in config.json and return found items."""
    all_found_items: list[dict] = []
    alerts = load_alerts()
    
    if not alerts:
        print("[!] No alerts configured. Nothing to hunt.")
        return []

    print(f"[*] Starting Hunt Mode for {len(alerts)} items...")
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        # 1. Nav to Home
        print(f"[*] Navigating to {TARGET_URL} ...")
        try:
            page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000) # Wait for hydration
        except PlaywrightTimeout:
            print("[WARN] Home page load timed out -- attempting to proceed anyway.")

        # 2. Ensure Currency is Euro (€)
        try:
            print("[*] Checking currency setting...")
            # Look for the currency button (shows $ or €)
            currency_btn = page.locator("header").locator("button, div").filter(has_text=re.compile(r"[\$€]")).first
            if currency_btn.is_visible():
                current_text = currency_btn.inner_text()
                if "$" in current_text or "USD" in current_text:
                    print("[*] Switching currency to EUR...")
                    currency_btn.click()
                    page.wait_for_selector("text='EUR'", timeout=5000).click()
                    page.wait_for_timeout(2000)
                else:
                    print("[*] Currency already seems to be EUR.")
        except Exception as e:
            print(f"[WARN] Could not verify/switch currency: {e}")

        for alert in alerts:
            pet_name = alert.get("pet_name", "").strip()
            target_price = alert.get("target_price")
            
            if not pet_name:
                continue
                
            print(f"\n[>] Hunting for: {pet_name} (Target <= {target_price}€)")

            try:
                # 3. Human-like Search
                # The search box might be a div that needs a click first
                search_area = page.locator("text='Quick search'").first
                if search_area.is_visible():
                    search_area.click()
                
                search_box = page.get_by_placeholder("Quick search")
                search_box.focus()
                search_box.fill("") # Clear
                search_box.type(pet_name, delay=100) # Human-like typing
                page.keyboard.press("Enter")
                
                # 4. Wait for results
                page.wait_for_timeout(4000) # Give it time to filter
                
                # ── Take screenshot ─────────────────────────────────────────────
                SCREENSHOT_DIR.mkdir(exist_ok=True)
                sanitized_name = re.sub(r'[^\w\-_\. ]', '_', pet_name)
                screenshot_path = SCREENSHOT_DIR / f"hunt_{sanitized_name}.png"
                page.screenshot(path=str(screenshot_path))

                # 4. Extract data
                raw_items = page.evaluate("""
                    () => {
                        const cards = document.querySelectorAll('a[href*="/adopt-me/shop/"]');
                        const results = [];
                        // Get up to 10 results for each search
                        for (let i = 0; i < Math.min(cards.length, 10); i++) {
                            const text = cards[i].innerText.trim();
                            if (text) results.push(text);
                        }
                        return results;
                    }
                """)

                hunt_count = 0
                for raw in raw_items:
                    lines = [l.strip() for l in raw.split("\n") if l.strip()]
                    if len(lines) < 2: continue

                    price_line = None
                    name_parts = []
                    currency_symbols = ["$", "€", "EUR", "USD"]
                    
                    for line in lines:
                        has_currency = any(sym in line for sym in currency_symbols)
                        if has_currency or (re.search(r"\d", line) and line == lines[-1]):
                            price_line = line
                        else:
                            name_parts.append(line)

                    if price_line is None: continue
                    price = parse_price(price_line)
                    if price is None: continue

                    # The name might be split over multiple lines
                    found_pet_name = " ".join(name_parts)
                    
                    found_item = {
                        "timestamp": timestamp,
                        "pet_name": found_pet_name,
                        "price_eur": price
                    }
                    all_found_items.append(found_item)
                    hunt_count += 1

                    # 5. Check Alert & Notify
                    if price <= target_price:
                        msg = f"HUNT SUCCESS: {found_pet_name} found for {price:.2f}€ (Target <= {target_price:.2f}€)"
                        print(f"  [!!!] {msg}")
                        send_alert(msg)
                
                if hunt_count == 0:
                    print(f"  [?] No results found for '{pet_name}'")
                else:
                    print(f"  [*] Found {hunt_count} listings.")

            except Exception as e:
                print(f"  [ERR] Failed hunting '{pet_name}': {e}")

        browser.close()

    return all_found_items


# ── Main ────────────────────────────────────────────────────────────────────
def main() -> None:
    print("=" * 60)
    print("  [*] Starpets.gg Targeted Hunt Scraper")
    print("=" * 60)

    items = hunt()

    if items:
        # Save to CSV
        append_to_csv(items)
        print(f"\n[SAVE] Logged {len(items)} listings to {CSV_PATH}")
    else:
        print("\n[INFO] Hunt complete. No data to log.")

    print("[DONE] Finished!")


if __name__ == "__main__":
    main()
