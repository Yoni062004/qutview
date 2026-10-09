"""Run the full QUTVIEW pipeline end to end and export the Power BI CSVs.

One command keeps everything current:

    python scripts/refresh_all.py

Order: schema -> ingests (Comtrade annual, World Bank prices, FAO dairy,
FRED barley and lentils, Comtrade mirror monthly, Comtrade mirror annual,
FAOSTAT production) -> risk indicators (annual, monthly, dependency) ->
forecasts -> Power BI CSV export.

Fail loudly: an ingest step that cannot refresh its source keeps the
existing data untouched and exits non-zero. This script then carries on
with the remaining steps (so everything that CAN refresh does), lists every
stale step in the summary, and exits non-zero itself — a run that left any
source stale never reports success. Computation and export steps must
succeed; the script stops immediately if one fails.

Sample data: comtrade_imports.py never writes sample data on the live path
(only via its explicit --sample flag). worldbank_prices.py still seeds
sample prices, but only into an EMPTY fact_prices table (fresh clone), and
records the source as 'sample' so the dashboard badge shows it.

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
    ("src/ingest/fred_lentils_price.py", True),
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
        print(f"\nSTALE DATA — {len(warnings)} ingest step(s) kept existing data "
              f"instead of refreshing:")
        for w in warnings:
            print(f"  - {w}")
        sys.exit(f"\nREFRESH INCOMPLETE: {len(warnings)} source(s) not refreshed "
                 f"(see above). Downstream scores were recomputed on partly stale data.")
    print("Streamlit is current now; open docs/QUTVIEW.pbix and hit "
          "Home -> Refresh to update the Power BI report.")


if __name__ == "__main__":
    main()
