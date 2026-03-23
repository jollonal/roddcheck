"""
app.py — Streamlit web interface for rowcheck.py

Run locally:
    pip install streamlit
    streamlit run app.py

Deploy free at https://streamlit.io/cloud:
    1. Push both app.py, rowcheck.py, and requirements.txt to a GitHub repo
    2. Sign in at share.streamlit.io with GitHub
    3. Click "New app", point it at app.py
    4. Share the URL with your fellow rowers

Both app.py and rowcheck.py must be in the same directory / repo.
"""

import streamlit as st
from datetime import datetime, timedelta

from rowcheck import (
    TZ,
    HAVOCHVATTEN_STATIONS,
    c_to_f,
    _fmt_dt,
    get_air_temp,
    get_water_smhi,
    get_water_havochvatten,
    get_sun_times,
)

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Roddcheck Stockholm",
    page_icon="🚣",
    layout="centered",
)

# ── Header ────────────────────────────────────────────────────────────────────

st.title("🚣 Roddcheck Stockholm")
st.write(
    "Cold water is dangerous long before you feel cold. "
    "This app checks whether today's air and water temperatures are safe for rowing "
    "using the **combined temperature rule** — add air + water (in °F). "
    "Below **100 °F combined (≈ 38 °C total)**: exercise caution. "
    "Below **90 °F combined (≈ 32 °C total)**: life jacket (PFD) and minimum four oars (2x) on the water."
)

now = datetime.now(TZ)
col_time, col_btn = st.columns([3, 1])
col_time.caption(f"Checked at {now:%H:%M} CET · {now:%-d %b %Y}")
if col_btn.button("🔄 Refresh", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

st.divider()

# ── Data fetching (cached 10 min) ─────────────────────────────────────────────

@st.cache_data(ttl=600, show_spinner="Fetching conditions…")
def fetch_all():
    air_c, air_ts = get_air_temp()
    smhi          = get_water_smhi()
    hav_results   = [
        (name, get_water_havochvatten(url))
        for name, url in HAVOCHVATTEN_STATIONS
    ]
    return air_c, air_ts, smhi, hav_results


try:
    air_c, air_ts, smhi, hav_results = fetch_all()
except Exception as exc:
    st.error(f"Could not fetch data: {exc}")
    st.stop()

# Collect valid water readings for averaging
available = []
if smhi:
    available.append(smhi[0])
for _, result in hav_results:
    if result:
        available.append(result[0])

if not available:
    st.error("Could not fetch water temperature from any source.")
    st.stop()

water_avg_c = sum(available) / len(available)
water_avg_f = c_to_f(water_avg_c)
air_f       = c_to_f(air_c)
combined_f  = air_f + water_avg_f

# ── Verdict ───────────────────────────────────────────────────────────────────

if combined_f < 90:
    st.error(
        f"**⛔ Cold water rules in effect — {combined_f:.1f} °F combined**\n\n"
        f"Life jacket (PFD) recommended · Minimum four oars (2x) on the water · No single sculling"
    )
elif combined_f < 100:
    st.warning(
        f"**⚠️ Caution — {combined_f:.1f} °F combined**\n\n"
        f"Below the 100 °F threshold. Row with extra care and stay close to shore."
    )
else:
    st.success(
        f"**✅ Good to go — {combined_f:.1f} °F combined**\n\n"
        f"Combined temperature is above 100 °F. Normal rowing conditions."
    )

# ── Safety rules explainer ────────────────────────────────────────────────────

with st.expander("ℹ️ How the safety rules work"):
    st.markdown("""
#### Why cold water is dangerous

Cold water causes serious problems long before hypothermia sets in. The immediate
risk is **cold shock**: when you hit water below about 15 °C, your body triggers
an uncontrollable gasping reflex that can cause you to inhale water within seconds.
This is followed quickly by **swimming failure** — the arms and legs lose coordination
and strength within 3–5 minutes, even in fit rowers who feel mentally alert.
Hypothermia (a drop in core body temperature) comes last, but by then you may
already be unable to swim.

Safety experts often use the 1-10-1 rule to explain the phases of cold water immersion:

1 Minute: You have roughly sixty seconds to get your breathing under control. If you
don't panic and keep your head up, the "cold shock" phase will pass.

10 Minutes: You have about ten minutes of "meaningful movement." After this, your nerves 
and muscles in your extremities get too cold to work, and you lose the ability to swim or 
grip a life ring (cold incapacitation).

1 Hour: It typically takes at least an hour before you lose consciousness due to hypothermia.

#### Pro Tip: This is why life jackets are so critical in cold water.
They keep your head above the surface during that first uncontrollable minute so that when
you gasp, you're inhaling air, not the lake.

#### The combined temperature rule

The rule was developed by USRowing (USA) as a practical field check: add the current
**air temperature and water temperature together in Fahrenheit**. The sum tells you
how much cold stress your body faces if you end up in the water.

| Combined temp | Conditions | What's required |
|---|---|---|
| **100 °F or above** (≈ 38 °C total) | Safe for normal rowing | Nothing extra |
| **90–99 °F** (≈ 32–37 °C total) | Caution zone | Row carefully, stay near shore |
| **Below 90 °F** (≈ below 32 °C total) | Cold water rules apply | Life jacket + min. a 2x |

*Why Fahrenheit?* The rule was designed in °F, which gives more useful granularity
in the cold range. The thresholds feel arbitrary in Celsius, but the underlying
safety logic is sound. 100 °F combined (e.g. air 10 °C + water 11 °C) is roughly
the boundary where a short unplanned swim is survivable with reasonable fitness.

#### The four-oar rule (minimum a 2x)

Below 90 °F combined, **no single sculling**. The smallest safe boat is a double —
so that if one rower capsizes, the other can assist and call for help. In practice:
no 1x, no sculling alone off a dock.

#### Life jacket (flytväst)

Below 90 °F combined, a life jacket should be **worn**, not just carried on board.
A conscious swimmer in cold water loses effective arm movement within minutes.
A life jacket keeps an incapacitated rower afloat without any effort on their part.
An inflatable belt-style PFD (the thin kind worn around the waist) counts —
it does not have to be a bulky foam vest.
""")

# ── Temperatures ──────────────────────────────────────────────────────────────

st.subheader("Temperatures")

col_air, col_water = st.columns(2)
col_air.metric(
    label="🌡 Air",
    value=f"{air_c:.1f} °C",
    help=f"{air_f:.1f} °F · {_fmt_dt(air_ts, 'fetched')} [Open-Meteo]",
)
col_water.metric(
    label="🌊 Water (avg)",
    value=f"{water_avg_c:.1f} °C",
    help=(
        f"{water_avg_f:.1f} °F · "
        f"avg of {len(available)} reading{'s' if len(available) != 1 else ''}"
    ),
)

with st.expander("Water sources"):
    if smhi:
        sc, sat = smhi
        st.write(
            f"**E4 bron sjöv (SMHI):** {sc:.1f} °C ({c_to_f(sc):.1f} °F)"
            f" · {_fmt_dt(sat, 'measured')} · real-time"
        )
    else:
        st.write("**E4 bron sjöv (SMHI):** unavailable")

    for name, result in hav_results:
        if result:
            hc, _ = result
            st.write(
                f"**{name}:** {hc:.1f} °C ({c_to_f(hc):.1f} °F)"
                f" · Copernicus model forecast"
            )
        else:
            st.write(f"**{name}:** unavailable")

# ── Sunrise / Sunset ──────────────────────────────────────────────────────────

st.subheader("Daylight")

try:
    today_date    = now.date()
    tomorrow_date = (now + timedelta(days=1)).date()
    sunrise_t,  earliest_t,  sunset_t,  off_water_t  = get_sun_times(today_date)
    sunrise_tm, earliest_tm, sunset_tm, off_water_tm = get_sun_times(tomorrow_date)
except Exception as exc:
    st.warning(f"Could not calculate sun times: {exc}")
else:
    col1, col2 = st.columns(2)

    if now < sunrise_t:
        # Early morning — show today's sunrise then sunset
        col1.metric("🌅 Sunrise", sunrise_t.strftime("%H:%M"),
                    help=f"No rowing before {earliest_t:%H:%M}")
        col2.metric("🌇 Sunset", sunset_t.strftime("%H:%M"),
                    help=f"Off the water by {off_water_t:%H:%M}")
        st.caption(
            f"No rowing before **{earliest_t:%H:%M}** · "
            f"Off the water by **{off_water_t:%H:%M}**"
        )
    elif now < sunset_t:
        # Daytime — show today's sunset then tomorrow's sunrise
        col1.metric("🌇 Sunset today", sunset_t.strftime("%H:%M"),
                    help=f"Off the water by {off_water_t:%H:%M}")
        col2.metric("🌅 Sunrise tomorrow", sunrise_tm.strftime("%H:%M"),
                    help=f"No rowing before {earliest_tm:%H:%M}")
        st.caption(
            f"Off the water by **{off_water_t:%H:%M}** · "
            f"Tomorrow no rowing before **{earliest_tm:%H:%M}**"
        )
    else:
        # Evening — show tomorrow's sunrise then sunset
        col1.metric("🌅 Sunrise tomorrow", sunrise_tm.strftime("%H:%M"),
                    help=f"No rowing before {earliest_tm:%H:%M}")
        col2.metric("🌇 Sunset tomorrow", sunset_tm.strftime("%H:%M"),
                    help=f"Off the water by {off_water_tm:%H:%M}")
        st.caption(
            f"No rowing before **{earliest_tm:%H:%M}** · "
            f"Off the water by **{off_water_tm:%H:%M}**"
        )

# ── Footer ────────────────────────────────────────────────────────────────────

st.divider()
st.caption(
    "Sources: "
    "[SMHI ocobs](https://opendata.smhi.se) · "
    "[havochvatten.se / Copernicus](https://www.havochvatten.se) · "
    "[Open-Meteo](https://open-meteo.com)"
)
