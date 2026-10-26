# Real Exchange Rate App

A Streamlit app that downloads monthly IMF CPI and exchange-rate data and computes the bilateral real exchange rate between any two IMF countries (including the Euro area).

## Economics

| Symbol | Definition |
|---|---|
| S(t) | Units of **Country 2** currency per 1 unit of **Country 1** currency |
| P1(t), P2(t) | Monthly CPI of Country 1 and Country 2 |
| RER(t) | S(t) × P1(t) / P2(t) |
| RER* | Average of RER(t) over valid months in the selected sample |
| Real Index(t) | 100 × RER(t) / RER* |

IMF exchange rates are local-currency units per USD (key `XDC_USD`), so the app converts them:

```
S(t) = [Country 2 LCU per USD] / [Country 1 LCU per USD]
```

Example: Country 1 = United Kingdom, Country 2 = United States gives S in **USD per GBP** (about 1.97 in Jan 2008).

A rising real index means Country 1's currency has appreciated in real terms against Country 2's.

## Features

- Choose Country 1 and Country 2 from all IMF countries that have both a monthly exchange-rate series and a CPI series, plus the Euro area (`G163`).
- Monthly data from January 1976 where the IMF has it.
- Pick start and end months; RER* and the real index are recalculated whenever the dates change.
- Interactive Plotly chart showing **Real**, **Nominal**, or **Both** (Both uses separate left and right axes). The nominal S(t) is plotted in actual currency units and is not normalized.
- CSV download of every month in the selected sample, with inputs (both FX rates, both CPIs) and calculations (S, RER, RER*, Real Index, valid flag).
- A built-in "Check one calculation" panel that works one month through by hand.

## Data

IMF SDMX 3.0 API: `https://api.imf.org/external/sdmx/3.0` (no API key needed).

| Series | Dataflow | Key |
|---|---|---|
| CPI | `IMF.STA:CPI` | `{ISO3}.CPI._T.IX.M` |
| Exchange rate (period average) | `IMF.STA:ER` | `{ISO3}.XDC_USD.PA_RT.M` |

Notes:
- The Euro area (`G163`) has no `CPI` index type in this dataflow, so the app uses its **HICP** series (`G163.HICP._T.IX.M`). Euro area data starts around 1999.
- Months where any of the four inputs is missing are left as gaps. They are excluded from RER* and appear as blanks in the CSV.
- Downloads are cached for 24 hours. If the IMF API is temporarily unavailable, the app shows an error; try again later.

## Files

- `app.py`: the Streamlit app
- `requirements.txt`: Python dependencies
- `README.md`: this file

## Run

Deployed on Streamlit Community Cloud (Python 3.12, main file `app.py`).

To run it locally instead:

```bash
pip install -r requirements.txt
streamlit run app.py
```
