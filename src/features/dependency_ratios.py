"""Compute import-dependency ratios per commodity-year.

dependency = imports / (imports + domestic production), by weight.
100% means every tonne consumed arrives on a ship; lower values mean
domestic production cushions corridor shocks. This contextualises the
corridor risk score: concentrated origins matter far more when there is
no domestic fallback.

Known approximations (disclosed in the dashboard): re-exports are not
netted out (the UAE re-exports significant volumes, especially rice and
sugar), and HS import headings do not map 1:1 to FAO production items
(frozen-only beef imports vs all cattle meat; chicken HS 0207 includes
offal). Years past FAOSTAT's latest release carry production forward
from the last known year rather than pretending it dropped to zero.

Run after faostat_production.py:  python src/features/dependency_ratios.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import get_connection


def main() -> None:
    conn = get_connection()
    imports = pd.read_sql(
        """SELECT year, commodity_id, sum(net_weight_kg) / 1e6 AS import_kt
           FROM fact_imports
           WHERE net_weight_kg IS NOT NULL AND net_weight_kg > 0
           GROUP BY year, commodity_id""",
        conn,
    )
    production = pd.read_sql(
        "SELECT year, commodity_id, production_tonnes / 1e3 AS production_kt "
        "FROM fact_production",
        conn,
    )
    if imports.empty:
        sys.exit("fact_imports is empty — run comtrade_imports.py first.")
    if production.empty:
        sys.exit("fact_production is empty — run faostat_production.py first.")

    # Carry each commodity's last known production forward so import years
    # newer than the FAOSTAT release still get a ratio.
    last_prod_year = int(production.loc[production["production_kt"] > 0, "year"].max())

    def production_for(cid: str, year: int) -> float:
        exact = production[(production["commodity_id"] == cid) & (production["year"] == year)]
        if not exact.empty:
            return float(exact["production_kt"].iloc[0])
        carry = production[(production["commodity_id"] == cid)
                           & (production["year"] == min(year, last_prod_year))]
        return float(carry["production_kt"].iloc[0]) if not carry.empty else 0.0

    conn.execute("DELETE FROM dependency_ratios")
    rows = 0
    for _, row in imports.iterrows():
        year, cid, imp_kt = int(row["year"]), row["commodity_id"], float(row["import_kt"])
        prod_kt = production_for(cid, year)
        dep = 100 * imp_kt / (imp_kt + prod_kt) if (imp_kt + prod_kt) > 0 else None
        conn.execute(
            "INSERT OR REPLACE INTO dependency_ratios "
            "(year, commodity_id, import_kt, production_kt, dependency_pct) "
            "VALUES (?,?,?,?,?)",
            (year, cid, imp_kt, prod_kt, dep),
        )
        rows += 1

    conn.commit()
    latest = pd.read_sql(
        """SELECT commodity_id, year, round(import_kt) AS import_kt,
                  round(production_kt) AS production_kt,
                  round(dependency_pct, 1) AS dependency_pct
           FROM dependency_ratios
           WHERE year = (SELECT max(year) FROM dependency_ratios)
           ORDER BY dependency_pct DESC""",
        conn,
    )
    conn.close()
    print(f"Computed {rows} dependency ratios.\n")
    print("Latest year:")
    print(latest.to_string(index=False))


if __name__ == "__main__":
    main()
