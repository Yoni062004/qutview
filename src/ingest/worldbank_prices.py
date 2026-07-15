"""Ingest monthly global commodity prices from the World Bank "Pink Sheet".

Downloads the CMO-Historical-Data-Monthly.xlsx file and extracts the five
QUTVIEW commodity series. The download URL carries a doc id that rotates
with each monthly release, so the current link is discovered live from the
commodity-markets page first, with known past URLs as backup. A manually
downloaded local copy is the last resort only — an old local file must
never shadow a fresher release (that silently froze prices at 2025-12
once). On total failure it keeps any existing data untouched, falling back
to sample data only when the table is empty so a fresh clone still demos
end-to-end.

Run:  python src/ingest/worldbank_prices.py            (live, with fallback)
      python src/ingest/worldbank_prices.py --sample   (force sample data)

Manual fallback: download "Monthly prices" from
https://www.worldbank.org/en/research/commodity-markets and save it as
data/raw/CMO-Historical-Data-Monthly.xlsx, then rerun this script.
"""
import io
import math
import random
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import COMMODITIES, DATA_DIR, YEARS, get_connection, record_data_source

PAGE_URL = "https://www.worldbank.org/en/research/commodity-markets"
CANDIDATE_URLS = [
    # Known past releases, used only if discovery from PAGE_URL fails.
    "https://thedocs.worldbank.org/en/doc/18675f1d1639c7a34d463f59263ba0a2-0050012025/related/CMO-Historical-Data-Monthly.xlsx",
    "https://thedocs.worldbank.org/en/doc/5d903e848db1d1b83e0ec8f744e55570-0350012021/related/CMO-Historical-Data-Monthly.xlsx",
]
LOCAL_COPY = DATA_DIR / "raw" / "CMO-Historical-Data-Monthly.xlsx"
STALE_MONTHS = 4  # warn when the freshest loaded month lags today by more


def discover_urls() -> list[str]:
    """Scrape the commodity-markets page for the current monthly-data link,
    so the ingest keeps working as the World Bank rotates release doc ids."""
    try:
        r = requests.get(PAGE_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        found = re.findall(
            r"https://thedocs\.worldbank\.org/[^\"'\s]+CMO-Historical-Data-Monthly\.xlsx",
            r.text,
        )
        return list(dict.fromkeys(found))  # de-dupe, keep order
    except Exception as exc:
        print(f"Could not discover current Pink Sheet URL ({exc}) — trying known URLs.")
        return []

# Rough current price levels used only for sample mode / fallback.
SAMPLE_BASE_PRICE = {"wheat": 270.0, "rice": 550.0, "palm_oil": 950.0, "sugar": 0.45, "poultry": 1.60}


def _norm(label) -> str:
    """Normalise a Pink Sheet column label: drop footnote asterisks,
    collapse case and stray whitespace ('Rice, Thai 5%  ' == 'rice, thai 5%')."""
    return str(label).replace("**", "").replace("*", "").strip().lower()


def parse_pink_sheet(content: bytes) -> list[tuple]:
    """The Pink Sheet 'Monthly Prices' sheet has a multi-row header: commodity
    names in one row, units below it, then data rows keyed like '2024M03'.
    Locate the name row by searching for 'Wheat, US HRW', then map each of our
    commodities to its column index. Returns (period, commodity_id, price)
    tuples — no database writes here, so a bad file can't corrupt anything."""
    raw = pd.read_excel(io.BytesIO(content), sheet_name="Monthly Prices", header=None)

    targets = {_norm(meta["wb_series"]): cid for cid, meta in COMMODITIES.items()}
    header_row, col_for = None, {}
    for i in range(min(12, len(raw))):
        labels = {j: _norm(v) for j, v in raw.iloc[i].items()}
        if "wheat, us hrw" in labels.values():
            header_row = i
            col_for = {j: targets[lbl] for j, lbl in labels.items() if lbl in targets}
            break
    if header_row is None:
        raise ValueError("Could not locate the commodity-name header row in Pink Sheet")
    missing = set(COMMODITIES) - set(col_for.values())
    if missing:
        raise ValueError(f"Pink Sheet columns not found for: {sorted(missing)}")

    rows = []
    for _, row in raw.iloc[header_row + 1:].iterrows():
        period_raw = str(row.iloc[0]).strip()  # format like '2024M03'
        if len(period_raw) != 7 or "M" not in period_raw:
            continue
        year, month = period_raw.split("M")
        if not year.isdigit() or int(year) < YEARS[0]:
            continue
        for col, cid in col_for.items():
            try:
                price = float(row.iloc[col])
            except (TypeError, ValueError):
                continue
            if math.isnan(price):
                continue
            rows.append((f"{year}-{month}", cid, price))
    return rows


def fetch_live() -> list[tuple]:
    for url in discover_urls() + CANDIDATE_URLS:
        try:
            print(f"Trying {url[:80]}...")
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            return parse_pink_sheet(r.content)
        except Exception as exc:
            print(f"  failed: {exc}")
    if LOCAL_COPY.exists():
        print(f"All URLs failed — using local copy: {LOCAL_COPY}")
        return parse_pink_sheet(LOCAL_COPY.read_bytes())
    return []


def store_rows(conn, rows) -> int:
    conn.execute("DELETE FROM fact_prices")
    conn.executemany(
        "INSERT OR REPLACE INTO fact_prices (period, commodity_id, price) VALUES (?,?,?)",
        rows,
    )
    return len(rows)


def load_sample(conn) -> int:
    """Synthetic monthly prices: trend + annual seasonality + noise, plus a
    2022 supply-shock bump so charts and volatility metrics look realistic."""
    rng = random.Random(7)
    inserted = 0
    for cid, base in SAMPLE_BASE_PRICE.items():
        price = base * 0.8
        for year in YEARS:
            for month in range(1, 13):
                if year == 2025 and month > 12:
                    break
                t = (year - YEARS[0]) * 12 + month
                seasonal = 1 + 0.04 * math.sin(2 * math.pi * month / 12)
                shock = 1.35 if (year == 2022 and 3 <= month <= 9) else 1.0
                drift = 1 + 0.003 * t
                price = base * 0.8 * drift * seasonal * shock * rng.uniform(0.97, 1.03)
                conn.execute(
                    "INSERT OR REPLACE INTO fact_prices (period, commodity_id, price) VALUES (?,?,?)",
                    (f"{year}-{month:02d}", cid, round(price, 4)),
                )
                inserted += 1
    return inserted


def main() -> None:
    force_sample = "--sample" in sys.argv
    conn = get_connection()

    if force_sample:
        conn.execute("DELETE FROM fact_prices")
        inserted = load_sample(conn)
        record_data_source(conn, "prices", "sample")
        print(f"Loaded {inserted} sample price points.")
        conn.commit()
        conn.close()
        return

    try:
        rows = fetch_live()
    except Exception as exc:
        print(f"Pink Sheet parse failed ({exc}).")
        rows = []

    if rows:
        inserted = store_rows(conn, rows)
        record_data_source(conn, "prices", "live")
        newest = max(period for period, _, _ in rows)
        print(f"Loaded {inserted} live price points from the World Bank Pink Sheet "
              f"(latest month: {newest}).")
        today = date.today()
        lag = (today.year - int(newest[:4])) * 12 + today.month - int(newest[5:7])
        if lag > STALE_MONTHS:
            print(f"WARNING: latest price month is {lag} months old — the source "
                  f"may be a stale release; check {PAGE_URL}.")
    else:
        # Never replace existing data with synthetic data: fall back to
        # sample prices only when the table is empty (fresh clone / demo).
        existing = conn.execute("SELECT count(*) FROM fact_prices").fetchone()[0]
        if existing:
            print(f"Could not fetch Pink Sheet — keeping {existing} existing price points untouched.")
            conn.close()
            sys.exit(1)
        print("Could not fetch Pink Sheet — falling back to sample prices.")
        inserted = load_sample(conn)
        record_data_source(conn, "prices", "sample")
        print(f"Loaded {inserted} sample price points.")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
