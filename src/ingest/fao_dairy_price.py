"""Ingest the FAO Dairy Price Index as the dairy price series.

The World Bank Pink Sheet has no dairy price, so dairy (milk powder, HS 0402)
is priced from the FAO Dairy Price Index — the dairy sub-index of the FAO Food
Price Index. It is an INDEX (2014-2016 = 100), not a $/mt price. That is fine
for QUTVIEW: volatility, the SARIMAX forecast, its backtest MAPE, and the A4
price momentum are all returns-based and work on an index unchanged. Only an
absolute $/unit display is unavailable — the unit is labelled
"index (2014-16=100)" everywhere a price shows, never $/kg.

Free monthly CSV, no key, history from 1990. This adapter only manages the
'dairy' rows in fact_prices (it never touches the Pink Sheet commodities), so
it can run after worldbank_prices.py without either clobbering the other.

Run after worldbank_prices.py:  python src/ingest/fao_dairy_price.py
"""
import io
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import YEARS, get_connection, record_data_source

# The FAO Food Price Index monthly CSV (dairy is one of five sub-index columns).
# The doc path is stable; if FAO rotates it, the discovery fallback re-finds it.
PRIMARY_URL = ("https://www.fao.org/media/docs/worldfoodsituationlibraries/"
               "default-document-library/food_price_indices_data.csv?download=true")
PAGE_URL = "https://www.fao.org/worldfoodsituation/foodpricesindex/en/"
MAX_RETRIES = 4
STALE_MONTHS = 4


def _discover_url() -> list[str]:
    """Scrape the FAO page for the current monthly-CSV link, so the ingest
    survives FAO rotating the document path."""
    import re
    try:
        r = requests.get(PAGE_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        found = re.findall(r"https://[^\"'\s]+food_price_indices_data[^\"'\s]*\.csv[^\"'\s]*",
                           r.text)
        return list(dict.fromkeys(found))
    except Exception as exc:
        print(f"Could not discover FAO CSV URL ({exc}) — using known URL.")
        return []


def fetch_dairy() -> list[tuple]:
    """Download the FAO CSV and return (period 'YYYY-MM', 'dairy', index) rows
    from YEARS[0] onward. No DB writes here."""
    content = None
    for url in [PRIMARY_URL] + _discover_url():
        delay = 2
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0"})
                r.raise_for_status()
                content = r.content
                break
            except Exception as exc:
                if attempt == MAX_RETRIES:
                    print(f"  {url[:60]}...: failed ({exc})")
                else:
                    import time
                    time.sleep(delay)
                    delay *= 2
        if content:
            break
    if not content:
        return []

    raw = pd.read_csv(io.BytesIO(content), header=None, dtype=str)
    header_row = None
    for i in range(min(8, len(raw))):
        if any("dairy" == str(v).strip().lower() for v in raw.iloc[i].tolist()):
            header_row = i
            break
    if header_row is None:
        raise ValueError("Could not locate the 'Dairy' column in the FAO CSV")
    df = pd.read_csv(io.BytesIO(content), header=header_row)
    df.columns = [str(c).strip() for c in df.columns]
    date_col = df.columns[0]
    dairy_col = next(c for c in df.columns if c.lower() == "dairy")

    rows = []
    for _, r in df.iterrows():
        period = str(r[date_col]).strip()  # 'YYYY-MM'
        if len(period) != 7 or period[4] != "-":
            continue
        if not period[:4].isdigit() or int(period[:4]) < YEARS[0]:
            continue
        try:
            value = float(r[dairy_col])
        except (TypeError, ValueError):
            continue
        if value != value:  # NaN
            continue
        rows.append((period, "dairy", value))
    return rows


def store_rows(conn, rows) -> int:
    conn.execute("DELETE FROM fact_prices WHERE commodity_id = 'dairy'")
    conn.executemany(
        "INSERT OR REPLACE INTO fact_prices (period, commodity_id, price) VALUES (?,?,?)",
        rows,
    )
    return len(rows)


def main() -> None:
    conn = get_connection()
    print("Fetching FAO Dairy Price Index...")
    try:
        rows = fetch_dairy()
    except Exception as exc:
        print(f"FAO dairy parse failed ({exc}) — existing dairy prices left untouched.")
        conn.close()
        sys.exit(1)

    if not rows:
        existing = conn.execute(
            "SELECT count(*) FROM fact_prices WHERE commodity_id = 'dairy'").fetchone()[0]
        print(f"Could not fetch FAO dairy index — keeping {existing} existing dairy rows.")
        conn.close()
        sys.exit(1 if existing == 0 else 0)

    inserted = store_rows(conn, rows)
    newest = max(p for p, _, _ in rows)
    print(f"Loaded {inserted} FAO Dairy Price Index points (latest month: {newest}, "
          f"value {dict((p, v) for p, _, v in rows)[newest]:.1f}; index 2014-16=100).")
    today = date.today()
    lag = (today.year - int(newest[:4])) * 12 + today.month - int(newest[5:7])
    if lag > STALE_MONTHS:
        print(f"WARNING: latest dairy month is {lag} months old — check {PAGE_URL}.")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
