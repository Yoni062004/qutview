"""Create the QUTVIEW SQLite star schema.

Run first:  python src/db/init_db.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import COMMODITIES, get_connection

SCHEMA = """
CREATE TABLE IF NOT EXISTS dim_commodity (
    commodity_id   TEXT PRIMARY KEY,      -- e.g. 'wheat'
    hs_code        TEXT NOT NULL,
    name           TEXT NOT NULL,
    wb_series      TEXT,
    price_unit     TEXT
);

CREATE TABLE IF NOT EXISTS dim_country (
    country_code   INTEGER PRIMARY KEY,   -- UN M49 numeric code
    name           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_imports (
    year            INTEGER NOT NULL,
    commodity_id    TEXT NOT NULL REFERENCES dim_commodity(commodity_id),
    origin_code     INTEGER NOT NULL REFERENCES dim_country(country_code),
    trade_value_usd REAL,
    net_weight_kg   REAL,
    PRIMARY KEY (year, commodity_id, origin_code)
);

CREATE TABLE IF NOT EXISTS fact_prices (
    period          TEXT NOT NULL,        -- 'YYYY-MM'
    commodity_id    TEXT NOT NULL REFERENCES dim_commodity(commodity_id),
    price           REAL NOT NULL,
    PRIMARY KEY (period, commodity_id)
);

CREATE TABLE IF NOT EXISTS risk_scores (
    year             INTEGER NOT NULL,
    commodity_id     TEXT NOT NULL REFERENCES dim_commodity(commodity_id),
    hhi              REAL,                -- origin concentration, 0..1
    top_origin       TEXT,
    top_origin_share REAL,                -- 0..1
    n_origins        INTEGER,
    price_volatility REAL,                -- stdev of monthly returns
    composite_risk   REAL,                -- 0..100
    PRIMARY KEY (year, commodity_id)
);

CREATE TABLE IF NOT EXISTS forecasts (
    period          TEXT NOT NULL,        -- 'YYYY-MM'
    commodity_id    TEXT NOT NULL REFERENCES dim_commodity(commodity_id),
    forecast        REAL NOT NULL,
    lower_ci        REAL,
    upper_ci        REAL,
    PRIMARY KEY (period, commodity_id)
);

CREATE TABLE IF NOT EXISTS backtest_metrics (
    commodity_id    TEXT PRIMARY KEY REFERENCES dim_commodity(commodity_id),
    horizon_months  INTEGER,
    mape_pct        REAL,
    evaluated_at    TEXT
);

CREATE TABLE IF NOT EXISTS data_source (
    source_name     TEXT PRIMARY KEY,     -- 'imports' | 'prices'
    kind            TEXT NOT NULL,        -- 'live' | 'sample'
    loaded_at       TEXT NOT NULL
);
"""


def main() -> None:
    conn = get_connection()
    conn.executescript(SCHEMA)
    for cid, meta in COMMODITIES.items():
        conn.execute(
            "INSERT OR REPLACE INTO dim_commodity "
            "(commodity_id, hs_code, name, wb_series, price_unit) VALUES (?,?,?,?,?)",
            (cid, meta["hs"], meta["name"], meta["wb_series"], meta["unit"]),
        )
    conn.commit()
    conn.close()
    print("Database initialised: data/qutview.db")


if __name__ == "__main__":
    main()
