"""Ingest UAE food import flows (by origin country) from UN Comtrade.

Uses the free public preview endpoint (no API key, capped at 500 records
per call, so we query one commodity-year at a time). If the API is
unreachable or returns nothing, falls back to realistic sample data so
the rest of the pipeline always works.

Run:  python src/ingest/comtrade_imports.py               (live, with fallback)
      python src/ingest/comtrade_imports.py --sample      (force sample data)
      python src/ingest/comtrade_imports.py --names-only  (refresh country names only)
"""
import random
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import COMMODITIES, UAE_REPORTER_CODE, YEARS, get_connection, record_data_source

PREVIEW_URL = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
PARTNER_REF_URL = "https://comtradeapi.un.org/files/v1/app/reference/partnerAreas.json"

# Realistic origin mixes used only for sample mode / fallback.
# Shares drift a little year to year via random noise.
SAMPLE_ORIGINS = {
    "wheat":    {"Russia": 0.55, "Canada": 0.15, "India": 0.12, "Australia": 0.10, "Ukraine": 0.08},
    "rice":     {"India": 0.45, "Pakistan": 0.25, "Thailand": 0.15, "Viet Nam": 0.10, "USA": 0.05},
    "palm_oil": {"Indonesia": 0.55, "Malaysia": 0.38, "Thailand": 0.04, "Papua New Guinea": 0.03},
    "sugar":    {"India": 0.40, "Brazil": 0.35, "Thailand": 0.15, "Egypt": 0.10},
    "poultry":  {"Brazil": 0.70, "USA": 0.12, "France": 0.08, "Ukraine": 0.06, "Turkey": 0.04},
}
SAMPLE_BASE_VALUE_USD = {
    "wheat": 450e6, "rice": 700e6, "palm_oil": 380e6, "sugar": 300e6, "poultry": 550e6,
}


def enrich_country_names(conn) -> int:
    """The preview endpoint returns numeric M49 partner codes without labels.
    Pull Comtrade's official partner reference once and set real names on any
    dim_country row whose name is still just the code."""
    try:
        r = requests.get(PARTNER_REF_URL, timeout=30)
        r.raise_for_status()
        ref = {int(row["id"]): row["text"] for row in r.json().get("results", [])
               if str(row.get("id", "")).lstrip("-").isdigit()}
    except Exception as exc:
        print(f"Could not fetch country reference names ({exc}) — codes kept as names.")
        return 0
    updated = 0
    for code, name in conn.execute("SELECT country_code, name FROM dim_country").fetchall():
        if name == str(code) and code in ref:
            conn.execute("UPDATE dim_country SET name = ? WHERE country_code = ?",
                         (ref[code], code))
            updated += 1
    return updated


def fetch_live(conn) -> int:
    """Query Comtrade preview endpoint per commodity-year. Returns rows inserted."""
    inserted = 0
    for cid, meta in COMMODITIES.items():
        for year in YEARS:
            params = {
                "reporterCode": UAE_REPORTER_CODE,
                "period": year,
                "cmdCode": meta["hs"],
                "flowCode": "M",
                "maxRecords": 500,
            }
            try:
                r = requests.get(PREVIEW_URL, params=params, timeout=30)
                r.raise_for_status()
                records = r.json().get("data", [])
            except Exception as exc:
                print(f"  {cid} {year}: request failed ({exc})")
                continue
            for rec in records:
                partner = rec.get("partnerCode")
                if not partner:  # partnerCode 0 = 'World' aggregate — skip
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO dim_country (country_code, name) VALUES (?,?)",
                    (partner, rec.get("partnerDesc") or str(partner)),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO fact_imports "
                    "(year, commodity_id, origin_code, trade_value_usd, net_weight_kg) "
                    "VALUES (?,?,?,?,?)",
                    (year, cid, partner, rec.get("primaryValue"), rec.get("netWgt")),
                )
                inserted += 1
            print(f"  {cid} {year}: {len(records)} records")
            time.sleep(1)  # stay polite on the free endpoint
    return inserted


def load_sample(conn) -> int:
    rng = random.Random(42)
    inserted = 0
    # Deterministic fake country codes for sample origins
    code_for = {}
    next_code = 1
    for cid, origins in SAMPLE_ORIGINS.items():
        base = SAMPLE_BASE_VALUE_USD[cid]
        for year in YEARS:
            growth = 1.0 + 0.05 * (year - YEARS[0])  # imports grow ~5%/yr
            for country, share in origins.items():
                if country not in code_for:
                    code_for[country] = next_code
                    next_code += 1
                    conn.execute(
                        "INSERT OR IGNORE INTO dim_country (country_code, name) VALUES (?,?)",
                        (code_for[country], country),
                    )
                noisy_share = max(0.005, share * rng.uniform(0.85, 1.15))
                value = base * growth * noisy_share
                conn.execute(
                    "INSERT OR REPLACE INTO fact_imports "
                    "(year, commodity_id, origin_code, trade_value_usd, net_weight_kg) "
                    "VALUES (?,?,?,?,?)",
                    (year, cid, code_for[country], value, value / 0.45),
                )
                inserted += 1
    return inserted


def main() -> None:
    force_sample = "--sample" in sys.argv
    names_only = "--names-only" in sys.argv
    conn = get_connection()

    if names_only:
        updated = enrich_country_names(conn)
        conn.commit()
        conn.close()
        print(f"Refreshed {updated} country names.")
        return

    conn.execute("DELETE FROM fact_imports")

    inserted = 0
    if not force_sample:
        print("Fetching UAE import flows from UN Comtrade (free preview endpoint)...")
        inserted = fetch_live(conn)

    if inserted == 0:
        if not force_sample:
            print("Live API returned no data — falling back to sample data.")
        inserted = load_sample(conn)
        record_data_source(conn, "imports", "sample")
        print(f"Loaded {inserted} sample import rows.")
    else:
        named = enrich_country_names(conn)
        record_data_source(conn, "imports", "live")
        print(f"Loaded {inserted} live import rows from UN Comtrade "
              f"({named} country names resolved).")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
