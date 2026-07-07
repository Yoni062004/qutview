"""Shared constants and helpers for QUTVIEW."""
import os
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "qutview.db"

UAE_REPORTER_CODE = 784  # UN M49 code for the United Arab Emirates
YEARS = list(range(2018, 2026))

# The strategic food commodities tracked by QUTVIEW.
# hs: HS chapter/heading code used by UN Comtrade.
# wb_series: column label in the World Bank Pink Sheet "Monthly Prices" sheet
# (matched after normalisation — case, whitespace and footnote asterisks ignored).
# Notes: maize is tracked because "domestic" UAE poultry/dairy runs on imported
# feed grain; beef uses HS 0202 (frozen) only — chilled 0201 is excluded to keep
# one HS code per commodity, and frozen is the bulk of long-haul beef trade.
COMMODITIES = {
    "wheat": {"hs": "1001", "name": "Wheat", "wb_series": "Wheat, US HRW", "unit": "USD/mt"},
    "rice": {"hs": "1006", "name": "Rice", "wb_series": "Rice, Thai 5%", "unit": "USD/mt"},
    "palm_oil": {"hs": "1511", "name": "Palm oil", "wb_series": "Palm oil", "unit": "USD/mt"},
    "sugar": {"hs": "1701", "name": "Sugar", "wb_series": "Sugar, world", "unit": "USD/kg"},
    "poultry": {"hs": "0207", "name": "Poultry", "wb_series": "Chicken", "unit": "USD/kg"},
    "maize": {"hs": "1005", "name": "Maize (feed)", "wb_series": "Maize", "unit": "USD/mt"},
    "sunflower_oil": {"hs": "1512", "name": "Sunflower oil", "wb_series": "Sunflower oil", "unit": "USD/mt"},
    "beef": {"hs": "0202", "name": "Beef (frozen)", "wb_series": "Beef", "unit": "USD/kg"},
}


def get_comtrade_key() -> str | None:
    """Read COMTRADE_API_KEY from the environment or a .env file in the
    project root. Returns None when no key is configured — callers fall
    back to the free preview endpoint."""
    key = os.environ.get("COMTRADE_API_KEY")
    if key:
        return key
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line.startswith("COMTRADE_API_KEY") and "=" in line:
                value = line.partition("=")[2].strip().strip("'\"")
                if value and "paste" not in value.lower():
                    return value
    return None


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def record_data_source(conn: sqlite3.Connection, source_name: str, kind: str) -> None:
    """Track whether a table was filled from a live API or sample data,
    so the dashboard can display an honest data-provenance badge."""
    conn.execute(
        "INSERT OR REPLACE INTO data_source (source_name, kind, loaded_at) "
        "VALUES (?, ?, datetime('now'))",
        (source_name, kind),
    )
