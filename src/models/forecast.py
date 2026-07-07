"""Baseline price forecasting with SARIMAX + honest backtesting.

For each commodity:
  1. Hold out the last 6 months, fit SARIMAX on the rest, measure MAPE
     on the holdout — stored in backtest_metrics so the dashboard can
     report real accuracy, not a claim.
  2. Refit on the full series and forecast 6 months ahead with 90% CI.

Run after the price ingest:  python src/models/forecast.py
"""
import sys
import warnings
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import COMMODITIES, get_connection

warnings.filterwarnings("ignore")  # statsmodels convergence chatter

HORIZON = 6  # months
ORDER = (1, 1, 1)
SEASONAL_ORDER = (1, 0, 1, 12)


def fit_forecast(series: pd.Series, steps: int):
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    model = SARIMAX(
        series, order=ORDER, seasonal_order=SEASONAL_ORDER,
        enforce_stationarity=False, enforce_invertibility=False,
    )
    res = model.fit(disp=False)
    fc = res.get_forecast(steps=steps)
    ci = fc.conf_int(alpha=0.10)
    return fc.predicted_mean, ci.iloc[:, 0], ci.iloc[:, 1]


def main() -> None:
    conn = get_connection()
    prices = pd.read_sql("SELECT period, commodity_id, price FROM fact_prices", conn)
    if prices.empty:
        sys.exit("fact_prices is empty — run src/ingest/worldbank_prices.py first.")

    prices["date"] = pd.to_datetime(prices["period"] + "-01")
    conn.execute("DELETE FROM forecasts")
    conn.execute("DELETE FROM backtest_metrics")

    for cid in COMMODITIES:
        s = (
            prices[prices["commodity_id"] == cid]
            .set_index("date")["price"]
            .sort_index()
            .asfreq("MS")
            .interpolate()
        )
        if len(s) < 36:
            print(f"{cid}: only {len(s)} months of data — skipping.")
            continue

        # 1) Backtest on the last HORIZON months
        train, test = s[:-HORIZON], s[-HORIZON:]
        pred, _, _ = fit_forecast(train, HORIZON)
        mape = float((abs((test.values - pred.values) / test.values)).mean() * 100)
        conn.execute(
            "INSERT OR REPLACE INTO backtest_metrics "
            "(commodity_id, horizon_months, mape_pct, evaluated_at) VALUES (?,?,?,?)",
            (cid, HORIZON, mape, datetime.utcnow().isoformat(timespec="seconds")),
        )

        # 2) Forward forecast from the full series
        mean, lo, hi = fit_forecast(s, HORIZON)
        for ts, m, l, h in zip(mean.index, mean.values, lo.values, hi.values):
            conn.execute(
                "INSERT OR REPLACE INTO forecasts "
                "(period, commodity_id, forecast, lower_ci, upper_ci) VALUES (?,?,?,?,?)",
                (ts.strftime("%Y-%m"), cid, float(m), float(l), float(h)),
            )
        print(f"{cid}: 6-month backtest MAPE {mape:.1f}% | forecast to {mean.index[-1]:%Y-%m}")

    conn.commit()
    conn.close()
    print("\nForecasts and backtest metrics stored.")


if __name__ == "__main__":
    main()
