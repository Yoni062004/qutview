"""Threshold alerting (roadmap A2): WHEN to look, not just what.

Turns current risk data into a short list of standing alerts. Deterministic
and grounded: every alert names the exact fact number that tripped it and
the threshold line it crossed. The facts come from A1's assemble_facts() —
the single source of truth — so an alert can never contradict a brief.

Honesty rules: alerts built on provisional/mirror-derived years carry that
label in the message (no confident alarms on reconstructed data), and the
rising-trend rule is framed as DIRECTION only, because the rolling-monitor
level is not comparable to the annual score.

Stateless v1: standing alerts are recomputed from current data on each run.
"New since last run" persistence (alert history / acknowledgements) is a
noted future step, not built yet.

Run:  python src/features/alerts.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import COMMODITIES, get_connection
from brief.corridor_brief import assemble_facts

# Thresholds — named so they can be tuned and defended, not buried in logic.
CONCENTRATION_HIGH = 0.80   # top-origin share: one supplier is 4/5 of supply,
                            # a single disruption removes most of the corridor
CONCENTRATION_WATCH = 0.60  # majority single-source; diversification erodes
# Risk thresholds tuned against the actual score distribution (2025 scores
# run 32..72): 65+ isolates the corridors in real trouble (poultry 72, sugar
# 68, rice 67); 45..65 is moderate (palm oil 56). Below 45 a corridor is not
# demanding attention by level alone — the old WATCH=30 caught even the
# safest corridor (wheat 32) and diluted the feed into noise.
RISK_HIGH = 65              # composite risk /100: only corridors in real trouble
RISK_WATCH = 45             # moderate; below this, level alone is not an alert
MONTHLY_RISE_PTS = 5        # rolling-monitor rise over its window that counts
                            # as a trend (smaller moves are noise)
YOY_SHARE_RISE_PTS = 0.05   # top-origin share up >= 5 percentage points vs
                            # the previous scored year = concentration climbing

SEVERITY_ORDER = {"high": 0, "watch": 1}


def _alert(base: dict, severity: str, rule: str, message: str, score: float) -> dict:
    """score is only for ordering within a severity band (bigger = first)."""
    return {**base, "severity": severity, "rule": rule, "message": message,
            "score": score}


def compute_alerts(conn) -> list[dict]:
    """Standing alerts for every commodity, from the A1 fact dicts alone."""
    alerts = []
    for cid in COMMODITIES:
        facts = assemble_facts(conn, cid)
        r = facts["risk_latest"]
        tag = ("provisional, mirror-derived" if facts["provenance"]["provisional"]
               else "UAE-reported")
        base = {
            "commodity": cid,
            "name": facts["commodity"]["name"],
            "data_year": r["year"],
            "provenance": r["source"],
            "provisional": facts["provenance"]["provisional"],
        }
        share = r["top_origin_share"]
        risk = r["composite_risk"]

        # Rule 1 — single-source exposure
        if share >= CONCENTRATION_WATCH:
            severity = "high" if share >= CONCENTRATION_HIGH else "watch"
            line = CONCENTRATION_HIGH if severity == "high" else CONCENTRATION_WATCH
            alerts.append(_alert(
                base, severity, "single_source",
                f"{r['top_origin']} supplies {share*100:.0f}% of {r['year']} "
                f"import value (threshold {line*100:.0f}%) [{tag} {r['year']}]",
                share * 100))

        # Rule 2 — elevated corridor risk
        if risk >= RISK_WATCH:
            severity = "high" if risk >= RISK_HIGH else "watch"
            line = RISK_HIGH if severity == "high" else RISK_WATCH
            alerts.append(_alert(
                base, severity, "elevated_risk",
                f"composite corridor risk {risk:.1f}/100 (threshold {line}) "
                f"[{tag} {r['year']}]",
                risk))

        # Rule 3 — rising trend on the rolling monitor (direction only: the
        # monitor's level is not comparable to the annual score)
        m = facts["monthly_trend"]
        if (m and m["direction"] == "rising" and m["change"] is not None
                and m["change"] >= MONTHLY_RISE_PTS):
            alerts.append(_alert(
                base, "watch", "rising_trend",
                f"rolling monitor up +{m['change']:.1f} pts over "
                f"{m['change_window_months']} months (as of {m['latest_period']}, "
                f"threshold +{MONTHLY_RISE_PTS}) - direction signal only, level "
                f"not comparable to the annual score [mirror-based monthly monitor]",
                m["change"]))

        # Rule 4 — top-origin share up meaningfully year-over-year. Only an
        # honest "concentration climbing" when it is the SAME supplier both
        # years; if the top origin changed, that is a supply-base shift and
        # must not be phrased as one supplier concentrating.
        p = facts["risk_previous"]
        if p and p.get("top_origin_share") is not None:
            delta = share - p["top_origin_share"]
            if delta >= YOY_SHARE_RISE_PTS:
                if r["top_origin"] == p["top_origin"]:
                    alerts.append(_alert(
                        base, "watch", "concentration_climbing",
                        f"top-origin share rose to {share*100:.0f}% "
                        f"({r['top_origin']}, {r['year']}) from "
                        f"{p['top_origin_share']*100:.0f}% ({p['top_origin']}, "
                        f"{p['year']}) - +{delta*100:.0f} pts "
                        f"(threshold +{YOY_SHARE_RISE_PTS*100:.0f}) "
                        f"[{tag} {r['year']}]",
                        delta * 100))
                else:
                    alerts.append(_alert(
                        base, "watch", "top_origin_shift",
                        f"top origin shifted from {p['top_origin']} "
                        f"({p['top_origin_share']*100:.0f}%, {p['year']}) to "
                        f"{r['top_origin']} ({share*100:.0f}%, {r['year']}) - "
                        f"supply-base change, not one supplier concentrating "
                        f"[{tag} {r['year']}]",
                        delta * 100))

    alerts.sort(key=lambda a: (SEVERITY_ORDER[a["severity"]], -a["score"]))
    return alerts


def main() -> None:
    conn = get_connection()
    alerts = compute_alerts(conn)
    conn.close()

    if not alerts:
        print("No active alerts.")
        return
    for severity in ("high", "watch"):
        group = [a for a in alerts if a["severity"] == severity]
        if not group:
            continue
        print(f"{severity.upper()} ({len(group)}):")
        for a in group:
            print(f"  {a['name']:<16} {a['rule']:<24} {a['message']}")
        print()
    print(f"{len(alerts)} standing alerts across "
          f"{len({a['commodity'] for a in alerts})} commodities. "
          f"Stateless v1: recomputed from current data each run.")


if __name__ == "__main__":
    main()
