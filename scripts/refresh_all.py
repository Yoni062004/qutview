"""Run the full QUTVIEW pipeline end to end and export the Power BI CSVs.

One command keeps everything current:

    python scripts/refresh_all.py

Order: schema -> ingests (Comtrade annual, World Bank prices, Comtrade
mirror monthly, Comtrade mirror annual, FAOSTAT production) -> risk
indicators (annual, monthly, dependency) -> forecasts -> Power BI CSV
export.

Ingest steps are allowed to fail (an API being down just means existing
data is kept — the ingest scripts never overwrite live data with sample
data). Computation and export steps must succeed; the script stops and
reports if one fails.

After this finishes, the Streamlit dashboard is current immediately, and
the Power BI report (docs/QUTVIEW.pbix) picks up the new numbers on the
next Home -> Refresh (Power BI Desktop cannot be refreshed unattended).
"""
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# (relative script path, is the step allowed to fail?)
STEPS = [
    ("src/db/init_db.py", False),
    ("src/ingest/comtrade_imports.py", True),
    ("src/ingest/worldbank_prices.py", True),
    ("src/ingest/fao_dairy_price.py", True),
    ("src/ingest/fred_barley_price.py", True),
    ("src/ingest/comtrade_mirror_monthly.py", True),
    ("src/ingest/comtrade_mirror_annual.py", True),
    ("src/ingest/faostat_production.py", True),
    ("src/features/risk_indicators.py", False),
    ("src/features/risk_indicators_monthly.py", False),
    ("src/features/dependency_ratios.py", False),
    ("src/models/forecast.py", False),
    ("scripts/export_powerbi.py", False),
]


def main() -> None:
    started = time.time()
    warnings = []
    for script, may_fail in STEPS:
        print(f"\n=== {script} ===", flush=True)
        result = subprocess.run([sys.executable, str(PROJECT_ROOT / script)],
                                cwd=PROJECT_ROOT)
        if result.returncode != 0:
            if may_fail:
                warnings.append(script)
                print(f"--- {script} failed; existing data kept, continuing.")
            else:
                sys.exit(f"\nABORTED: {script} failed (exit {result.returncode}).")

    minutes = (time.time() - started) / 60
    print(f"\nDone in {minutes:.1f} min.")
    if warnings:
        print("Ingest steps that kept existing data instead of refreshing:")
        for w in warnings:
            print(f"  - {w}")
    print("Streamlit is current now; open docs/QUTVIEW.pbix and hit "
          "Home -> Refresh to update the Power BI report.")


if __name__ == "__main__":
    main()
