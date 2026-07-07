"""Compute corridor-risk indicators per commodity per year.

Indicators:
  - HHI (Herfindahl-Hirschman Index) of origin concentration, 0..1
  - Top origin country and its import share
  - Number of distinct origin countries
  - Price volatility (stdev of monthly % returns within the year)
  - Composite risk score 0..100 (weighted blend of the above)

Run after both ingest scripts:  python src/features/risk_indicators.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import get_connection

# Composite weights: concentration is the core sovereign-risk signal.
W_HHI, W_TOP_SHARE, W_VOLATILITY = 0.5, 0.3, 0.2
# Volatility normalisation cap: monthly return stdev of 10%+ = max risk.
VOL_CAP = 0.10


def main() -> None:
    conn = get_connection()
    imports = pd.read_sql(
        """SELECT f.year, f.commodity_id, c.name AS origin, f.trade_value_usd
           FROM fact_imports f JOIN dim_country c ON c.country_code = f.origin_code
           WHERE f.trade_value_usd IS NOT NULL AND f.trade_value_usd > 0""",
        conn,
    )
    prices = pd.read_sql("SELECT period, commodity_id, price FROM fact_prices", conn)
    if imports.empty:
        sys.exit("fact_imports is empty — run the ingest scripts first.")

    prices["year"] = prices["period"].str[:4].astype(int)
    prices = prices.sort_values("period")
    prices["ret"] = prices.groupby("commodity_id")["price"].pct_change()
    vol = prices.groupby(["commodity_id", "year"])["ret"].std().rename("volatility")

    conn.execute("DELETE FROM risk_scores")
    rows = 0
    for (year, cid), grp in imports.groupby(["year", "commodity_id"]):
        total = grp["trade_value_usd"].sum()
        shares = grp.groupby("origin")["trade_value_usd"].sum() / total
        hhi = float((shares**2).sum())
        top_origin = shares.idxmax()
        top_share = float(shares.max())
        n_origins = int(shares.size)

        v = vol.get((cid, year))
        v = float(v) if v is not None and pd.notna(v) else 0.0
        vol_norm = min(v / VOL_CAP, 1.0)

        composite = 100 * (W_HHI * hhi + W_TOP_SHARE * top_share + W_VOLATILITY * vol_norm)
        conn.execute(
            "INSERT OR REPLACE INTO risk_scores "
            "(year, commodity_id, hhi, top_origin, top_origin_share, n_origins, "
            " price_volatility, composite_risk) VALUES (?,?,?,?,?,?,?,?)",
            (int(year), cid, hhi, top_origin, top_share, n_origins, v, composite),
        )
        rows += 1

    conn.commit()
    latest = pd.read_sql(
        """SELECT commodity_id, year, round(hhi,3) AS hhi, top_origin,
                  round(top_origin_share*100,1) AS top_share_pct,
                  round(composite_risk,1) AS risk
           FROM risk_scores
           WHERE year = (SELECT max(year) FROM risk_scores)
           ORDER BY risk DESC""",
        conn,
    )
    conn.close()
    print(f"Computed {rows} commodity-year risk scores.\n")
    print("Latest year, ranked by composite risk:")
    print(latest.to_string(index=False))


if __name__ == "__main__":
    main()
