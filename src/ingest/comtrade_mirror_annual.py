"""Ingest annual UAE-bound flows from mirror statistics.

The UAE publishes annual customs data to Comtrade ~18 months late (as of
mid-2026 nothing after 2023 is out), so the most recent annual corridor
picture is reconstructed from the other side of each trade: every country's
own reported exports to the UAE (flowCode X, partnerCode 784). Unlike the
monthly mirror, no origin pre-selection is applied — all reporters are
fetched, so origin shifts after the last UAE-published year are captured.
Values are FOB as reported by the origin, not UAE customs figures; rows are
stored in fact_imports_mirror_annual, never mixed into fact_imports.

The full 2018+ range is fetched, not just the missing years, so every
overlap year is cross-checked against UAE-reported imports and stored in
mirror_coverage_annual. Downstream consumers use that coverage to decide
whether a commodity's mirror picture is complete enough to score (a major
origin that stopped publishing, e.g. Russia after 2021, makes mirror-derived
concentration metrics misleading, not just imprecise).

Requires COMTRADE_API_KEY. No sample fallback — this dataset is additive,
and an empty table simply means the annual series ends at the UAE-reported
years.

Run after comtrade_imports.py:  python src/ingest/comtrade_mirror_annual.py
"""
import sys
import time
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import (
    COMMODITIES, UAE_REPORTER_CODE, YEARS,
    get_comtrade_key, get_connection, record_data_source,
)

ANNUAL_URL = "https://comtradeapi.un.org/data/v1/get/C/A/HS"
MAX_RETRIES = 4
# Comfortably above the ~50 reporters/commodity-year observed; if a response
# ever hits this cap it is truncated and we refuse to store it.
MAX_RECORDS = 5000


def fetch_one(params, headers):
    delay = 2
    for attempt in range(1, MAX_RETRIES + 1):
        r = requests.get(ANNUAL_URL, params=params, headers=headers, timeout=90)
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == MAX_RETRIES:
                r.raise_for_status()
            time.sleep(delay)
            delay *= 2
            continue
        r.raise_for_status()
        return r.json().get("data", [])
    return []


def fetch_mirror() -> list[tuple]:
    """One request per commodity covering every year from YEARS[0] through the
    last complete calendar year (annual data for the running year cannot exist
    yet). No reporterCode filter: every country reporting exports to the UAE.
    Same breakdown filters as the monthly mirror (partner2Code/motCode/
    customsCode), without which each flow appears several times."""
    key = get_comtrade_key()
    if not key:
        sys.exit("COMTRADE_API_KEY is required for annual mirror data — see .env.example.")
    headers = {"Ocp-Apim-Subscription-Key": key}
    years = list(range(YEARS[0], date.today().year))
    rows = []
    for cid, meta in COMMODITIES.items():
        params = {
            "partnerCode": UAE_REPORTER_CODE,
            "partner2Code": 0,
            "motCode": 0,
            "customsCode": "C00",
            "period": ",".join(str(y) for y in years),
            "cmdCode": meta["hs"],
            "flowCode": "X",
            "maxRecords": MAX_RECORDS,
            "includeDesc": "true",
        }
        try:
            records = fetch_one(params, headers)
        except Exception as exc:
            print(f"  {cid}: request failed after retries ({exc}) — aborting")
            raise
        if len(records) >= MAX_RECORDS:
            raise ValueError(
                f"{cid}: response hit the {MAX_RECORDS}-record cap — refusing to "
                f"store a truncated mirror picture"
            )
        seen = set()
        per_year = {}
        for rec in records:
            period = str(rec.get("period", ""))
            reporter = rec.get("reporterCode")
            if len(period) != 4 or not reporter:
                continue
            dedup_key = (period, reporter)
            if dedup_key in seen:
                raise ValueError(
                    f"duplicate mirror record for {cid} reporter={reporter} "
                    f"period={period} despite breakdown filters — refusing to "
                    f"store possibly double-counted data"
                )
            seen.add(dedup_key)
            per_year[period] = per_year.get(period, 0) + 1
            rows.append((
                int(period), cid, reporter,
                rec.get("reporterDesc") or str(reporter),
                rec.get("primaryValue"), rec.get("netWgt"),
            ))
        summary = ", ".join(f"{y}: {per_year.get(str(y), 0)}" for y in years)
        print(f"  {cid} (origins per year) — {summary}")
        time.sleep(1)
    return rows


def store_rows(conn, rows) -> int:
    conn.execute("DELETE FROM fact_imports_mirror_annual")
    for year, cid, origin, name, value, weight in rows:
        conn.execute(
            "INSERT OR IGNORE INTO dim_country (country_code, name) VALUES (?,?)",
            (origin, name),
        )
        conn.execute(
            "INSERT OR REPLACE INTO fact_imports_mirror_annual "
            "(year, commodity_id, origin_code, trade_value_usd, net_weight_kg) "
            "VALUES (?,?,?,?,?)",
            (year, cid, origin, value, weight),
        )
    return len(rows)


def store_coverage(conn) -> None:
    """Mirror total as % of UAE-reported total, for every year both sources
    cover. FOB mirror vs CIF customs makes ~85-100% normal; well under that
    means origins are missing from partner reporting, well over means origins
    count shipments the UAE books as transit/re-exports."""
    conn.execute("DELETE FROM mirror_coverage_annual")
    overlap_years = [y for (y,) in conn.execute(
        """SELECT DISTINCT year FROM fact_imports
           WHERE year IN (SELECT DISTINCT year FROM fact_imports_mirror_annual)
           ORDER BY year"""
    )]
    if not overlap_years:
        print("  no overlap with UAE-reported years — coverage not computable")
        return
    for cid in COMMODITIES:
        parts = []
        for year in overlap_years:
            mirror = conn.execute(
                "SELECT coalesce(sum(trade_value_usd), 0) FROM fact_imports_mirror_annual "
                "WHERE commodity_id = ? AND year = ?", (cid, year),
            ).fetchone()[0]
            reported = conn.execute(
                "SELECT coalesce(sum(trade_value_usd), 0) FROM fact_imports "
                "WHERE commodity_id = ? AND year = ?", (cid, year),
            ).fetchone()[0]
            pct = 100 * mirror / reported if reported else None
            conn.execute(
                "INSERT OR REPLACE INTO mirror_coverage_annual "
                "(commodity_id, year, coverage_pct) VALUES (?,?,?)",
                (cid, year, pct),
            )
            parts.append(f"{year}: {pct:.0f}%" if pct is not None else f"{year}: n/a")
        print(f"  {cid} mirror vs UAE-reported — {', '.join(parts)}")


def main() -> None:
    conn = get_connection()
    print("Fetching annual mirror flows (all reporters) from UN Comtrade...")
    try:
        rows = fetch_mirror()
    except Exception as exc:
        print(f"Mirror fetch aborted ({exc}) — existing annual mirror data left untouched.")
        conn.close()
        sys.exit(1)

    if not rows:
        print("No mirror records returned — existing annual mirror data left untouched.")
        conn.close()
        sys.exit(1)

    inserted = store_rows(conn, rows)
    print("Cross-checking mirror against UAE-reported years...")
    store_coverage(conn)
    record_data_source(conn, "imports_mirror_annual", "live")
    latest = conn.execute("SELECT max(year) FROM fact_imports_mirror_annual").fetchone()[0]
    print(f"Loaded {inserted} annual mirror rows (latest year: {latest}).")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
