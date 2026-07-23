"""Ingest the lentils price from FRED series WPU0113012.

No clean global $/mt lentil price exists, but the US Bureau of Labor Statistics
publishes a monthly Producer Price Index for "Farm Products: Dry Peas and
Lentils" (FRED WPU0113012). Like the FAO dairy index it is an INDEX (2015=100),
not a $/mt price — fine for QUTVIEW, whose volatility / SARIMAX forecast / MAPE
/ A4 momentum are all returns-based and work on an index unchanged. It is
labelled "US PPI, Dry Peas & Lentils (index, 2015=100)" everywhere, never $/kg.

Scope nuance (disclosed in the UI): the price index covers peas AND lentils,
while the QUTVIEW corridor is lentils only (HS 0713.40) — lentils are a core
component, so it is a reasonable price proxy. Chickpeas are a separate corridor
this index does NOT cover; they are parked (no clean chickpea price source).

Free monthly CSV, no key, history from 2015. Manages only the 'lentils' rows in
fact_prices, so it never clobbers the Pink Sheet or other adapters. A browser
User-Agent is required — FRED 403s a plain request.

Run after worldbank_prices.py:  python src/ingest/fred_lentils_price.py
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

CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=WPU0113012"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
MAX_RETRIES = 4
STALE_MONTHS = 4


def fetch_lentils() -> list[tuple]:
    """Download the FRED CSV and return (period 'YYYY-MM', 'lentils', index)
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
    next(reader, None)  # header: observation_date, WPU0113012
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
            idx = float(value)
        except ValueError:
            continue
        rows.append((obs_date[:7], "lentils", idx))
    return rows


def store_rows(conn, rows) -> int:
    conn.execute("DELETE FROM fact_prices WHERE commodity_id = 'lentils'")
    conn.executemany(
        "INSERT OR REPLACE INTO fact_prices (period, commodity_id, price) VALUES (?,?,?)",
        rows,
    )
    return len(rows)


def main() -> None:
    conn = get_connection()
    print("Fetching lentils price (FRED WPU0113012, US PPI Dry Peas & Lentils, index)...")
    rows = fetch_lentils()
    if not rows:
        existing = conn.execute(
            "SELECT count(*) FROM fact_prices WHERE commodity_id = 'lentils'").fetchone()[0]
        print(f"Could not fetch FRED lentils — keeping {existing} existing lentils rows.")
        conn.close()
        sys.exit(1 if existing == 0 else 0)

    inserted = store_rows(conn, rows)
    newest = max(p for p, _, _ in rows)
    latest = dict((p, v) for p, _, v in rows)[newest]
    print(f"Loaded {inserted} lentils index points (latest month: {newest}, "
          f"{latest:.1f}; US PPI, 2015=100).")
    today = date.today()
    lag = (today.year - int(newest[:4])) * 12 + today.month - int(newest[5:7])
    if lag > STALE_MONTHS:
        print(f"WARNING: latest lentils month is {lag} months old — check {CSV_URL}.")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
