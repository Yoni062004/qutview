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
    source           TEXT NOT NULL DEFAULT 'uae_reported',  -- 'uae_reported' | 'mirror_derived'
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
    source_name     TEXT PRIMARY KEY,     -- 'imports' | 'prices' | 'imports_monthly'
    kind            TEXT NOT NULL,        -- 'live' | 'sample'
    loaded_at       TEXT NOT NULL
);

-- Monthly corridor flows reconstructed from mirror statistics: each origin
-- country's own reported exports to the UAE (the UAE stopped publishing
-- monthly customs data to Comtrade after 2019). FOB values as reported by
-- the origin, not UAE customs figures.
CREATE TABLE IF NOT EXISTS fact_imports_monthly (
    period          TEXT NOT NULL,        -- 'YYYY-MM'
    commodity_id    TEXT NOT NULL REFERENCES dim_commodity(commodity_id),
    origin_code     INTEGER NOT NULL REFERENCES dim_country(country_code),
    trade_value_usd REAL,
    net_weight_kg   REAL,
    PRIMARY KEY (period, commodity_id, origin_code)
);

CREATE TABLE IF NOT EXISTS risk_scores_monthly (
    period           TEXT NOT NULL,       -- 'YYYY-MM', rolling 12-month window ending here
    commodity_id     TEXT NOT NULL REFERENCES dim_commodity(commodity_id),
    hhi              REAL,
    top_origin       TEXT,
    top_origin_share REAL,
    n_origins        INTEGER,
    price_volatility REAL,                -- rolling 12-month stdev of monthly returns
    composite_risk   REAL,                -- 0..100, same weights as annual
    PRIMARY KEY (period, commodity_id)
);

-- UAE domestic production (FAOSTAT QCL, tonnes). Commodities the UAE does
-- not produce simply have no rows; the dependency computation treats
-- missing as zero production.
CREATE TABLE IF NOT EXISTS fact_production (
    year              INTEGER NOT NULL,
    commodity_id      TEXT NOT NULL REFERENCES dim_commodity(commodity_id),
    production_tonnes REAL,
    PRIMARY KEY (year, commodity_id)
);

-- Import dependency by weight: imports / (imports + domestic production).
-- An approximation — re-exports are not netted out and HS headings do not
-- map 1:1 to FAO items (e.g. frozen-only beef imports vs all cattle meat).
CREATE TABLE IF NOT EXISTS dependency_ratios (
    year            INTEGER NOT NULL,
    commodity_id    TEXT NOT NULL REFERENCES dim_commodity(commodity_id),
    import_kt       REAL,
    production_kt   REAL,
    dependency_pct  REAL,
    PRIMARY KEY (year, commodity_id)
);

-- How much of the UAE-reported annual import value the mirror origins cover,
-- measured on the latest year both sources report. Shown in the dashboard so
-- partial corridors (e.g. wheat: Russia stopped publishing in 2022) are
-- visible rather than hidden.
CREATE TABLE IF NOT EXISTS mirror_coverage (
    commodity_id    TEXT PRIMARY KEY REFERENCES dim_commodity(commodity_id),
    basis_year      INTEGER,
    coverage_pct    REAL
);

-- Annual corridor flows reconstructed from mirror statistics: every country
-- reporting exports to the UAE, used to extend the annual series past the
-- last UAE-published year (the UAE releases annual data ~18 months late).
-- Kept separate from fact_imports so UAE-reported data is never mixed with
-- origin-reported FOB values.
CREATE TABLE IF NOT EXISTS fact_imports_mirror_annual (
    year            INTEGER NOT NULL,
    commodity_id    TEXT NOT NULL REFERENCES dim_commodity(commodity_id),
    origin_code     INTEGER NOT NULL REFERENCES dim_country(country_code),
    trade_value_usd REAL,
    net_weight_kg   REAL,
    PRIMARY KEY (year, commodity_id, origin_code)
);

-- Annual-mirror cross-check: mirror total as % of the UAE-reported total for
-- every year both sources cover. Persisted per year (not just the latest) so
-- drift is visible, e.g. wheat collapsing after Russia stopped publishing.
CREATE TABLE IF NOT EXISTS mirror_coverage_annual (
    commodity_id    TEXT NOT NULL REFERENCES dim_commodity(commodity_id),
    year            INTEGER NOT NULL,
    coverage_pct    REAL,
    PRIMARY KEY (commodity_id, year)
);
"""


def main() -> None:
    conn = get_connection()
    conn.executescript(SCHEMA)
    # Migration for databases created before provenance tracking: CREATE TABLE
    # IF NOT EXISTS never adds columns to an existing table.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(risk_scores)")]
    if "source" not in cols:
        conn.execute(
            "ALTER TABLE risk_scores ADD COLUMN source TEXT NOT NULL DEFAULT 'uae_reported'"
        )
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
