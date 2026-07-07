"""Ingest monthly UAE-bound flows from mirror statistics.

The UAE stopped publishing monthly customs data to Comtrade after 2019,
so recent monthly corridor flows are reconstructed from the other side of
each trade: origin countries' own reported exports to the UAE (flowCode X,
partnerCode 784). Values are FOB as reported by the origin — close to, but
not identical to, UAE customs figures. The overlap year is cross-checked
against UAE-reported annual imports and stored in mirror_coverage so the
dashboard can show how complete each corridor's mirror picture is.

Origins are selected data-driven: for each commodity, the origins covering
>= 90% of UAE-reported import value in the latest annual year (capped at 8).
Requires COMTRADE_API_KEY. No sample fallback — this dataset is additive,
and the dashboard treats an empty table as "monitor unavailable".

Run after comtrade_imports.py:  python src/ingest/comtrade_mirror_monthly.py
"""
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import (
    COMMODITIES, UAE_REPORTER_CODE,
    get_comtrade_key, get_connection, record_data_source,
)

MONTHLY_URL = "https://comtradeapi.un.org/data/v1/get/C/M/HS"
START_YEAR = 2022          # 12 months of history before the first rolling score
MAX_ORIGINS = 8
COVERAGE_TARGET = 0.90     # take origins until this share of annual value is covered
MAX_RETRIES = 4


def pick_origins(conn) -> tuple[int, dict[str, list[int]]]:
    """From UAE-reported annual imports, pick each commodity's top origins
    covering COVERAGE_TARGET of the latest year's value. Returns the basis
    year and {commodity_id: [origin M49 codes]}."""
    basis_year = conn.execute("SELECT max(year) FROM fact_imports").fetchone()[0]
    if basis_year is None:
        sys.exit("fact_imports is empty — run comtrade_imports.py first.")
    origins = {}
    for cid in COMMODITIES:
        rows = conn.execute(
            """SELECT origin_code, trade_value_usd FROM fact_imports
               WHERE year = ? AND commodity_id = ? AND trade_value_usd > 0
               ORDER BY trade_value_usd DESC""",
            (basis_year, cid),
        ).fetchall()
        total = sum(v for _, v in rows)
        picked, cum = [], 0.0
        for code, value in rows:
            if cum >= COVERAGE_TARGET * total or len(picked) >= MAX_ORIGINS:
                break
            picked.append(code)
            cum += value
        origins[cid] = picked
    return basis_year, origins


def fetch_one(params, headers):
    delay = 2
    for attempt in range(1, MAX_RETRIES + 1):
        r = requests.get(MONTHLY_URL, params=params, headers=headers, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == MAX_RETRIES:
                r.raise_for_status()
            time.sleep(delay)
            delay *= 2
            continue
        r.raise_for_status()
        return r.json().get("data", [])
    return []


def fetch_mirror(origins: dict[str, list[int]], last_year: int) -> list[tuple]:
    """One request per commodity-year: all origin reporters, 12 monthly
    periods, partner2Code=0 to exclude the duplicate 'Areas, nes' rows the
    API returns alongside the World aggregate."""
    key = get_comtrade_key()
    if not key:
        sys.exit("COMTRADE_API_KEY is required for monthly mirror data — see .env.example.")
    headers = {"Ocp-Apim-Subscription-Key": key}
    rows = []
    for cid, meta in COMMODITIES.items():
        reporter_list = ",".join(str(c) for c in origins[cid])
        if not reporter_list:
            print(f"  {cid}: no origins picked — skipping")
            continue
        for year in range(START_YEAR, last_year + 1):
            params = {
                "reporterCode": reporter_list,
                "partnerCode": UAE_REPORTER_CODE,
                # Comtrade returns the same flow under several breakdown
                # aggregates; without these filters each month shows up
                # multiple times (partner2 'Areas, nes', motCode 'Other').
                "partner2Code": 0,
                "motCode": 0,
                "customsCode": "C00",
                "period": ",".join(f"{year}{m:02d}" for m in range(1, 13)),
                "cmdCode": meta["hs"],
                "flowCode": "X",
                "maxRecords": 500,
                "includeDesc": "true",
            }
            try:
                records = fetch_one(params, headers)
            except Exception as exc:
                print(f"  {cid} {year}: request failed after retries ({exc}) — aborting")
                raise
            seen = set()
            for rec in records:
                period = str(rec.get("period", ""))
                reporter = rec.get("reporterCode")
                if len(period) != 6 or not reporter:
                    continue
                dedup_key = (period, reporter)
                if dedup_key in seen:
                    raise ValueError(
                        f"duplicate mirror record for {cid} reporter={reporter} "
                        f"period={period} despite breakdown filters — refusing to "
                        f"store possibly double-counted data"
                    )
                seen.add(dedup_key)
                rows.append((
                    f"{period[:4]}-{period[4:]}", cid, reporter,
                    rec.get("reporterDesc") or str(reporter),
                    rec.get("primaryValue"), rec.get("netWgt"),
                ))
            print(f"  {cid} {year}: {len(records)} records")
            time.sleep(1)
    return rows


def store_rows(conn, rows) -> int:
    conn.execute("DELETE FROM fact_imports_monthly")
    for period, cid, origin, name, value, weight in rows:
        conn.execute(
            "INSERT OR IGNORE INTO dim_country (country_code, name) VALUES (?,?)",
            (origin, name),
        )
        conn.execute(
            "INSERT OR REPLACE INTO fact_imports_monthly "
            "(period, commodity_id, origin_code, trade_value_usd, net_weight_kg) "
            "VALUES (?,?,?,?,?)",
            (period, cid, origin, value, weight),
        )
    return len(rows)


def store_coverage(conn, basis_year: int) -> None:
    """Mirror total vs UAE-reported total on the basis year, per commodity.
    Under 100% means the picked origins miss some flows (or an origin does
    not publish, e.g. Russia after 2022); over 100% can happen because FOB
    mirror values and CIF customs values differ."""
    conn.execute("DELETE FROM mirror_coverage")
    for cid in COMMODITIES:
        mirror = conn.execute(
            """SELECT coalesce(sum(trade_value_usd), 0) FROM fact_imports_monthly
               WHERE commodity_id = ? AND substr(period, 1, 4) = ?""",
            (cid, str(basis_year)),
        ).fetchone()[0]
        reported = conn.execute(
            """SELECT coalesce(sum(trade_value_usd), 0) FROM fact_imports
               WHERE commodity_id = ? AND year = ?""",
            (cid, basis_year),
        ).fetchone()[0]
        pct = 100 * mirror / reported if reported else None
        conn.execute(
            "INSERT OR REPLACE INTO mirror_coverage (commodity_id, basis_year, coverage_pct) "
            "VALUES (?,?,?)",
            (cid, basis_year, pct),
        )
        label = f"{pct:.0f}%" if pct is not None else "n/a"
        print(f"  {cid}: mirror covers {label} of UAE-reported {basis_year} imports")


def main() -> None:
    conn = get_connection()
    basis_year, origins = pick_origins(conn)
    named = {
        cid: [conn.execute("SELECT name FROM dim_country WHERE country_code = ?", (c,)).fetchone()[0]
              for c in codes]
        for cid, codes in origins.items()
    }
    print(f"Mirror origins (from {basis_year} annual imports):")
    for cid, names in named.items():
        print(f"  {cid}: {', '.join(names)}")

    from datetime import date
    print("Fetching monthly mirror flows from UN Comtrade...")
    try:
        rows = fetch_mirror(origins, date.today().year)
    except Exception as exc:
        print(f"Mirror fetch aborted ({exc}) — existing monthly data left untouched.")
        conn.close()
        sys.exit(1)

    if not rows:
        print("No mirror records returned — existing monthly data left untouched.")
        conn.close()
        sys.exit(1)

    inserted = store_rows(conn, rows)
    store_coverage(conn, basis_year)
    record_data_source(conn, "imports_monthly", "live")
    print(f"Loaded {inserted} monthly mirror rows "
          f"(latest period: {conn.execute('SELECT max(period) FROM fact_imports_monthly').fetchone()[0]}).")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
