#!/usr/bin/env python3
"""
BMVS Appointment Availability Checker
======================================
Checks the Bupa Medical Visa Services website for available appointment
slots near Darwin, Northern Territory, Australia.

Sends email alerts via Gmail when slots are found.

Discovered site flow (Aug 2026):
  1. Default.aspx → Click "New Individual booking" (#btnInd)
  2. Choose location → Type city, select state, click Search
  3. Results table shows clinics with "First Available Date" column
  4. Select a clinic → Choose tests → Calendar page
  5. Calendar shows "no available appointments" or clickable dates
"""

import os
import sys
import json
import time
import logging
import smtplib
import hashlib
import zoneinfo
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Searches to perform: (city_query, state_value, display_name)
SEARCHES = [
    ("Darwin", "NT", "Darwin, NT"),
    ("Alice Springs", "NT", "Alice Springs, NT"),
    ("0820", "NT", "Darwin postcode 0820"),
    ("0870", "NT", "Alice Springs postcode 0870"),
]

# Fallback: check nearest other cities if NT has nothing
FALLBACK_SEARCHES = [
    ("Cairns", "QLD", "Cairns, QLD"),
    ("Townsville", "QLD", "Townsville, QLD"),
    ("Adelaide", "SA", "Adelaide, SA"),
    ("Brisbane", "QLD", "Brisbane, QLD"),
    ("Sydney", "NSW", "Sydney, NSW"),
    ("Melbourne", "VIC", "Melbourne, VIC"),
]

# Email config (from environment variables)
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
ALERT_RECIPIENT = os.environ.get("ALERT_RECIPIENT", GMAIL_ADDRESS)

# State file to avoid duplicate alerts
STATE_DIR = Path(__file__).parent / "state"
STATE_FILE = STATE_DIR / "last_check.json"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bmvs-checker")

BASE_URL = "https://bmvs.onlineappointmentscheduling.net.au/oasis/Default.aspx"


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def send_email(subject: str, body: str, html_body: str = "") -> bool:
    """Send an email alert via Gmail SMTP."""
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        log.warning("GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set — skipping email.")
        return False

    msg = MIMEMultipart("alternative")
    msg["From"] = f"BMVS Checker <{GMAIL_ADDRESS}>"
    msg["To"] = ALERT_RECIPIENT
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "plain"))
    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, ALERT_RECIPIENT, msg.as_string())
        log.info(f"Email sent to {ALERT_RECIPIENT}")
        return True
    except Exception as e:
        log.error(f"Failed to send email: {e}")
        return False


# ---------------------------------------------------------------------------
# State management (dedup)
# ---------------------------------------------------------------------------

def load_state() -> dict:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"alerted_hashes": [], "last_check": None}


def save_state(state: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2))


def slot_hash(clinic: str, date_str: str) -> str:
    raw = f"{clinic}|{date_str}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Main checker using Playwright
# ---------------------------------------------------------------------------

def check_appointments() -> list[dict]:
    """
    Navigate the BMVS booking site and return available appointment slots.

    Discovered flow:
      Page 1: Welcome → Click "New Individual booking"
      Page 2: Location search → Type city, select state, click Search
      Page 3: Results table → Shows clinics with "First Available Date"
               - "No available slot" = nothing
               - A date (e.g. "25 Aug 2026") = SLOT AVAILABLE!
      (Optional) Page 4: Tests → Select checkboxes
      (Optional) Page 5: Calendar → Shows available dates
    """
    from playwright.sync_api import sync_playwright

    found_slots = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        try:
            # ===================== PAGE 1: Welcome =====================
            log.info("Loading BMVS appointment page...")
            page.goto(BASE_URL, timeout=30000, wait_until="networkidle")
            time.sleep(2)

            log.info("Clicking 'New Individual booking'...")
            page.click("#ContentPlaceHolder1_btnInd")
            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(2)

            # ===================== PAGES 2-3: Location search =========
            all_searches = SEARCHES + FALLBACK_SEARCHES

            for city_query, state_value, display_name in all_searches:
                log.info(f"Searching: {display_name}")

                # Fill the suburb/city field
                suburb_input = page.query_selector(
                    "#ContentPlaceHolder1_SelectLocation1_txtSuburb"
                )
                if not suburb_input:
                    log.error("Could not find suburb input field!")
                    break

                suburb_input.fill("")
                time.sleep(0.3)
                suburb_input.fill(city_query)

                # Select state
                page.select_option(
                    "#ContentPlaceHolder1_SelectLocation1_ddlState", state_value
                )
                time.sleep(0.5)

                # Click Search
                search_btn = page.query_selector('input[value="Search"]')
                if search_btn and search_btn.is_visible():
                    search_btn.click()
                else:
                    # ASP.NET postback — try clicking the hidden search button
                    hidden_search = page.query_selector(
                        "#ContentPlaceHolder1_SelectLocation1_btnSearch"
                    )
                    if hidden_search:
                        hidden_search.click()

                page.wait_for_load_state("networkidle", timeout=15000)
                time.sleep(2)

                # ================== Parse results table ================
                # Look for "No available slot" or actual dates
                page_text = page.inner_text("body")

                if "No available slot" in page_text:
                    log.info(f"  {display_name}: All clinics show 'No available slot'")

                    # Check if there are ANY clinics listed at all
                    if "assessment centre" not in page_text.lower() and "clinic" not in page_text.lower():
                        log.info(f"  {display_name}: No clinics found for this search")
                    continue

                # Check if we got any results at all
                if "Town, suburb or postcode required" in page_text:
                    log.info(f"  {display_name}: Validation error — skipping")
                    continue

                # Parse the table rows for clinics
                clinics = parse_clinic_table(page, display_name)
                if clinics:
                    found_slots.extend(clinics)
                    log.info(f"  🎉 {display_name}: Found {len(clinics)} clinic(s) with available slots!")
                    # Don't break — collect all available slots
                else:
                    log.info(f"  {display_name}: No slots found in table")

                # If we found NT slots, no need to check fallbacks
                if found_slots and state_value == "NT":
                    log.info("Found NT slots — skipping fallback searches")
                    break

                # Small delay between searches
                time.sleep(1)

            # ===================== Save debug screenshot ==============
            try:
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(STATE_DIR / "last_screenshot.png"))
            except Exception:
                pass

        except Exception as e:
            log.error(f"Error during check: {e}", exc_info=True)
            try:
                STATE_DIR.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(STATE_DIR / "error_screenshot.png"))
            except Exception:
                pass
        finally:
            browser.close()

    return found_slots


def parse_clinic_table(page, search_display: str) -> list[dict]:
    """
    Parse the location search results page for clinics with available slots.

    The page shows a table like:
      Centre | First Available Date | ...
      Jobfit Darwin | No available slot | ...
    Or:
      Jobfit Darwin | 25 Aug 2026 | ...
    """
    slots = []

    # Get the full page HTML to parse more reliably
    page_html = page.content()
    page_text = page.inner_text("body")

    # Method 1: Look for table rows in the results
    # The results table has rows with clinic names and dates
    rows = page.query_selector_all("table tr, tr")
    for row in rows:
        try:
            cells = row.query_selector_all("td")
            if len(cells) < 2:
                continue

            row_text = row.inner_text().strip()

            # Skip header rows
            if "Centre" in row_text and "Available" in row_text:
                continue
            if "Checkbox" in row_text:
                continue

            # Look for a row that has a date pattern
            # Dates on BMVS look like: "25 Aug 2026", "Mon 25 Aug", etc.
            has_date = False
            date_str = ""
            clinic_name = ""

            for cell in cells:
                cell_text = cell.inner_text().strip()

                # Check for date patterns
                month_keywords = [
                    "jan", "feb", "mar", "apr", "may", "jun",
                    "jul", "aug", "sep", "oct", "nov", "dec",
                ]
                if any(m in cell_text.lower() for m in month_keywords):
                    # Could be a date
                    if any(y in cell_text for y in ["2025", "2026", "2027"]):
                        has_date = True
                        date_str = cell_text
                    elif any(day in cell_text.lower() for day in [
                        "monday", "tuesday", "wednesday", "thursday",
                        "friday", "saturday", "sunday", "mon", "tue",
                        "wed", "thu", "fri", "sat", "sun",
                    ]):
                        has_date = True
                        date_str = cell_text

                # Check for clinic name (first substantial text cell)
                if not clinic_name and len(cell_text) > 3 and "km" not in cell_text.lower():
                    clinic_name = cell_text

            if has_date and date_str and "no available" not in date_str.lower():
                slots.append({
                    "clinic": clinic_name or "Unknown clinic",
                    "date": date_str,
                    "location": search_display,
                    "raw": row_text,
                })

        except Exception:
            continue

    # Method 2: If no table found, scan the entire page for the "First Available" pattern
    if not slots:
        # Look for the specific text pattern used by BMVS
        import re
        # Pattern: clinic name followed by date
        date_pattern = re.compile(
            r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})',
            re.IGNORECASE
        )
        matches = date_pattern.findall(page_text)
        for m in matches:
            slots.append({
                "clinic": "See booking page",
                "date": m,
                "location": search_display,
                "raw": m,
            })

    return slots


# ---------------------------------------------------------------------------
# Alert
# ---------------------------------------------------------------------------

def process_and_alert(slots: list[dict]):
    """Dedup slots and send email alert if new ones are found."""
    state = load_state()
    already_alerted = set(state.get("alerted_hashes", []))

    new_slots = []
    for s in slots:
        h = slot_hash(s.get("clinic", ""), s.get("date", ""))
        if h not in already_alerted:
            new_slots.append(s)
            already_alerted.add(h)

    state["alerted_hashes"] = list(already_alerted)[-500:]
    save_state(state)

    if not new_slots:
        log.info("No new appointment slots found.")
        return

    log.info(f"🎉 Found {len(new_slots)} NEW appointment slot(s)!")

    subject = f"🚨 BMVS Appointment Available — {len(new_slots)} slot(s) near Darwin!"

    plain_lines = [
        subject,
        "",
        f"Checked at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]
    html_lines = [
        "<h2>🚨 Appointment Slots Available!</h2>",
        f"<p>Found <strong>{len(new_slots)}</strong> new slot(s) near Darwin, NT</p>",
        f"<p>Checked at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>",
        "<table border='1' cellpadding='8' cellspacing='0' "
        "style='border-collapse:collapse;'>",
        "<tr style='background:#f0f0f0;'><th>Clinic</th><th>Date</th>"
        "<th>Location</th></tr>",
    ]

    for s in new_slots:
        plain_lines.append(
            f"  📍 {s.get('clinic', 'N/A')} | 📅 {s['date']} | "
            f"🗺️  {s.get('location', 'N/A')}"
        )
        html_lines.append(
            f"<tr><td>{s.get('clinic', 'N/A')}</td><td>{s['date']}</td>"
            f"<td>{s.get('location', 'N/A')}</td></tr>"
        )

    plain_lines.extend([
        "",
        "👉 Book now: " + BASE_URL,
        "",
        "(Automated alert from BMVS Appointment Checker)",
    ])

    html_lines.extend([
        "</table>",
        "<br>",
        f"<a href='{BASE_URL}' style='display:inline-block;padding:12px 24px;"
        "background:#007bff;color:#fff;text-decoration:none;border-radius:4px;'>"
        "Book Now →</a>",
        "<br><br>",
        "<p style='color:#888;font-size:12px;'>"
        "Automated alert from BMVS Appointment Checker</p>",
    ])

    send_email(subject, "\n".join(plain_lines), "\n".join(html_lines))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def is_in_check_window() -> bool:
    """Check if current time is within 6:00–8:00 AM ACST (Darwin time)."""
    try:
        acst = zoneinfo.ZoneInfo("Australia/Darwin")
    except Exception:
        # Fallback: ACST is UTC+9:30
        from datetime import timedelta
        acst = timezone(timedelta(hours=9, minutes=30))

    now = datetime.now(acst)
    hour = now.hour
    minute = now.minute

    # Window: 6:00 AM to 7:59 AM (inclusive)
    in_window = 6 <= hour < 8
    log.info(
        f"Darwin time: {now.strftime('%H:%M:%S ACST')} — "
        f"{'✅ In check window (6-8 AM)' if in_window else '⏸️ Outside window, skipping'}"
    )
    return in_window


def main():
    log.info("=" * 60)
    log.info("BMVS Appointment Checker — Starting")
    log.info(f"Time (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

    # Only check during 6-8 AM Darwin time
    if not is_in_check_window():
        log.info("Outside check window. Exiting.")
        return

    log.info(f"Primary searches: {[s[2] for s in SEARCHES]}")
    log.info(f"Fallback searches: {[s[2] for s in FALLBACK_SEARCHES]}")
    log.info("=" * 60)

    try:
        slots = check_appointments()
        process_and_alert(slots)

        if not slots:
            log.info("Result: No available appointments found.")
        else:
            log.info(f"Result: {len(slots)} slot(s) available!")
            for s in slots:
                log.info(
                    f"  → {s.get('clinic', 'N/A')} | {s['date']} | "
                    f"{s.get('location', 'N/A')}"
                )

    except Exception as e:
        log.error(f"Fatal error: {e}", exc_info=True)
        try:
            send_email(
                "⚠️ BMVS Checker Error",
                f"Error:\n\n{e}\n\nCheck GitHub Actions logs for details.",
            )
        except Exception:
            pass
        sys.exit(1)

    log.info("Done.")


if __name__ == "__main__":
    main()
