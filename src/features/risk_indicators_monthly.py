"""Compute a monthly corridor-risk series from mirror monthly flows.

For each commodity and each month, indicators are computed over the
trailing 12 months of fact_imports_monthly (so one late-reporting origin
in the newest month barely moves the needle):

  - HHI of origin concentration over the window
  - Top origin and its share of the window's import value
  - Number of active origins in the window
  - Price volatility: rolling 12-month stdev of monthly returns from
    fact_prices; months past the last published price carry the latest
    known volatility forward
  - Composite risk 0..100 with the same weights as the annual model,
    so the two series are directly comparable

Caveat inherited from the source: mirror data reflects what origin
countries report exporting to the UAE, and recent months may still be
missing slow-reporting origins. Scores are only emitted up to each
commodity's own latest reported month.

Run after comtrade_mirror_monthly.py:
    python src/features/risk_indicators_monthly.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import get_connection

# Same weights and volatility cap as the annual model (risk_indicators.py).
W_HHI, W_TOP_SHARE, W_VOLATILITY = 0.5, 0.3, 0.2
VOL_CAP = 0.10
WINDOW = 12  # months


def rolling_volatility(prices: pd.DataFrame) -> dict[str, pd.Series]:
    """Per commodity: rolling 12-month stdev of monthly returns, indexed by
    period ('YYYY-MM')."""
    out = {}
    for cid, grp in prices.groupby("commodity_id"):
        s = grp.sort_values("period").set_index("period")["price"]
        vol = s.pct_change().rolling(WINDOW).std()
        out[cid] = vol
    return out


def main() -> None:
    conn = get_connection()
    flows = pd.read_sql(
        """SELECT f.period, f.commodity_id, c.name AS origin, f.trade_value_usd
           FROM fact_imports_monthly f
           JOIN dim_country c ON c.country_code = f.origin_code
           WHERE f.trade_value_usd IS NOT NULL AND f.trade_value_usd > 0""",
        conn,
    )
    if flows.empty:
        sys.exit("fact_imports_monthly is empty — run comtrade_mirror_monthly.py first.")
    prices = pd.read_sql("SELECT period, commodity_id, price FROM fact_prices", conn)
    vol_by_cid = rolling_volatility(prices)

    conn.execute("DELETE FROM risk_scores_monthly")
    rows = 0
    for cid, grp in flows.groupby("commodity_id"):
        pivot = (
            grp.pivot_table(index="period", columns="origin",
                            values="trade_value_usd", aggfunc="sum")
            .fillna(0.0)
            .sort_index()
        )
        # Continuous month index so trailing windows cover real calendar time
        # even when a month has no reported flows at all.
        full_range = pd.period_range(pivot.index.min(), pivot.index.max(), freq="M")
        pivot = pivot.reindex([str(p) for p in full_range], fill_value=0.0)

        vol_series = vol_by_cid.get(cid, pd.Series(dtype=float))
        last_known_vol = None

        for i in range(WINDOW - 1, len(pivot)):
            window = pivot.iloc[i - WINDOW + 1: i + 1]
            totals = window.sum()
            totals = totals[totals > 0]
            if totals.empty:
                continue
            shares = totals / totals.sum()
            hhi = float((shares**2).sum())
            top_origin = shares.idxmax()
            top_share = float(shares.max())
            period = pivot.index[i]

            v = vol_series.get(period)
            if v is not None and pd.notna(v):
                last_known_vol = float(v)
            v_used = last_known_vol if last_known_vol is not None else 0.0
            vol_norm = min(v_used / VOL_CAP, 1.0)

            composite = 100 * (W_HHI * hhi + W_TOP_SHARE * top_share + W_VOLATILITY * vol_norm)
            conn.execute(
                "INSERT OR REPLACE INTO risk_scores_monthly "
                "(period, commodity_id, hhi, top_origin, top_origin_share, n_origins, "
                " price_volatility, composite_risk) VALUES (?,?,?,?,?,?,?,?)",
                (period, cid, hhi, top_origin, top_share, int(shares.size), v_used, composite),
            )
            rows += 1

    conn.commit()
    latest = pd.read_sql(
        """SELECT m.commodity_id, m.period, round(m.hhi,3) AS hhi, m.top_origin,
                  round(m.top_origin_share*100,1) AS top_share_pct,
                  round(m.composite_risk,1) AS risk
           FROM risk_scores_monthly m
           JOIN (SELECT commodity_id, max(period) AS period
                 FROM risk_scores_monthly GROUP BY commodity_id) x
             ON x.commodity_id = m.commodity_id AND x.period = m.period
           ORDER BY risk DESC""",
        conn,
    )
    conn.close()
    print(f"Computed {rows} monthly risk scores.\n")
    print("Latest month per commodity, ranked by composite risk:")
    print(latest.to_string(index=False))


if __name__ == "__main__":
    main()
