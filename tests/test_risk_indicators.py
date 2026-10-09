"""Annual risk model (src/features/risk_indicators.py).

Runs the production main() against a synthetic DB and checks the stored
risk_scores against hand-computed expectations, so the arithmetic the
product claims (HHI, top-origin share, the 50/30/20 composite, the 10%
volatility cap) is pinned independently of how the code computes it.
"""
import statistics

import pandas as pd
import pytest

from features import risk_indicators as ri


def test_documented_weights_and_cap():
    # The README / docs quote these numbers; a silent retune should fail here.
    assert (ri.W_HHI, ri.W_TOP_SHARE, ri.W_VOLATILITY) == (0.5, 0.3, 0.2)
    assert ri.W_HHI + ri.W_TOP_SHARE + ri.W_VOLATILITY == pytest.approx(1.0)
    assert ri.VOL_CAP == 0.10
    assert ri.MIN_MIRROR_COVERAGE_PCT == 75.0


# --- HHI, top-origin share, n_origins ------------------------------------


def test_single_origin_is_full_concentration(db, score):
    db.add_flows("fact_imports", 2023, "wheat", {"Russia": 400e6})
    r = score()[(2023, "wheat")]
    assert r["hhi"] == pytest.approx(1.0)
    assert r["top_origin"] == "Russia"
    assert r["top_origin_share"] == pytest.approx(1.0)
    assert r["n_origins"] == 1
    # No prices -> volatility 0, so composite = 100 * (0.5*1 + 0.3*1) = 80.
    assert r["price_volatility"] == 0.0
    assert r["composite_risk"] == pytest.approx(80.0)


@pytest.mark.parametrize("n", [2, 3, 4, 5, 10])
def test_n_equal_origins_hhi_is_one_over_n(db, score, n):
    db.add_flows("fact_imports", 2023, "rice", {f"Origin{i}": 50e6 for i in range(n)})
    r = score()[(2023, "rice")]
    assert r["hhi"] == pytest.approx(1 / n)
    assert r["top_origin_share"] == pytest.approx(1 / n)
    assert r["n_origins"] == n
    assert r["composite_risk"] == pytest.approx(100 * (0.5 / n + 0.3 / n))


def test_known_mix(db, score):
    # 50/30/20 split -> HHI = .25 + .09 + .04 = .38
    db.add_flows("fact_imports", 2023, "sugar",
                 {"India": 500e6, "Brazil": 300e6, "Thailand": 200e6})
    r = score()[(2023, "sugar")]
    assert r["hhi"] == pytest.approx(0.38)
    assert r["top_origin"] == "India"
    assert r["top_origin_share"] == pytest.approx(0.50)
    assert r["n_origins"] == 3
    assert r["composite_risk"] == pytest.approx(100 * (0.5 * 0.38 + 0.3 * 0.50))


def test_zero_and_null_values_are_not_origins(db, score):
    # A $0 or NULL row must not count as an origin or dilute the shares.
    db.add_flows("fact_imports", 2023, "maize", {"USA": 300e6, "Argentina": 100e6})
    db.add_flows("fact_imports", 2023, "maize", {"Ukraine": 0.0})
    with db.write() as conn:
        conn.execute("INSERT INTO fact_imports VALUES (2023, 'maize', ?, NULL, NULL)",
                     (db.code("Brazil"),))
    r = score()[(2023, "maize")]
    assert r["n_origins"] == 2
    assert r["hhi"] == pytest.approx(0.75**2 + 0.25**2)


def test_years_and_commodities_scored_independently(db, score):
    db.add_flows("fact_imports", 2022, "wheat", {"A": 100, "B": 100})
    db.add_flows("fact_imports", 2023, "wheat", {"A": 100})
    db.add_flows("fact_imports", 2023, "rice", {"A": 100, "B": 100, "C": 100, "D": 100})
    s = score()
    assert s[(2022, "wheat")]["hhi"] == pytest.approx(0.5)
    assert s[(2023, "wheat")]["hhi"] == pytest.approx(1.0)
    assert s[(2023, "rice")]["hhi"] == pytest.approx(0.25)
    assert all(r["source"] == "uae_reported"
               for (_, cid), r in s.items() if cid != "lentils")


# --- volatility term -------------------------------------------------------


def _monthly(year, values):
    return {f"{year}-{m:02d}": v for m, v in enumerate(values, start=1)}


def _expected_vol(values):
    """Sample stdev of month-on-month returns, computed independently."""
    rets = [b / a - 1 for a, b in zip(values, values[1:])]
    return statistics.stdev(rets)


def test_volatility_below_cap_is_weighted_at_20pct(db, score):
    prices = [100, 102, 100, 103, 101, 104, 102, 100, 103, 101, 102, 100]
    db.add_flows("fact_imports", 2023, "wheat", {"A": 100, "B": 100})
    db.add_prices("wheat", _monthly(2023, prices))
    r = score()[(2023, "wheat")]
    vol = _expected_vol(prices)
    assert vol < ri.VOL_CAP  # fixture sanity: this case is below the cap
    assert r["price_volatility"] == pytest.approx(vol)
    assert r["composite_risk"] == pytest.approx(
        100 * (0.5 * 0.5 + 0.3 * 0.5 + 0.2 * vol / 0.10))


def test_volatility_is_capped_at_10pct(db, score):
    prices = [100, 150, 90, 160, 80, 170, 70, 150, 90, 160, 80, 170]  # wild swings
    db.add_flows("fact_imports", 2023, "wheat", {"A": 100})
    db.add_prices("wheat", _monthly(2023, prices))
    r = score()[(2023, "wheat")]
    vol = _expected_vol(prices)
    assert vol > ri.VOL_CAP
    # Raw volatility is stored uncapped; only its contribution is capped...
    assert r["price_volatility"] == pytest.approx(vol)
    # ...so a single origin + max volatility is exactly 100, never above.
    assert r["composite_risk"] == pytest.approx(100.0)


def test_volatility_does_not_leak_between_commodities(db, score):
    db.add_flows("fact_imports", 2023, "wheat", {"A": 100})
    db.add_flows("fact_imports", 2023, "rice", {"A": 100})
    db.add_prices("rice", _monthly(2023, [100, 150, 90, 160, 80, 170,
                                         70, 150, 90, 160, 80, 170]))
    s = score()
    assert s[(2023, "wheat")]["price_volatility"] == 0.0
    assert s[(2023, "wheat")]["composite_risk"] == pytest.approx(80.0)
    assert s[(2023, "rice")]["composite_risk"] == pytest.approx(100.0)


def test_composite_is_bounded_0_100(db, score):
    db.add_flows("fact_imports", 2023, "wheat", {"A": 1})
    db.add_flows("fact_imports", 2023, "rice", {f"O{i}": 1 for i in range(200)})
    s = score()
    assert all(0 <= r["composite_risk"] <= 100 for r in s.values())


# --- known bug -------------------------------------------------------------


@pytest.mark.xfail(strict=True, raises=pd.errors.InvalidIndexError, reason=(
    "BUG: main() sets imports['source'] before calling mirror_extension(), whose "
    "empty-result path returns DataFrame(columns=[*imports.columns, 'source']) - "
    "'source' twice - so pd.concat raises InvalidIndexError. Any DB with no "
    "gate-passing mirror extension (fresh clone without a Comtrade key, or every "
    "commodity below 75% coverage) cannot be scored at all."))
@pytest.mark.parametrize("mirror", ["none", "all_below_gate"])
def test_scores_with_no_mirror_extension(db, score, mirror):
    db.add_flows("fact_imports", 2023, "wheat", {"Russia": 600e6, "Canada": 400e6})
    if mirror == "all_below_gate":
        db.add_flows("fact_imports_mirror_annual", 2024, "wheat", {"Canada": 1e6})
        db.add_coverage("wheat", 2023, 10.0)
    s = score(ballast=False)
    assert s[(2023, "wheat")]["hhi"] == pytest.approx(0.52)
