"""Ingest UAE domestic production from the FAOSTAT bulk file.

FAOSTAT's REST API moved behind a registered developer portal in 2025,
but the bulk CSV downloads remain open. This script uses the QCL
(Production: Crops and Livestock) all-data file: downloaded once into
data/raw (~34 MB zip) and reused on later runs, like the Pink Sheet.

Item mapping is by FAO item code, verified against the actual file.
Commodities the UAE does not produce (rice, palm oil, sunflower seed,
sugar cane) have no rows at all in FAOSTAT; wheat has rows with missing
values. Both cases are stored as zero production — the honest reading
for the UAE — so downstream dependency ratios read 100% imported.

Run:  python src/ingest/faostat_production.py
"""
import math
import sys
import zipfile
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import COMMODITIES, DATA_DIR, YEARS, get_connection, record_data_source

BULK_URL = ("https://bulks-faostat.fao.org/production/"
            "Production_Crops_Livestock_E_All_Data_(Normalized).zip")
LOCAL_COPY = DATA_DIR / "raw" / "Production_Crops_Livestock_E_All_Data_(Normalized).zip"
UAE_FAO_AREA = 225

# FAO QCL item code per commodity (verified against the bulk file).
# None = the UAE has no production series for it; stored as zero.
FAO_ITEMS = {
    "wheat": 15,            # rows exist but values are missing -> 0
    "rice": 27,
    "palm_oil": 254,        # oil palm fruit
    "sugar": 156,           # sugar cane
    "poultry": 1058,        # meat of chickens, fresh or chilled
    "maize": 56,            # maize (corn)
    "sunflower_oil": 267,   # sunflower seed
    "beef": 867,            # meat of cattle with the bone, fresh or chilled
    # No FAOSTAT crops/livestock production item for soybean MEAL (a processed
    # feed, not a crop) -> None -> stored as zero, ~100% import-dependent. Do
    # NOT map to "Soya bean oil" (item 237, which the UAE does produce): meal
    # and oil are different products of the same bean.
    "soybean_meal": None,
    # Milk POWDER (HS 0402): no UAE production (the UAE produces fresh milk —
    # ~266 kt/yr raw milk, FAOSTAT item 1780 — but essentially no milk powder).
    # None -> zero -> ~100% import-dependent on powder specifically. Do NOT map
    # to fresh-milk production: powder is the storable, tradeable form imported.
    "dairy": None,
    "barley": 44,          # rows exist but values are missing/zero -> 0 (like wheat)
}


def download_bulk() -> None:
    print(f"Downloading FAOSTAT bulk file (~34 MB) to {LOCAL_COPY} ...")
    LOCAL_COPY.parent.mkdir(parents=True, exist_ok=True)
    tmp = LOCAL_COPY.with_suffix(".part")
    with requests.get(BULK_URL, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.replace(LOCAL_COPY)


def parse_uae_production() -> list[tuple]:
    """Returns (year, commodity_id, production_tonnes) tuples for YEARS.
    Every commodity-year in range gets a row (zero when FAOSTAT has no
    data), so downstream joins never mistake 'missing' for 'unknown'."""
    zf = zipfile.ZipFile(LOCAL_COPY)
    csv_name = [n for n in zf.namelist()
                if n.endswith(".csv") and "Codes" not in n and "Flags" not in n
                and "Elements" not in n][0]
    code_to_cid = {code: cid for cid, code in FAO_ITEMS.items() if code is not None}
    found = {}
    for chunk in pd.read_csv(zf.open(csv_name), chunksize=500_000,
                             encoding="latin-1", low_memory=False):
        sel = chunk[
            (chunk["Area Code"] == UAE_FAO_AREA)
            & (chunk["Element"] == "Production")
            & (chunk["Unit"] == "t")
            & (chunk["Item Code"].isin(code_to_cid))
            & (chunk["Year"] >= YEARS[0])
        ]
        for _, row in sel.iterrows():
            v = row["Value"]
            found[(int(row["Year"]), code_to_cid[int(row["Item Code"])])] = (
                0.0 if (v is None or (isinstance(v, float) and math.isnan(v))) else float(v)
            )
    rows = []
    for year in YEARS:
        for cid in COMMODITIES:
            rows.append((year, cid, found.get((year, cid), 0.0)))
    return rows


def main() -> None:
    if not LOCAL_COPY.exists():
        try:
            download_bulk()
        except Exception as exc:
            print(f"FAOSTAT bulk download failed ({exc}) — existing production data left untouched.")
            sys.exit(1)
    else:
        print(f"Using local copy: {LOCAL_COPY}")

    try:
        rows = parse_uae_production()
    except Exception as exc:
        print(f"FAOSTAT parse failed ({exc}) — existing production data left untouched.")
        sys.exit(1)

    conn = get_connection()
    conn.execute("DELETE FROM fact_production")
    conn.executemany(
        "INSERT OR REPLACE INTO fact_production (year, commodity_id, production_tonnes) "
        "VALUES (?,?,?)",
        rows,
    )
    record_data_source(conn, "production", "live")
    nonzero = sum(1 for _, _, v in rows if v > 0)
    print(f"Stored {len(rows)} production rows ({nonzero} non-zero).")
    nonzero_years = [y for y, _, v in rows if v > 0]
    if nonzero_years:
        latest = max(nonzero_years)
        for year, cid, v in rows:
            if year == latest and v > 0:
                print(f"  {cid} {year}: {v/1000:.1f} kt")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
