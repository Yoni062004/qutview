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
python src/ingest/comtrade_imports.py      # add --sample to skip the API
python src/ingest/worldbank_prices.py      # add --sample to skip the download
python src/features/risk_indicators.py
python src/models/forecast.py

# 4. Launch the dashboard
streamlit run app/dashboard.py
```

## Project structure

```
├── app/dashboard.py               # Streamlit dashboard (risk cards, origins, forecasts)
├── src/
│   ├── common.py                  # shared constants, DB helpers, commodity registry
│   ├── db/init_db.py              # SQLite star schema (dim/fact tables)
│   ├── ingest/
│   │   ├── comtrade_imports.py    # UAE import flows by origin (UN Comtrade)
│   │   └── worldbank_prices.py    # monthly prices (World Bank Pink Sheet)
│   ├── features/risk_indicators.py  # HHI, dependency, volatility → composite risk
│   └── models/forecast.py         # SARIMAX forecasts + backtested MAPE
└── data/qutview.db                # generated — not committed
```

## Risk methodology (v1)

Per commodity-year: **composite risk (0–100)** =
50% × origin-concentration HHI + 30% × top-origin import share + 20% × normalized
price volatility (stdev of monthly returns, capped at 10%). Forecasts are a
SARIMAX(1,1,1)(1,0,1,12) baseline; the dashboard reports the model's real
6-month holdout MAPE rather than claiming accuracy.

## Roadmap

- [x] Phase 0–1: schema + ingestion (this repo)
- [x] Phase 2: corridor risk indicators
- [x] Phase 3: baseline forecasting with backtesting
- [x] Phase 4a: Streamlit dashboard
- [ ] Phase 4b: Power BI report over the same SQLite database
- [ ] Registered UN Comtrade API key (full history, no 500-record cap)
- [ ] AIS shipping-lane signals, satellite crop indices, monthly granularity
