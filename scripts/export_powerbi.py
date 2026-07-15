"""Export tidy CSVs from qutview.db for the Power BI report.

Power BI's native SQLite support requires an ODBC driver install; CSV
import needs nothing. This script writes one CSV per table Power BI
needs into data/powerbi/, denormalised where it saves the report a join
(origin names are already attached to flow tables).

Run after the pipeline:  python scripts/export_powerbi.py
Re-run whenever the database is refreshed, then hit Refresh in Power BI.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from common import get_connection, PROJECT_ROOT

OUT_DIR = PROJECT_ROOT / "data" / "powerbi"

EXPORTS = {
    # name -> query
    "commodities": """
        SELECT commodity_id, name, hs_code, price_unit FROM dim_commodity
    """,
    "annual_imports": """
        SELECT f.year, f.commodity_id, d.name AS commodity, c.name AS origin,
               f.trade_value_usd, f.net_weight_kg
        FROM fact_imports f
        JOIN dim_country c ON c.country_code = f.origin_code
        JOIN dim_commodity d ON d.commodity_id = f.commodity_id
    """,
    "monthly_mirror_flows": """
        SELECT f.period, f.commodity_id, d.name AS commodity, c.name AS origin,
               f.trade_value_usd, f.net_weight_kg
        FROM fact_imports_monthly f
        JOIN dim_country c ON c.country_code = f.origin_code
        JOIN dim_commodity d ON d.commodity_id = f.commodity_id
    """,
    "prices": """
        SELECT p.period, p.commodity_id, d.name AS commodity, p.price, d.price_unit
        FROM fact_prices p JOIN dim_commodity d ON d.commodity_id = p.commodity_id
    """,
    "annual_mirror_flows": """
        SELECT f.year, f.commodity_id, d.name AS commodity, c.name AS origin,
               f.trade_value_usd, f.net_weight_kg
        FROM fact_imports_mirror_annual f
        JOIN dim_country c ON c.country_code = f.origin_code
        JOIN dim_commodity d ON d.commodity_id = f.commodity_id
    """,
    "annual_risk": """
        SELECT r.year, r.commodity_id, d.name AS commodity, r.hhi, r.top_origin,
               r.top_origin_share, r.n_origins, r.price_volatility, r.composite_risk,
               r.source
        FROM risk_scores r JOIN dim_commodity d ON d.commodity_id = r.commodity_id
    """,
    "monthly_risk": """
        SELECT m.period, m.commodity_id, d.name AS commodity, m.hhi, m.top_origin,
               m.top_origin_share, m.n_origins, m.price_volatility, m.composite_risk
        FROM risk_scores_monthly m
        JOIN dim_commodity d ON d.commodity_id = m.commodity_id
    """,
    "forecasts": """
        SELECT f.period, f.commodity_id, d.name AS commodity,
               f.forecast, f.lower_ci, f.upper_ci
        FROM forecasts f JOIN dim_commodity d ON d.commodity_id = f.commodity_id
    """,
    "backtest_metrics": """
        SELECT b.commodity_id, d.name AS commodity, b.horizon_months,
               b.mape_pct, b.evaluated_at
        FROM backtest_metrics b JOIN dim_commodity d ON d.commodity_id = b.commodity_id
    """,
    "dependency_ratios": """
        SELECT r.year, r.commodity_id, d.name AS commodity, r.import_kt,
               r.production_kt, r.dependency_pct
        FROM dependency_ratios r JOIN dim_commodity d ON d.commodity_id = r.commodity_id
    """,
    "mirror_coverage": """
        SELECT m.commodity_id, d.name AS commodity, m.basis_year, m.coverage_pct
        FROM mirror_coverage m JOIN dim_commodity d ON d.commodity_id = m.commodity_id
    """,
    "mirror_coverage_annual": """
        SELECT m.commodity_id, d.name AS commodity, m.year, m.coverage_pct
        FROM mirror_coverage_annual m
        JOIN dim_commodity d ON d.commodity_id = m.commodity_id
    """,
    "data_provenance": """
        SELECT source_name, kind, loaded_at FROM data_source
    """,
}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    for name, query in EXPORTS.items():
        df = pd.read_sql(query, conn)
        dest = OUT_DIR / f"{name}.csv"
        df.to_csv(dest, index=False)
        print(f"  {name}.csv: {len(df)} rows")
    conn.close()
    print(f"\nExported {len(EXPORTS)} tables to {OUT_DIR}")


if __name__ == "__main__":
    main()
