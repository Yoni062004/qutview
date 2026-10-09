"""Ingest fallback detection.

Production failure this guards against: a refresh where a source could not be
fetched, the step quietly kept (or replaced) existing data, and nothing failed
loudly — it was one line in a run log.

Two contracts every ingest script should honour when its fetch fails or
returns nothing:
  1. existing data is left untouched (never wiped, never replaced by sample);
  2. the process exits NON-ZERO, so scripts/refresh_all.py can see that the
     step kept stale data.
And refresh_all.py itself must end non-zero when any step kept stale data, so
a scheduled refresh can't report success over a stale database.

No network: every fetch function is monkeypatched.
"""
import subprocess
import sys

import pytest

from ingest import (comtrade_imports, comtrade_mirror_annual, fao_dairy_price,
                    fred_barley_price, fred_lentils_price, worldbank_prices)

# name -> (module, fetch attr, table holding its data, seed SQL, seed rows)
_PRICE_SEED = "INSERT INTO fact_prices (period, commodity_id, price) VALUES (?,?,?)"
INGESTS = {
    "comtrade_imports": (comtrade_imports, "fetch_live", "fact_imports",
                         "INSERT INTO fact_imports VALUES (?,?,?,?,?)",
                         [(2023, "wheat", 643, 400e6, 1e9), (2023, "wheat", 124, 100e6, 3e8)]),
    "comtrade_mirror_annual": (comtrade_mirror_annual, "fetch_mirror",
                               "fact_imports_mirror_annual",
                               "INSERT INTO fact_imports_mirror_annual VALUES (?,?,?,?,?)",
                               [(2024, "wheat", 124, 120e6, 3e8)]),
    "worldbank_prices": (worldbank_prices, "fetch_live", "fact_prices", _PRICE_SEED,
                         [("2026-01", "wheat", 250.0)]),
    "fred_barley_price": (fred_barley_price, "fetch_barley", "fact_prices", _PRICE_SEED,
                          [("2026-01", "barley", 210.0)]),
    "fred_lentils_price": (fred_lentils_price, "fetch_lentils", "fact_prices", _PRICE_SEED,
                           [("2026-01", "lentils", 180.0)]),
    "fao_dairy_price": (fao_dairy_price, "fetch_dairy", "fact_prices", _PRICE_SEED,
                        [("2026-01", "dairy", 140.0)]),
}

def _seed(db, name):
    _, _, table, sql, rows = INGESTS[name]
    for code in {r[2] for r in rows if table != "fact_prices"}:
        with db.write() as conn:
            conn.execute("INSERT OR IGNORE INTO dim_country VALUES (?, ?)", (code, f"C{code}"))
    with db.write() as conn:
        conn.executemany(sql, rows)
        conn.execute("INSERT INTO data_source VALUES (?, 'live', '2026-01-01 00:00:00')",
                     (name,))
    return table


def _snapshot(db, table):
    return sorted(db.query(f"SELECT * FROM {table}"))


def _run_with_fetch(monkeypatch, point_at, name, fetch):
    module, attr, *_ = INGESTS[name]
    point_at(module)
    monkeypatch.setattr(module, attr, fetch)
    monkeypatch.setattr(sys, "argv", [f"{name}.py"])
    if hasattr(module, "get_comtrade_key"):
        monkeypatch.setattr(module, "get_comtrade_key", lambda: "test-key")
    if hasattr(module, "enrich_country_names"):
        monkeypatch.setattr(module, "enrich_country_names", lambda conn: 0)
    try:
        module.main()
    except SystemExit as exc:
        return exc.code
    return 0


def _empty(*args, **kwargs):
    return []


def _boom(*args, **kwargs):
    raise ConnectionError("source unreachable")


@pytest.mark.parametrize("name", [
    "comtrade_imports",
    "comtrade_mirror_annual", "worldbank_prices",
    "fred_barley_price", "fred_lentils_price", "fao_dairy_price",
])
def test_empty_fetch_leaves_existing_data_untouched(db, point_at, monkeypatch, name):
    table = _seed(db, name)
    before = _snapshot(db, table)
    _run_with_fetch(monkeypatch, point_at, name, _empty)
    assert _snapshot(db, table) == before


@pytest.mark.parametrize("name", [
    "comtrade_imports",
    "comtrade_mirror_annual", "worldbank_prices",
    "fred_barley_price",
    "fred_lentils_price",
    "fao_dairy_price",
])
def test_empty_fetch_exits_nonzero(db, point_at, monkeypatch, name):
    _seed(db, name)
    code = _run_with_fetch(monkeypatch, point_at, name, _empty)
    assert code not in (0, None), "kept stale data but reported success"


@pytest.mark.parametrize("name", ["comtrade_imports", "comtrade_mirror_annual",
                                  "worldbank_prices", "fao_dairy_price"])
def test_fetch_error_keeps_data_and_exits_nonzero(db, point_at, monkeypatch, name):
    table = _seed(db, name)
    before = _snapshot(db, table)
    code = _run_with_fetch(monkeypatch, point_at, name, _boom)
    assert code not in (0, None)
    assert _snapshot(db, table) == before


@pytest.mark.parametrize("fetch", [_boom, _empty], ids=["error", "empty"])
@pytest.mark.parametrize("name", ["comtrade_imports", "comtrade_mirror_annual",
                                  "worldbank_prices"])
def test_failed_fetch_does_not_touch_provenance(db, point_at, monkeypatch, name, fetch):
    # data_source is the durable freshness/provenance signal; a failed refresh
    # must neither make stale data look freshly loaded nor relabel it 'sample'.
    _seed(db, name)
    before = sorted(db.query("SELECT * FROM data_source"))
    _run_with_fetch(monkeypatch, point_at, name, fetch)
    assert sorted(db.query("SELECT * FROM data_source")) == before


def test_comtrade_sample_only_via_explicit_flag(db, point_at, monkeypatch):
    # The offline demo path still works — but only when asked for.
    point_at(comtrade_imports)
    monkeypatch.setattr(comtrade_imports, "fetch_live", _boom)  # must not be called
    monkeypatch.setattr(sys, "argv", ["comtrade_imports.py", "--sample"])
    comtrade_imports.main()
    assert db.query("SELECT count(*) FROM fact_imports")[0][0] > 0
    assert db.query("SELECT kind FROM data_source WHERE source_name = 'imports'") ==         [("sample",)]


def test_successful_comtrade_fetch_replaces_data(db, point_at, monkeypatch):
    _seed(db, "comtrade_imports")
    live = [(2024, "rice", 356, "India", 900e6, 2e9)]
    code = _run_with_fetch(monkeypatch, point_at, "comtrade_imports", lambda conn: live)
    assert code == 0
    assert db.query("SELECT year, commodity_id, origin_code FROM fact_imports") == \
        [(2024, "rice", 356)]
    assert db.query("SELECT kind FROM data_source WHERE source_name = 'imports'") == \
        [("live",)]


# --- scripts/refresh_all.py ---------------------------------------------------


@pytest.fixture
def refresh(monkeypatch):
    """refresh(failing={script: returncode}) -> (exit code, stdout lines)."""
    import refresh_all

    def _run(failing, capsys):
        def fake_run(cmd, cwd=None):
            script = str(cmd[-1]).replace("\\", "/")
            rc = next((rc for s, rc in failing.items() if script.endswith(s)), 0)
            return subprocess.CompletedProcess(cmd, rc)
        monkeypatch.setattr(refresh_all.subprocess, "run", fake_run)
        try:
            refresh_all.main()
            code = 0
        except SystemExit as exc:
            code = exc.code
        return code, capsys.readouterr().out
    return _run


def test_refresh_all_lists_ingest_steps_that_kept_stale_data(refresh, capsys):
    code, out = refresh({"src/ingest/comtrade_imports.py": 1}, capsys)
    assert "kept existing data instead of refreshing" in out
    assert "src/ingest/comtrade_imports.py" in out


def test_refresh_all_lists_every_stale_step_and_fails(refresh, capsys):
    stale = ["src/ingest/comtrade_imports.py", "src/ingest/fred_barley_price.py",
             "src/ingest/fred_lentils_price.py", "src/ingest/fao_dairy_price.py"]
    code, out = refresh({s: 1 for s in stale}, capsys)
    assert code not in (0, None)
    assert "REFRESH INCOMPLETE: 4 source(s)" in str(code)
    for s in stale:
        assert f"  - {s}" in out
    assert "  - src/ingest/worldbank_prices.py" not in out
    # Every later step still ran: the export happened despite stale ingests.
    assert "=== scripts/export_powerbi.py ===" in out


def test_refresh_all_aborts_when_a_compute_step_fails(refresh, capsys):
    code, out = refresh({"src/features/risk_indicators.py": 1}, capsys)
    assert code not in (0, None)
    assert "ABORTED" in str(code)


def test_refresh_all_exits_nonzero_when_an_ingest_kept_stale_data(refresh, capsys):
    code, _ = refresh({"src/ingest/comtrade_imports.py": 1}, capsys)
    assert code not in (0, None)


def test_refresh_all_clean_run_exits_zero(refresh, capsys):
    code, out = refresh({}, capsys)
    assert code in (0, None)
    assert "kept existing data" not in out
