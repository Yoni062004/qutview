"""Shared fixtures for the QUTVIEW test suite.

The suite is offline and deterministic: no live APIs, and never the committed
data/qutview.db. Each DB-backed test gets a fresh SQLite file in pytest's
tmp_path, built from the production schema (db/init_db.py SCHEMA), and the
modules under test are pointed at it by monkeypatching their get_connection.
A file (not :memory:) is used because the production entry points close
their connection when done, which would discard an in-memory database.
"""
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
for p in (str(SRC), str(PROJECT_ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

from common import COMMODITIES  # noqa: E402
from db.init_db import SCHEMA  # noqa: E402


class FixtureDB:
    """A throwaway QUTVIEW database plus small helpers for seeding it."""

    def __init__(self, path: Path):
        self.path = path
        self._codes: dict[str, int] = {}
        conn = self.connect()
        conn.executescript(SCHEMA)
        for cid, meta in COMMODITIES.items():
            conn.execute(
                "INSERT INTO dim_commodity (commodity_id, hs_code, name, wb_series, "
                "price_unit) VALUES (?,?,?,?,?)",
                (cid, meta["hs"], meta["name"], meta["wb_series"], meta["unit"]))
        conn.commit()
        conn.close()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def write(self):
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def code(self, origin: str) -> int:
        """Stable synthetic country code per origin name (inserted on demand)."""
        if origin not in self._codes:
            self._codes[origin] = 1000 + len(self._codes)
            with self.write() as conn:
                conn.execute("INSERT INTO dim_country (country_code, name) VALUES (?,?)",
                             (self._codes[origin], origin))
        return self._codes[origin]

    def add_flows(self, table: str, year: int, cid: str, values: dict[str, float],
                  weight_kg: float | None = None) -> None:
        """Annual flows {origin: USD} into fact_imports or fact_imports_mirror_annual."""
        rows = [(year, cid, self.code(o), v, weight_kg) for o, v in values.items()]
        with self.write() as conn:
            conn.executemany(
                f"INSERT INTO {table} (year, commodity_id, origin_code, trade_value_usd, "
                f"net_weight_kg) VALUES (?,?,?,?,?)", rows)

    def add_coverage(self, cid: str, year: int, pct: float) -> None:
        with self.write() as conn:
            conn.execute("INSERT INTO mirror_coverage_annual (commodity_id, year, "
                         "coverage_pct) VALUES (?,?,?)", (cid, year, pct))

    def add_prices(self, cid: str, prices: dict[str, float]) -> None:
        with self.write() as conn:
            conn.executemany("INSERT INTO fact_prices (period, commodity_id, price) "
                             "VALUES (?,?,?)", [(p, cid, v) for p, v in prices.items()])

    def query(self, sql: str, params=()) -> list[tuple]:
        conn = self.connect()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()


@pytest.fixture
def db(tmp_path) -> FixtureDB:
    return FixtureDB(tmp_path / "test.db")


@pytest.fixture
def point_at(db, monkeypatch):
    """point_at(module, ...) — make each module's get_connection open the
    fixture DB instead of data/qutview.db."""
    def _point(*modules):
        for m in modules:
            monkeypatch.setattr(m, "get_connection", db.connect)
    return _point


@pytest.fixture
def score(db, point_at):
    """Run the production annual risk model against the fixture DB and return
    {(year, commodity_id): row-dict} from risk_scores."""
    from features import risk_indicators

    def _score(ballast=True):
        # KNOWN BUG (see test_risk_indicators.py::test_scores_with_no_mirror_
        # extension): main() crashes when no commodity has a gate-passing mirror
        # extension. Until that is fixed, tests that are about the arithmetic,
        # not that path, add one passing mirror year for an otherwise-unused
        # commodity ("lentils") so the extension frame is non-empty. Its rows
        # appear in the result but no test asserts on them.
        if ballast:
            db.add_flows("fact_imports", 2018, "lentils", {"Ballast": 1.0})
            db.add_flows("fact_imports_mirror_annual", 2019, "lentils", {"Ballast": 1.0})
            db.add_coverage("lentils", 2018, 100.0)
        point_at(risk_indicators)
        risk_indicators.main()
        conn = db.connect()
        cur = conn.execute("SELECT * FROM risk_scores")
        cols = [c[0] for c in cur.description]
        out = {(r[0], r[1]): dict(zip(cols, r)) for r in cur.fetchall()}
        conn.close()
        return out
    return _score


def make_facts(*, share=0.50, risk=30.0, year=2024, top_origin="Brazil",
               provisional=False, previous=None, monthly=None,
               flow_decomposition=None, name="Wheat", cid="wheat") -> dict:
    """Minimal assemble_facts()-shaped dict for rule tests that don't need a DB.
    Only the keys the alert rules, driver note and template read are filled."""
    return {
        "commodity": {"id": cid, "name": name, "hs_code": COMMODITIES[cid]["hs"],
                      "price_unit": COMMODITIES[cid]["unit"]},
        "risk_latest": {"year": year, "source": "mirror_derived" if provisional
                        else "uae_reported", "composite_risk": risk, "hhi": share ** 2,
                        "top_origin": top_origin, "top_origin_share": share,
                        "n_origins": 4, "price_volatility": 0.02},
        "risk_previous": previous,
        "monthly_trend": monthly,
        "top_origins": [], "total_import_value_usd": 0,
        "alternatives": {"candidates": [], "has_alternatives": False},
        "drivers": {"price_momentum": None, "flow_decomposition": flow_decomposition},
        "dependency": None, "exposure_adjusted_risk": None, "latest_price": None,
        "forecast": None, "backtest": None,
        "provenance": {"source": "mirror_derived" if provisional else "uae_reported",
                       "provisional": provisional, "mirror_gate": None,
                       "newer_mirror_years_unscored": []},
        "gaps": [],
    }


@pytest.fixture
def facts_factory():
    return make_facts
