#!/usr/bin/env python3
"""
rowcheck.py — Stockholm rowing safety checker
Checks air temp, water temp (two sources), sunrise and sunset.

Primary water source:   SMHI station 35185 (E4 bron sjöv, Sjöfartsverket)
                        ocobs API, parameter 5 (Havstemperatur). Real-time,
                        timestamped, updated hourly.
Secondary source:       havochvatten.se — Ekhagens strandbad.
                        Reports "Dagens vattentemperatur" — a Copernicus model
                        forecast, updated once daily. Used as context/fallback.
                        NOTE: Norr Mälarstrand was removed — that page has no
                        water temperature data, only an air temp forecast.

All available water readings are averaged; the average feeds into the
combined air + water safety calculation.

Install dependencies:
    pip install requests beautifulsoup4 selenium astral

Selenium requires Chrome (or Chromium) on your machine.
Modern Selenium (4.6+) auto-downloads the matching ChromeDriver.
"""

import re
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from astral import LocationInfo
from astral.sun import sun

# ── Config ───────────────────────────────────────────────────────

TZ = ZoneInfo("Europe/Stockholm")
LAT, LON = 59.3293, 18.0686

# Primary: SMHI station 35185 (E4 bron sjöv)
# ocobs API, parameter 5 = Havstemperatur (confirmed via smhi_probe.py)
SMHI_URL = (
    "https://opendata-download-ocobs.smhi.se/api/version/latest"
    "/parameter/5/station/35185/period/latest-day/data.json"
)
SMHI_TEMP_MIN, SMHI_TEMP_MAX = -2.0, 35.0  # sanity bounds (°C) for Stockholm water

# Secondary: Ekhagens strandbad (havochvatten.se / Copernicus forecast)
# "Dagens vattentemperatur" is a model forecast, updated once daily.
# Norr Mälarstrand removed — page has no water temp, only air temp forecast.
HAVOCHVATTEN_STATIONS = [
    (
        "Ekhagens strandbad",
        "https://www.havochvatten.se/badplatser-och-badvatten/kommuner/"
        "badplatser-i-stockholms-kommun/ekhagens-strandbad.html",
    ),
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# ── Helpers ──────────────────────────────────────────────────────

def c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def _fmt_dt(dt: datetime, label: str = "measured") -> str:
    """Format timestamp — include date if not today."""
    now = datetime.now(TZ)
    if dt.date() == now.date():
        return f"{label} {dt:%H:%M} CET"
    return f"{label} {dt:%-d %b %H:%M} CET"


# ── Air temperature (Open-Meteo API — free, no key needed) ───────

def get_air_temp() -> tuple[float, datetime]:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&current=temperature_2m"
        "&timezone=Europe%2FStockholm"
    )
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return float(r.json()["current"]["temperature_2m"]), datetime.now(TZ)


# ── Water temperatures ────────────────────────────────────────────

TEMP_PAT = re.compile(r"(\d+(?:[.,]\d+)?)\s*°\s*C", re.I)
CLEAN_RE = re.compile(r"[\xad\u200b\u00a0]")  # soft-hyphen, ZWSP, NBSP

# Both words must appear together to match "Dagens vattentemperatur"
# (Swedish: "Today's water temperature"). Requiring both avoids accidentally
# picking up air temperatures from the weather forecast table on the same page.
DAGENS_KEYWORDS = ["dagens", "vattentemperatur"]


def _clean(text: str) -> str:
    return CLEAN_RE.sub(" ", text).strip()


def _extract_temp_from_soup(soup: BeautifulSoup) -> float | None:
    """
    Multi-strategy search for today's water temperature on a havochvatten.se page.

    Strategy 1 (run first) — plain-text scan for the exact phrase
    'dagens vattentemperatur', then grab the next °C value within 300 characters.
    This is the most reliable strategy: it anchors to the phrase position so only
    the water temperature value that follows it can match — not any air temperature
    values that appear earlier on the same page.
    _clean() strips soft hyphens and NBSP which the site embeds in words like
    "vatten­temperatur", which would otherwise prevent a plain str.find() match.

    Strategy 2 (fallback) — tag-walking: find a tag containing both 'dagens' AND
    'vattentemperatur', then check its next siblings for a °C value. Handles cases
    where the phrase and value are split across separate elements (e.g. <dt>/<dd>).

    Strategy 3 (last resort) — broad search for bare 'vattentemperatur'. Result is
    NOT returned — a warning is printed and None returned, excluding the reading
    from the average, because this search often picks up air temperatures.
    """
    # Strategy 1 — plain-text scan anchored to "dagens vattentemperatur"
    full  = _clean(soup.get_text(" "))
    lower = full.lower()
    pos = lower.find("dagens vattentemperatur")
    if pos >= 0:
        snippet = full[pos: pos + 300]
        m = TEMP_PAT.search(snippet)
        if m:
            return float(m.group(1).replace(",", "."))

    # Strategy 2 — tag-walking fallback for split label/value structures
    target_tags = ["th", "td", "dt", "p", "li", "span", "h2", "h3", "h4"]
    for tag in soup.find_all(target_tags):
        txt = _clean(tag.get_text(" ", strip=True))
        if not all(k in txt.lower() for k in DAGENS_KEYWORDS):
            continue
        # Only search short tags (< 120 chars) — avoids large container elements
        # whose text spans the whole section and includes air temperatures.
        if len(txt) <= 120:
            m = TEMP_PAT.search(txt)
            if m:
                return float(m.group(1).replace(",", "."))
        # Check next sibling tags (up to 6) for a value in a separate element
        for sibling in list(tag.next_siblings)[:6]:
            if hasattr(sibling, "get_text"):
                m = TEMP_PAT.search(_clean(sibling.get_text()))
                if m:
                    return float(m.group(1).replace(",", "."))

    # Strategy 3 — broad fallback, excluded from average
    pos = lower.find("vattentemperatur")
    if pos >= 0:
        snippet = full[pos: pos + 300]
        m = TEMP_PAT.search(snippet)
        if m:
            val = float(m.group(1).replace(",", "."))
            print(
                f"  [havochvatten] Could not confirm 'Dagens vattentemperatur' "
                f"(found {val} °C via broad search — excluded from average).",
                file=sys.stderr,
            )
            return None   # ← excluded from average

    return None


def get_water_smhi() -> tuple[float, datetime] | None:
    """
    Primary: SMHI station 35185 (E4 bron sjöv), ocobs parameter 5 (Havstemperatur).
    Sanity-checks the value against plausible Stockholm water temperature range.
    Returns (°C, measured_at) or None if unavailable or implausible.
    """
    try:
        r = requests.get(SMHI_URL, timeout=10)
        r.raise_for_status()
        data = r.json()
        values = data.get("value", [])
        if not values:
            return None
        latest = values[-1]
        temp_c = float(latest["value"])
        if not (SMHI_TEMP_MIN <= temp_c <= SMHI_TEMP_MAX):
            print(f"  ⚠  SMHI value {temp_c} °C outside plausible range — skipped", file=sys.stderr)
            return None
        measured_at = datetime.fromtimestamp(latest["date"] / 1000, tz=TZ)
        return temp_c, measured_at
    except Exception:
        return None


def _scrape_static(url: str) -> float | None:
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return _extract_temp_from_soup(BeautifulSoup(r.text, "html.parser"))


def _scrape_selenium(url: str) -> float | None:
    """Fallback: Selenium for JS-rendered havochvatten.se pages."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=opts)
    try:
        driver.get(url)
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located(
                (By.XPATH, "//*[contains(translate(text(),'V','v'),'vattentemperatur')]")
            )
        )
        html = driver.page_source
    finally:
        driver.quit()
    return _extract_temp_from_soup(BeautifulSoup(html, "html.parser"))


def get_water_havochvatten(url: str) -> tuple[float, datetime] | None:
    """
    Secondary: scrape havochvatten.se. Static first, Selenium fallback.
    Returns (°C, fetched_at) or None. Note: site updates once daily.
    """
    try:
        val = _scrape_static(url)
        if val is not None:
            return val, datetime.now(TZ)
    except Exception:
        pass
    try:
        val = _scrape_selenium(url)
        if val is not None:
            return val, datetime.now(TZ)
    except Exception:
        pass
    return None


# ── Sunrise & sunset (astral — local calculation, no network) ─────

def get_sun_times(date=None) -> tuple[datetime, datetime, datetime, datetime]:
    """
    Returns (sunrise, earliest_on_water, sunset, off_water) for the given date.
    Defaults to today if no date is supplied.
    """
    if date is None:
        date = datetime.now(TZ).date()
    city = LocationInfo("Stockholm", "Sweden", "Europe/Stockholm", LAT, LON)
    s = sun(city.observer, date=date, tzinfo=TZ)
    sunrise = s["sunrise"]
    sunset  = s["sunset"]
    return sunrise, sunrise - timedelta(minutes=20), sunset, sunset + timedelta(minutes=20)


# ── Main ──────────────────────────────────────────────────────────

def main() -> None:
    now = datetime.now(TZ)
    print(f"Rowing conditions for Stockholm — {now:%Y-%m-%d (%A)} at {now:%H:%M} CET")
    print("─" * 60)

    try:
        air_c, air_at = get_air_temp()
    except Exception as e:
        sys.exit(f"✗ Could not fetch air temperature: {e}")

    try:
        today_date    = now.date()
        tomorrow_date = (now + timedelta(days=1)).date()
        sunrise_t,  earliest_t,  sunset_t,  off_water_t  = get_sun_times(today_date)
        sunrise_tm, earliest_tm, sunset_tm, off_water_tm = get_sun_times(tomorrow_date)
    except Exception as e:
        sys.exit(f"✗ Could not calculate sun times: {e}")

    # Fetch all water readings
    smhi        = get_water_smhi()
    hav_results = [(name, get_water_havochvatten(url)) for name, url in HAVOCHVATTEN_STATIONS]

    # Collect valid readings for averaging
    available = []
    if smhi:
        available.append(smhi[0])
    for _, result in hav_results:
        if result:
            available.append(result[0])

    if not available:
        sys.exit("✗ Could not fetch water temperature from any source.")

    # Average all available readings in °C, then convert
    water_avg_c = sum(available) / len(available)
    water_avg_f = c_to_f(water_avg_c)
    n_readings  = len(available)

    # Safety calculation (100 °F rule: convert separately then add)
    air_f      = c_to_f(air_c)
    combined_f = air_f + water_avg_f

    if combined_f < 90:
        verdict = "exercise extreme caution — PFD and 4-oars rule in effect"
    elif combined_f < 100:
        verdict = "under 100 °F — exercise caution"
    else:
        verdict = "good to go"

    # ── Output ────────────────────────────────────────────────────
    print(f"Air:      {air_c:4.1f} °C  ({air_f:.1f} °F)   {_fmt_dt(air_at, 'fetched')}  [Open-Meteo]")
    print()
    print("Water temperatures:")

    if smhi:
        sc, sat = smhi
        print(f"  E4 bron sjöv (SMHI)      {sc:4.1f} °C  ({c_to_f(sc):.1f} °F)   {_fmt_dt(sat)}  [SMHI ocobs]")
    else:
        print(f"  E4 bron sjöv (SMHI)      unavailable  [SMHI ocobs]")

    for name, result in hav_results:
        if result:
            hc, hat = result
            print(f"  {name:<22}  {hc:4.1f} °C  ({c_to_f(hc):.1f} °F)   {_fmt_dt(hat, 'fetched')}  [havochvatten.se / Copernicus forecast]")
        else:
            print(f"  {name:<22}  unavailable  [havochvatten.se]")

    avg_note = f"avg of {n_readings} readings" if n_readings > 1 else "1 reading only"
    print()
    print(f"Water avg ({avg_note}):  {water_avg_c:.1f} °C  ({water_avg_f:.1f} °F)")
    print(f"Combined (air + water avg):  {combined_f:.1f} °F  →  {verdict}")
    # Display sunrise/sunset in the order most relevant to current time.
    #   Before sunrise (after midnight):  sunrise today → sunset today
    #   Daytime (sunrise–sunset):          sunset today → sunrise tomorrow
    #   After sunset (before midnight):    sunrise tomorrow → sunset tomorrow
    if now < sunrise_t:
        # Early morning — next events are today's sunrise then today's sunset
        print(f"Sunrise:           {sunrise_t:%H:%M}    No rowing before: {earliest_t:%H:%M}")
        print(f"Sunset:            {sunset_t:%H:%M}    Off the water by: {off_water_t:%H:%M}")
    elif now < sunset_t:
        # Daytime — next events are today's sunset then tomorrow's sunrise
        print(f"Sunset:            {sunset_t:%H:%M}    Off the water by: {off_water_t:%H:%M}")
        print(f"Sunrise (tomorrow): {sunrise_tm:%H:%M}    No rowing before: {earliest_tm:%H:%M}")
    else:
        # Evening — next events are tomorrow's sunrise then tomorrow's sunset
        print(f"Sunrise (tomorrow): {sunrise_tm:%H:%M}    No rowing before: {earliest_tm:%H:%M}")
        print(f"Sunset  (tomorrow): {sunset_tm:%H:%M}    Off the water by: {off_water_tm:%H:%M}")


if __name__ == "__main__":
    main()
