"""Ingest the barley price from FRED series PBARLUSDM.

The World Bank Pink Sheet discontinued its barley series in Aug 2020, but the
IMF Primary Commodity Price System still publishes barley — U.S. No. 2 feed
barley (Minneapolis delivery spot, USDA) — hosted on FRED as PBARLUSDM. Unlike
the FAO dairy index, this is a real USD/mt PRICE (same unit family as the Pink
Sheet series), so no FX conversion and no index labelling are needed.

Free monthly CSV, no key, history from 1992. This adapter only manages the
'barley' rows in fact_prices (never the Pink Sheet commodities), so it can run
after worldbank_prices.py without either clobbering the other. A browser
User-Agent is required — FRED 403s a plain request.

Run after worldbank_prices.py:  python src/ingest/fred_barley_price.py
"""
import csv
import io
import sys
import time
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import YEARS, get_connection

CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=PBARLUSDM"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
MAX_RETRIES = 4
STALE_MONTHS = 4


def fetch_barley() -> list[tuple]:
    """Download the FRED CSV and return (period 'YYYY-MM', 'barley', price USD/mt)
    rows from YEARS[0] onward. No DB writes here."""
    content = None
    delay = 2
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(CSV_URL, headers=UA, timeout=60)
            r.raise_for_status()
            content = r.text
            break
        except Exception as exc:
            if attempt == MAX_RETRIES:
                print(f"  FRED fetch failed after retries ({exc})")
                return []
            time.sleep(delay)
            delay *= 2
    if not content:
        return []

    rows = []
    reader = csv.reader(io.StringIO(content))
    header = next(reader, None)  # observation_date, PBARLUSDM
    for rec in reader:
        if len(rec) < 2:
            continue
        obs_date, value = rec[0].strip(), rec[1].strip()
        if len(obs_date) < 7 or obs_date[4] != "-":
            continue
        if not obs_date[:4].isdigit() or int(obs_date[:4]) < YEARS[0]:
            continue
        if value in (".", "", "NA"):
            continue
        try:
            price = float(value)
        except ValueError:
            continue
        rows.append((obs_date[:7], "barley", price))
    return rows


def store_rows(conn, rows) -> int:
    conn.execute("DELETE FROM fact_prices WHERE commodity_id = 'barley'")
    conn.executemany(
        "INSERT OR REPLACE INTO fact_prices (period, commodity_id, price) VALUES (?,?,?)",
        rows,
    )
    return len(rows)


def main() -> None:
    conn = get_connection()
    print("Fetching barley price (FRED PBARLUSDM, USD/mt)...")
    rows = fetch_barley()
    if not rows:
        existing = conn.execute(
            "SELECT count(*) FROM fact_prices WHERE commodity_id = 'barley'").fetchone()[0]
        print(f"Could not fetch FRED barley — keeping {existing} existing barley rows.")
        conn.close()
        sys.exit(1 if existing == 0 else 0)

    inserted = store_rows(conn, rows)
    newest = max(p for p, _, _ in rows)
    latest_price = dict((p, v) for p, _, v in rows)[newest]
    print(f"Loaded {inserted} barley price points (latest month: {newest}, "
          f"{latest_price:.2f} USD/mt).")
    today = date.today()
    lag = (today.year - int(newest[:4])) * 12 + today.month - int(newest[5:7])
    if lag > STALE_MONTHS:
        print(f"WARNING: latest barley month is {lag} months old — the FRED "
              f"series may have paused; check {CSV_URL}.")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
