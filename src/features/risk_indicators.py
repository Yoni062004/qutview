"""Compute corridor-risk indicators per commodity per year.

Indicators:
  - HHI (Herfindahl-Hirschman Index) of origin concentration, 0..1
  - Top origin country and its import share
  - Number of distinct origin countries
  - Price volatility (stdev of monthly % returns within the year)
  - Composite risk score 0..100 (weighted blend of the above)

Each year is scored from the best available source, recorded in
risk_scores.source: UAE-reported customs data ('uae_reported') where the UAE
has published, annual mirror statistics ('mirror_derived') for more recent
years. Mirror years are only scored for commodities whose mirror covered at
least MIN_MIRROR_COVERAGE_PCT of UAE-reported value on the latest overlap
year — below that, a major origin is missing from partner reporting (e.g.
Russia stopped publishing after 2021) and mirror-derived concentration
metrics would name the wrong top origin, so the commodity honestly stays at
its last UAE-reported year.

Run after the ingest scripts:  python src/features/risk_indicators.py
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
# Mirror-derived years are only scored when the annual mirror covered at
# least this share of UAE-reported value on the latest overlap year.
MIN_MIRROR_COVERAGE_PCT = 75.0


def mirror_extension(conn, imports: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Mirror-derived rows for years beyond each commodity's last UAE-reported
    year, restricted to commodities passing the coverage gate. Returns the
    extension frame (same columns as imports, plus source) and a dict of
    excluded commodities -> (gate year, coverage %) for honest reporting."""
    mirror = pd.read_sql(
        """SELECT f.year, f.commodity_id, c.name AS origin, f.trade_value_usd
           FROM fact_imports_mirror_annual f
           JOIN dim_country c ON c.country_code = f.origin_code
           WHERE f.trade_value_usd IS NOT NULL AND f.trade_value_usd > 0""",
        conn,
    )
    coverage = pd.read_sql(
        "SELECT commodity_id, year, coverage_pct FROM mirror_coverage_annual "
        "WHERE coverage_pct IS NOT NULL",
        conn,
    )
    if mirror.empty or coverage.empty:
        return pd.DataFrame(columns=[*imports.columns, "source"]), {}

    last_uae_year = imports.groupby("commodity_id")["year"].max()
    extension, excluded = [], {}
    for cid, cov in coverage.groupby("commodity_id"):
        gate = cov.loc[cov["year"].idxmax()]
        extra = mirror[
            (mirror["commodity_id"] == cid)
            & (mirror["year"] > last_uae_year.get(cid, -1))
        ]
        if extra.empty:
            continue
        if gate.coverage_pct < MIN_MIRROR_COVERAGE_PCT:
            excluded[cid] = (int(gate.year), float(gate.coverage_pct))
            continue
        extension.append(extra)
    if not extension:
        return pd.DataFrame(columns=[*imports.columns, "source"]), excluded
    extension = pd.concat(extension)
    extension["source"] = "mirror_derived"
    return extension, excluded


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

    imports["source"] = "uae_reported"
    extension, excluded = mirror_extension(conn, imports)
    imports = pd.concat([imports, extension], ignore_index=True)

    prices["year"] = prices["period"].str[:4].astype(int)
    prices = prices.sort_values("period")
    prices["ret"] = prices.groupby("commodity_id")["price"].pct_change()
    vol = prices.groupby(["commodity_id", "year"])["ret"].std().rename("volatility")

    conn.execute("DELETE FROM risk_scores")
    rows = 0
    for (year, cid, source), grp in imports.groupby(["year", "commodity_id", "source"]):
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
            " price_volatility, composite_risk, source) VALUES (?,?,?,?,?,?,?,?,?)",
            (int(year), cid, hhi, top_origin, top_share, n_origins, v, composite, source),
        )
        rows += 1

    conn.commit()
    latest = pd.read_sql(
        """SELECT commodity_id, year, source, round(hhi,3) AS hhi, top_origin,
                  round(top_origin_share*100,1) AS top_share_pct,
                  round(composite_risk,1) AS risk
           FROM risk_scores r
           WHERE year = (SELECT max(year) FROM risk_scores r2
                         WHERE r2.commodity_id = r.commodity_id)
           ORDER BY risk DESC""",
        conn,
    )
    conn.close()
    print(f"Computed {rows} commodity-year risk scores.\n")
    print("Latest available year per commodity, ranked by composite risk:")
    print(latest.to_string(index=False))
    for cid, (year, pct) in excluded.items():
        print(f"\nNOTE: {cid} not extended past its last UAE-reported year — "
              f"annual mirror covered only {pct:.0f}% of UAE-reported {year} value "
              f"(threshold {MIN_MIRROR_COVERAGE_PCT:.0f}%), so mirror-derived "
              f"concentration metrics would be misleading.")


if __name__ == "__main__":
    main()
