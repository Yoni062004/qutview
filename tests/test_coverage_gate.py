"""Mirror coverage gate (MIN_MIRROR_COVERAGE_PCT = 75).

A mirror-derived year may only be scored for a commodity whose annual mirror
covered >= 75% of UAE-reported value on the latest overlap year. Below that,
a major origin is missing from partner reporting and the score would name
the wrong top origin.
"""
import pytest

from features import risk_indicators as ri


def _seed(db, cid="wheat", coverage=None):
    """UAE-reported through 2023; mirror covers 2023 (overlap) and 2024 (new)."""
    db.add_flows("fact_imports", 2023, cid, {"Russia": 600e6, "Canada": 400e6})
    db.add_flows("fact_imports_mirror_annual", 2023, cid, {"Canada": 400e6})
    db.add_flows("fact_imports_mirror_annual", 2024, cid,
                 {"Canada": 420e6, "Australia": 100e6})
    if coverage is not None:
        db.add_coverage(cid, 2023, coverage)


@pytest.mark.parametrize("coverage, scored", [
    (0.0, False),
    (74.9, False),
    (74.99, False),
    (75.0, True),    # "at least" 75% — the threshold itself passes
    (100 * 16.08 / 21.44, True),   # 75% on paper, 74.99999999999999 in binary
    (75.1, True),
    (100.0, True),
])
def test_gate_boundary(db, score, coverage, scored):
    _seed(db, coverage=coverage)
    s = score()
    assert (2023, "wheat") in s  # the UAE-reported year is always scored
    assert ((2024, "wheat") in s) is scored
    if scored:
        assert s[(2024, "wheat")]["source"] == "mirror_derived"


def test_below_threshold_is_reported_as_excluded(db, point_at):
    _seed(db, coverage=74.9)
    point_at(ri)
    conn = db.connect()
    import pandas as pd
    imports = pd.read_sql(
        """SELECT f.year, f.commodity_id, c.name AS origin, f.trade_value_usd
           FROM fact_imports f JOIN dim_country c ON c.country_code = f.origin_code""",
        conn)
    ext, excluded = ri.mirror_extension(conn, imports)
    conn.close()
    assert ext.empty
    assert excluded == {"wheat": (2023, 74.9)}


def test_gate_uses_latest_overlap_year_not_best(db, score):
    # A great 2021 must not vouch for a commodity whose 2023 coverage collapsed
    # (the Russia-stopped-publishing case).
    _seed(db)
    db.add_coverage("wheat", 2021, 98.0)
    db.add_coverage("wheat", 2023, 40.0)
    assert (2024, "wheat") not in score()


def test_no_coverage_row_means_no_mirror_extension(db, score):
    # Mirror rows without any cross-check must not be scored by default.
    _seed(db, coverage=None)
    assert (2024, "wheat") not in score()


def test_gate_is_per_commodity(db, score):
    _seed(db, "wheat", coverage=50.0)
    _seed(db, "rice", coverage=90.0)
    s = score()
    assert (2024, "wheat") not in s
    assert (2024, "rice") in s


def test_mirror_never_overrides_a_uae_reported_year(db, score):
    # Mirror rows for a year the UAE already published must be ignored.
    _seed(db, coverage=100.0)
    s = score()
    assert s[(2023, "wheat")]["source"] == "uae_reported"
    assert s[(2023, "wheat")]["top_origin"] == "Russia"  # from UAE data, not mirror


def test_mirror_year_scored_from_mirror_values(db, score):
    _seed(db, coverage=80.0)
    r = score()[(2024, "wheat")]
    assert r["top_origin"] == "Canada"
    assert r["top_origin_share"] == pytest.approx(420 / 520)
    assert r["n_origins"] == 2
