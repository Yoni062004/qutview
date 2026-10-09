"""Float-robust threshold comparisons (common.at_least / at_most) and the
places that use them outside the alert feed."""
import pytest

from brief import corridor_brief as cb
from common import at_least, at_most


@pytest.mark.parametrize("value, threshold, expected", [
    (0.60 - 0.55, 0.05, True),     # 0.04999... -> at the threshold
    (0.05, 0.05, True),
    (0.0499, 0.05, False),
    (0.04999999, 0.05, False),     # a real shortfall is not rounded away
    (100 * 16.08 / 21.44, 75.0, True),
])
def test_at_least(value, threshold, expected):
    assert at_least(value, threshold) is expected


@pytest.mark.parametrize("value, threshold, expected", [
    (-0.05000000000000001, -0.05, True),
    (-0.0499, -0.05, False),
])
def test_at_most(value, threshold, expected):
    assert at_most(value, threshold) is expected


@pytest.mark.parametrize("ref, latest, expected", [
    (3, 3.15, "rising"),           # +5% on paper; (3.15-3)/3 = 0.04999...
    (6, 6.3, "rising"),
    (100, 104.9, "stable"),
    (100, 115, "sharp rise"),
    (100, 95, "falling"),
    (100, 85, "sharp fall"),
])
def test_momentum_classification_at_boundaries(ref, latest, expected):
    assert cb._classify_momentum((latest - ref) / ref) == expected


def test_flat_incumbent_boundary_is_exactly_five_percent(facts_factory):
    def note(prev, latest):
        fd = {"latest_year": 2024, "prev_year": 2023, "provisional": False,
              "incumbent": {"origin": "Brazil", "prev_value": prev, "latest_value": latest,
                            "change_usd": latest - prev,
                            "change_pct": 100 * (latest - prev) / prev},
              "risers": [], "fallers": [], "unreported": None,
              "total_prev": 1e9, "total_latest": 1e9, "market_change_pct": 0.0}
        return cb.concentration_driver_note(facts_factory(flow_decomposition=fd))
    # A 5% move is a move (described), not "roughly flat" (silent) — even when
    # the computed change_pct is 4.99999...
    p = 1e6 * 5 / 9  # 100 * (p * 1.05 - p) / p = 4.99999...
    assert note(p, p * 1.05) is not None
    assert note(1e6, 1.049e6) is None
