# QUTVIEW — Sovereign Food-Corridor Risk Intelligence (Prototype)

Early-warning risk intelligence for the UAE's strategic food import corridors.
Tracks five staple commodities (wheat, rice, palm oil, sugar, poultry), scores
each import corridor for concentration and volatility risk, and produces
baseline 6-month price forecasts with honestly backtested error rates.

**Stack:** Python 3.11 · SQLite (star schema) · pandas · statsmodels (SARIMAX) · Streamlit + Plotly

**Public data sources:** UN Comtrade (UAE import flows by origin), World Bank
Pink Sheet (monthly commodity prices). Every ingest script falls back to
realistic sample data if an API is unavailable, and the dashboard displays a
LIVE / SAMPLE provenance badge — the demo always runs.

## Quick start

```powershell
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the pipeline (in this order)
python src/db/init_db.py
python src/ingest/comtrade_imports.py           # add --sample to skip the API
python src/ingest/worldbank_prices.py           # add --sample to skip the download
python src/ingest/comtrade_mirror_monthly.py    # monthly mirror flows (needs API key)
python src/ingest/faostat_production.py         # UAE domestic production (FAOSTAT bulk)
python src/features/risk_indicators.py
python src/features/risk_indicators_monthly.py
python src/features/dependency_ratios.py
python src/models/forecast.py

# 4. Launch the dashboard
streamlit run app/dashboard.py
```

## Project structure

```
├── app/dashboard.py               # Streamlit dashboard (risk cards, origins, forecasts, monthly monitor)
├── src/
│   ├── common.py                  # shared constants, DB helpers, commodity registry
│   ├── db/init_db.py              # SQLite star schema (dim/fact tables)
│   ├── ingest/
│   │   ├── comtrade_imports.py            # UAE annual import flows by origin (UN Comtrade)
│   │   ├── comtrade_mirror_monthly.py     # monthly flows via mirror statistics (origin-reported)
│   │   ├── faostat_production.py          # UAE domestic production (FAOSTAT bulk file)
│   │   └── worldbank_prices.py            # monthly prices (World Bank Pink Sheet)
│   ├── features/
│   │   ├── risk_indicators.py             # annual HHI, dependency, volatility → composite risk
│   │   ├── risk_indicators_monthly.py     # rolling 12-month risk series from mirror flows
│   │   └── dependency_ratios.py           # import dependency vs domestic production
│   └── models/forecast.py         # SARIMAX forecasts + backtested MAPE
└── data/qutview.db                # generated — not committed
```

## Risk methodology (v1)

Per commodity-year: **composite risk (0–100)** =
50% × origin-concentration HHI + 30% × top-origin import share + 20% × normalized
price volatility (stdev of monthly returns, capped at 10%). Forecasts are a
SARIMAX(1,1,1)(1,0,1,12) baseline; the dashboard reports the model's real
6-month holdout MAPE rather than claiming accuracy.

**Monthly corridor monitor (v2):** the UAE stopped publishing monthly customs
data to Comtrade after 2019, so monthly flows are reconstructed from *mirror
statistics* — each top origin's own reported exports to the UAE (FOB,
origin-reported). The same composite formula runs over a rolling 12-month
window, giving a risk series that reacts to corridor shifts within months.
Each commodity's mirror coverage is cross-checked against UAE-reported annual
imports and shown in the dashboard (51%–137%; wheat is lowest because Russia
stopped publishing trade data in 2022). Levels read higher than the annual
model because only top corridors are tracked — the series is for direction
and shift detection, not absolute comparison.

**Import dependency (v2):** imports / (imports + UAE domestic production),
by weight, using FAOSTAT production data. Most staples are 100% imported;
maize (~97%), poultry (~91%) and beef (~82%) have partial domestic cushions.
The dashboard shows an *exposure-adjusted risk* (corridor risk × import
dependency) alongside the raw corridor score. Approximations disclosed:
re-exports are not netted out, and HS import headings map only roughly to
FAO production items.

## Roadmap

- [x] Phase 0–1: schema + ingestion (this repo)
- [x] Phase 2: corridor risk indicators
- [x] Phase 3: baseline forecasting with backtesting
- [x] Phase 4a: Streamlit dashboard
- [x] Registered UN Comtrade API key (authenticated endpoint, no preview rate caps)
- [x] Monthly granularity via mirror statistics + rolling 12-month risk series
- [x] Import-dependency ratios (FAOSTAT production data) + exposure-adjusted risk
- [ ] Phase 4b: Power BI report over the same SQLite database
- [ ] AIS shipping-lane signals, satellite crop indices
