"""Real Exchange Rate Explorer — Streamlit app using IMF SDMX 3.0 data.

Definitions
-----------
S(t)   : units of Country 2 currency per 1 unit of Country 1 currency
P1, P2 : monthly CPI of Country 1 and Country 2 (HICP for the Euro area)
RER(t) = S(t) * P1(t) / P2(t)
RER*   = average of RER(t) over valid months in the selected sample
Real Index(t) = 100 * RER(t) / RER*

IMF exchange rates (XDC_USD) are local-currency units per USD, so
S(t) = [Country 2 LCU per USD] / [Country 1 LCU per USD].
"""

from __future__ import annotations

import io

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from plotly.subplots import make_subplots

IMF_API = "https://api.imf.org/external/sdmx/3.0"
START_MONTH = pd.Period("1976-01", freq="M")
EURO_AREA = "G163"
HEADERS = {"Accept": "application/json", "User-Agent": "real-exchange-rate-app/1.0"}
TIMEOUT = 90
REAL_COLOR = "#2563eb"
NOMINAL_COLOR = "#ea580c"

# CPI index types to try, in order. The Euro area (G163) only publishes HICP.
CPI_INDEX_TYPES = ["CPI", "HICP"]

# Currency codes for nicer axis labels. Countries not listed fall back to
# "<country name> currency".
CURRENCY = {
    "USA": "USD", "GBR": "GBP", "G163": "EUR", "JPN": "JPY", "CHN": "CNY",
    "CAN": "CAD", "AUS": "AUD", "NZL": "NZD", "CHE": "CHF", "SWE": "SEK",
    "NOR": "NOK", "DNK": "DKK", "ISL": "ISK", "POL": "PLN", "CZE": "CZK",
    "HUN": "HUF", "ROU": "RON", "BGR": "BGN", "TUR": "TRY", "RUS": "RUB",
    "UKR": "UAH", "ISR": "ILS", "SAU": "SAR", "ARE": "AED", "EGY": "EGP",
    "ZAF": "ZAR", "NGA": "NGN", "KEN": "KES", "IND": "INR", "PAK": "PKR",
    "BGD": "BDT", "LKA": "LKR", "IDN": "IDR", "MYS": "MYR", "SGP": "SGD",
    "THA": "THB", "PHL": "PHP", "VNM": "VND", "KOR": "KRW", "HKG": "HKD",
    "TWN": "TWD", "MEX": "MXN", "BRA": "BRL", "ARG": "ARS", "CHL": "CLP",
    "COL": "COP", "PER": "PEN", "URY": "UYU", "MAR": "MAD", "DZA": "DZD",
    "TUN": "TND", "GHA": "GHS", "ETH": "ETB", "IRN": "IRR", "KAZ": "KZT",
    "QAT": "QAR", "KWT": "KWD", "OMN": "OMR", "BHR": "BHD", "JOR": "JOD",
}


# ---------------------------------------------------------------------------
# IMF data access
# ---------------------------------------------------------------------------

def _get_json(url: str, params: dict | None = None) -> dict:
    resp = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _parse_sdmx_series(payload: dict) -> dict[tuple[str, ...], pd.Series]:
    """Turn an SDMX-JSON 2.0 data message into {series key tuple: monthly Series}."""
    data = payload.get("data", {})
    datasets = data.get("dataSets") or []
    structures = data.get("structures") or []
    if not datasets or not structures or "series" not in datasets[0]:
        return {}

    dims = structures[0]["dimensions"]
    series_dims = dims["series"]
    time_values = [v.get("value", v.get("id")) for v in dims["observation"][0]["values"]]

    out: dict[tuple[str, ...], pd.Series] = {}
    for key, s in datasets[0]["series"].items():
        idx = [int(i) for i in key.split(":")]
        key_ids = tuple(series_dims[d]["values"][i]["id"] for d, i in enumerate(idx))
        periods, values = [], []
        for t_idx, obs in s.get("observations", {}).items():
            raw = obs[0] if isinstance(obs, list) and obs else None
            try:
                periods.append(pd.Period(time_values[int(t_idx)].replace("-M", "-"), freq="M"))
                values.append(raw)
            except (ValueError, IndexError):
                continue
        ser = pd.Series(pd.to_numeric(values, errors="coerce"), index=pd.PeriodIndex(periods, freq="M"))
        out[key_ids] = ser.sort_index()
    return out


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_series(dataflow: str, key: str) -> pd.Series:
    """Fetch one monthly IMF series (from January 1976 on). Empty Series if no data."""
    url = f"{IMF_API}/data/dataflow/IMF.STA/{dataflow}/+/{key}"
    payload = _get_json(url, {"dimensionAtObservation": "TIME_PERIOD", "attributes": "none"})
    series = _parse_sdmx_series(payload)
    if not series:
        return pd.Series(dtype=float, index=pd.PeriodIndex([], freq="M"))
    ser = next(iter(series.values()))
    ser = ser[ser.index >= START_MONTH]
    return ser[~ser.index.duplicated(keep="last")].dropna()


def fetch_cpi(iso: str) -> tuple[pd.Series, str]:
    """Monthly CPI index for a country; falls back to HICP (needed for the Euro area)."""
    for index_type in CPI_INDEX_TYPES:
        ser = fetch_series("CPI", f"{iso}.{index_type}._T.IX.M")
        if not ser.empty:
            return ser, index_type
    return ser, "none"


def fetch_fx(iso: str) -> pd.Series:
    """Monthly average exchange rate, local currency units per USD."""
    if iso == "USA":
        # Not strictly needed (IMF reports USA = 1), but avoids a failed call.
        try:
            ser = fetch_series("ER", "USA.XDC_USD.PA_RT.M")
            if not ser.empty:
                return ser
        except requests.RequestException:
            pass
        months = pd.period_range(START_MONTH, pd.Timestamp.today().to_period("M"), freq="M")
        return pd.Series(1.0, index=months)
    return fetch_series("ER", f"{iso}.XDC_USD.PA_RT.M")


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_country_list() -> pd.DataFrame:
    """Countries (plus Euro area) that have both an IMF FX series and a CPI/HICP series."""
    names_payload = _get_json(f"{IMF_API}/structure/codelist/IMF/CL_COUNTRY/+")
    names = {c["id"]: c.get("name", c["id"]) for c in names_payload["data"]["codelists"][0]["codes"]}

    def countries_with(dataflow: str, key: str) -> set[str]:
        payload = _get_json(
            f"{IMF_API}/data/dataflow/IMF.STA/{dataflow}/+/{key}",
            {"lastNObservations": 1, "attributes": "none"},
        )
        return {k[0] for k in _parse_sdmx_series(payload)}

    fx = countries_with("ER", "*.XDC_USD.PA_RT.M") | {"USA"}
    cpi = countries_with("CPI", "*.*._T.IX.M")
    codes = sorted(fx & cpi)
    df = pd.DataFrame({"iso": codes, "name": [names.get(c, c) for c in codes]})
    df.loc[df["iso"] == EURO_AREA, "name"] = "Euro area"
    return df.sort_values("name").reset_index(drop=True)


FALLBACK_COUNTRIES = pd.DataFrame(
    {
        "iso": ["G163", "USA", "GBR", "JPN", "CAN", "CHE", "AUS", "CHN", "IND", "MEX", "BRA", "KOR", "ZAF"],
        "name": ["Euro area", "United States", "United Kingdom", "Japan", "Canada", "Switzerland",
                 "Australia", "China", "India", "Mexico", "Brazil", "Korea", "South Africa"],
    }
).sort_values("name").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Economics
# ---------------------------------------------------------------------------

def build_dataset(fx1: pd.Series, fx2: pd.Series, p1: pd.Series, p2: pd.Series) -> pd.DataFrame:
    """Align inputs monthly and compute S(t) and RER(t). Gaps stay as NaN."""
    df = pd.concat(
        {"FX1_LCU_per_USD": fx1, "FX2_LCU_per_USD": fx2, "P1_CPI": p1, "P2_CPI": p2}, axis=1
    ).sort_index()
    if df.empty:
        return df
    df = df.reindex(pd.period_range(df.index.min(), df.index.max(), freq="M"))
    df["S_c2_per_c1"] = df["FX2_LCU_per_USD"] / df["FX1_LCU_per_USD"]
    df["RER"] = df["S_c2_per_c1"] * df["P1_CPI"] / df["P2_CPI"]
    df["valid"] = df["RER"].notna() & (df["RER"] > 0)
    df.loc[~df["valid"], "RER"] = float("nan")
    return df


def apply_sample(df: pd.DataFrame, start: pd.Period, end: pd.Period) -> tuple[pd.DataFrame, float]:
    """Restrict to [start, end], compute RER* over valid months and the real index."""
    sample = df.loc[(df.index >= start) & (df.index <= end)].copy()
    rer_star = sample.loc[sample["valid"], "RER"].mean()
    sample["RER_star"] = rer_star
    sample["Real_Index"] = 100 * sample["RER"] / rer_star
    return sample, rer_star


def currency_label(iso: str, name: str) -> str:
    return CURRENCY.get(iso, f"{name} currency")


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def make_chart(sample: pd.DataFrame, view: str, s_label: str, title: str) -> go.Figure:
    x = sample.index.to_timestamp()
    real = go.Scatter(x=x, y=sample["Real_Index"], name="Real index (sample avg = 100)",
                      mode="lines", line=dict(color=REAL_COLOR, width=2.2), connectgaps=False)
    nominal = go.Scatter(x=x, y=sample["S_c2_per_c1"], name=f"Nominal S ({s_label})",
                         mode="lines", line=dict(color=NOMINAL_COLOR, width=2.2), connectgaps=False)

    if view == "Both":
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(real, secondary_y=False)
        fig.add_trace(nominal, secondary_y=True)
        fig.update_yaxes(title_text="Real index (avg = 100)", secondary_y=False)
        fig.update_yaxes(title_text=f"S(t): {s_label}", secondary_y=True)
        fig.add_hline(y=100, line_dash="dot", line_color="gray", secondary_y=False)
    elif view == "Real":
        fig = go.Figure(real)
        fig.update_yaxes(title_text="Real index (avg = 100)")
        fig.add_hline(y=100, line_dash="dot", line_color="gray")
    else:
        fig = go.Figure(nominal)
        fig.update_yaxes(title_text=f"S(t): {s_label}")

    fig.update_layout(
        title=dict(text=title, x=0, font=dict(size=18)),
        template="plotly_white", hovermode="x unified", height=540,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(t=90, b=20, l=10, r=10),
    )
    fig.update_xaxes(
        showgrid=False,
        rangeselector=dict(buttons=[
            dict(count=5, label="5Y", step="year", stepmode="backward"),
            dict(count=10, label="10Y", step="year", stepmode="backward"),
            dict(count=20, label="20Y", step="year", stepmode="backward"),
            dict(step="all", label="All"),
        ]),
    )
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)", zeroline=False)
    if view == "Both":
        fig.update_yaxes(title_font_color=REAL_COLOR, tickfont_color=REAL_COLOR, secondary_y=False)
        fig.update_yaxes(title_font_color=NOMINAL_COLOR, tickfont_color=NOMINAL_COLOR,
                         showgrid=False, secondary_y=True)
    return fig


def export_frame(sample: pd.DataFrame, c1: str, c2: str, cpi1: str, cpi2: str) -> pd.DataFrame:
    out = sample.copy()
    out.insert(0, "month", out.index.strftime("%Y-%m"))
    out = out.rename(columns={
        "FX1_LCU_per_USD": f"FX1_{c1}_LCU_per_USD",
        "FX2_LCU_per_USD": f"FX2_{c2}_LCU_per_USD",
        "P1_CPI": f"P1_{c1}_{cpi1}",
        "P2_CPI": f"P2_{c2}_{cpi2}",
        "S_c2_per_c1": f"S_{c2}_per_{c1}",
    })
    return out.reset_index(drop=True)


def main() -> None:
    st.set_page_config(page_title="Real Exchange Rate Explorer", page_icon="💱", layout="wide")
    st.title("💱 Real Exchange Rate Explorer")
    st.markdown(
        "Compare two currencies in **nominal** and **real** (inflation-adjusted) terms "
        "using monthly IMF exchange-rate and CPI data from January 1976."
    )

    try:
        countries = fetch_country_list()
    except Exception as exc:  # network / API problems
        st.warning(f"Could not load the full IMF country list ({exc}). Showing a short default list.")
        countries = FALLBACK_COUNTRIES

    names = countries["name"].tolist()
    iso_of = dict(zip(countries["name"], countries["iso"]))
    default1 = names.index("United Kingdom") if "United Kingdom" in names else 0
    default2 = names.index("United States") if "United States" in names else min(1, len(names) - 1)

    with st.container(border=True):
        col1, col2 = st.columns(2)
        name1 = col1.selectbox("🌍 Country 1 (base currency)", names, index=default1)
        name2 = col2.selectbox("🌎 Country 2 (quote currency)", names, index=default2)

    iso1, iso2 = iso_of[name1], iso_of[name2]
    if iso1 == iso2:
        st.info("Choose two different countries.")
        st.stop()

    with st.spinner("Downloading IMF data…"):
        try:
            fx1, fx2 = fetch_fx(iso1), fetch_fx(iso2)
            (p1, cpi1), (p2, cpi2) = fetch_cpi(iso1), fetch_cpi(iso2)
        except Exception as exc:
            st.error(f"IMF data download failed: {exc}. The IMF API may be temporarily unavailable — "
                     "please try again in a minute.")
            st.stop()

    missing = [label for label, ser in [
        (f"exchange rate for {name1}", fx1), (f"exchange rate for {name2}", fx2),
        (f"CPI for {name1}", p1), (f"CPI for {name2}", p2)] if ser.empty]
    if missing:
        st.error("The IMF has no monthly data for: " + ", ".join(missing) + ".")
        st.stop()

    full = build_dataset(fx1, fx2, p1, p2)
    valid_months = full.index[full["valid"]]
    if valid_months.empty:
        st.error("No month has all four inputs (both exchange rates and both CPIs). Try another pair.")
        st.stop()

    cur1, cur2 = currency_label(iso1, name1), currency_label(iso2, name2)
    s_label = f"{cur2} per 1 {cur1}"

    month_options = list(pd.period_range(valid_months.min(), valid_months.max(), freq="M"))
    with st.container(border=True):
        c_period, c_view = st.columns([3, 1], gap="large")
        start, end = c_period.select_slider(
            "📅 Sample period (start and end month)",
            options=month_options,
            value=(month_options[0], month_options[-1]),
            format_func=lambda p: p.strftime("%b %Y"),
        )
        view = c_view.radio("📈 Chart shows", ["Real", "Nominal", "Both"], index=2, horizontal=True)

    sample, rer_star = apply_sample(full, start, end)
    n_valid = int(sample["valid"].sum())
    if n_valid == 0:
        st.error("No valid months in the selected period.")
        st.stop()

    gaps = len(sample) - n_valid
    if gaps:
        st.warning(f"{gaps} month(s) in the selected period are missing at least one input; "
                   "they are excluded from RER* and shown as gaps.")
    if cpi1 == "HICP" or cpi2 == "HICP":
        st.caption("ℹ️ The Euro area uses the IMF HICP series as its CPI.")

    last = sample[sample["valid"]].iloc[-1]
    c_a, c_b, c_c, c_d = st.columns(4)
    c_a.metric("RER* (sample average RER)", f"{rer_star:,.6g}", border=True)
    c_b.metric("Valid months in sample", f"{n_valid}", border=True)
    c_c.metric(f"Nominal S, {last.name.strftime('%b %Y')}", f"{last['S_c2_per_c1']:,.6g}",
               help=s_label, border=True)
    c_d.metric(f"Real index, {last.name.strftime('%b %Y')}", f"{last['Real_Index']:,.2f}",
               delta=f"{last['Real_Index'] - 100:+.1f} vs sample average", border=True)

    export = export_frame(sample, iso1, iso2, cpi1, cpi2)
    tab_chart, tab_data, tab_method = st.tabs(["📈 Chart", "📋 Data", "🧮 How it's calculated"])

    with tab_chart:
        title = f"{name1} vs {name2} · {start.strftime('%b %Y')} – {end.strftime('%b %Y')}"
        st.plotly_chart(make_chart(sample, view, s_label, title), width="stretch")
        st.caption(
            f"A higher real index means {cur1} is stronger in real terms against {cur2} "
            f"(a real appreciation of {cur1}). The nominal series is in actual currency units: {s_label}."
        )

    with tab_data:
        st.dataframe(export, width="stretch", hide_index=True)

    with tab_method:
        st.latex(r"S(t) = \frac{\text{Country 2 LCU per USD}}{\text{Country 1 LCU per USD}}"
                 rf"\quad\left[\text{{{s_label}}}\right]")
        st.latex(r"RER(t) = S(t)\times\frac{P_1(t)}{P_2(t)}")
        st.latex(r"RER^{*} = \text{average of } RER(t) \text{ over valid months in the sample}")
        st.latex(r"\text{Real Index}(t) = 100\times\frac{RER(t)}{RER^{*}}")
        r = last
        st.markdown(
            f"**Worked example: {r.name.strftime('%B %Y')}**\n\n"
            f"| Step | Value |\n|---|---|\n"
            f"| FX1 ({cur1} per USD) | {r['FX1_LCU_per_USD']:.6f} |\n"
            f"| FX2 ({cur2} per USD) | {r['FX2_LCU_per_USD']:.6f} |\n"
            f"| S = FX2 / FX1 ({s_label}) | {r['S_c2_per_c1']:.6f} |\n"
            f"| P1 ({name1} {cpi1}) | {r['P1_CPI']:.4f} |\n"
            f"| P2 ({name2} {cpi2}) | {r['P2_CPI']:.4f} |\n"
            f"| RER = S × P1 / P2 | {r['RER']:.6f} |\n"
            f"| RER* | {rer_star:.6f} |\n"
            f"| Real index = 100 × RER / RER* | {r['Real_Index']:.4f} |\n"
            f"| Average real index over valid months | "
            f"{sample.loc[sample['valid'], 'Real_Index'].mean():.6f} |"
        )

    buf = io.StringIO()
    export.to_csv(buf, index=False)
    st.download_button(
        "⬇️ Download CSV (selected sample period)",
        data=buf.getvalue(),
        file_name=f"rer_{iso1}_{iso2}_{start.strftime('%Y%m')}_{end.strftime('%Y%m')}.csv",
        mime="text/csv",
        type="primary",
    )

    st.divider()
    st.caption("Source: IMF, SDMX 3.0 API (dataflows IMF.STA:CPI and IMF.STA:ER).")


if __name__ == "__main__":
    main()
