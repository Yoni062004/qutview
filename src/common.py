"""Shared constants and helpers for QUTVIEW."""
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "qutview.db"

UAE_REPORTER_CODE = 784  # UN M49 code for the United Arab Emirates
YEARS = list(range(2018, 2026))

# The five strategic food commodities tracked in v1.
# hs: HS chapter/heading code used by UN Comtrade.
# wb_series: column label in the World Bank Pink Sheet "Monthly Prices" sheet
# (matched after normalisation — case, whitespace and footnote asterisks ignored).
COMMODITIES = {
    "wheat": {"hs": "1001", "name": "Wheat", "wb_series": "Wheat, US HRW", "unit": "USD/mt"},
    "rice": {"hs": "1006", "name": "Rice", "wb_series": "Rice, Thai 5%", "unit": "USD/mt"},
    "palm_oil": {"hs": "1511", "name": "Palm oil", "wb_series": "Palm oil", "unit": "USD/mt"},
    "sugar": {"hs": "1701", "name": "Sugar", "wb_series": "Sugar, world", "unit": "USD/kg"},
    "poultry": {"hs": "0207", "name": "Poultry", "wb_series": "Chicken", "unit": "USD/kg"},
}


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
