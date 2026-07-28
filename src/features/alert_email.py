"""Compose and (optionally) send the corridor-risk alert-digest email.

Turns the standing alerts (features.alerts.compute_alerts) into the email a
procurement subscriber would receive — the natural next beat after a red alert
on the dashboard. Composing is always available (the dashboard previews it);
live sending requires SMTP settings in .env (see .env.example). Same honesty
discipline as everywhere: provisional/mirror-derived alerts keep their inline
label, and the digest states plainly that it is a draft for review, not an
instruction.

Run:  python src/features/alert_email.py            (print the digest preview)
      python src/features/alert_email.py --send      (also send, if SMTP set)
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common import get_connection, get_smtp_config
from features.alerts import compute_alerts


def compose_digest(conn) -> tuple[str, str]:
    """Return (subject, plain-text body) for the current alert digest."""
    alerts = compute_alerts(conn)
    n_high = sum(a["severity"] == "high" for a in alerts)
    n_watch = sum(a["severity"] == "watch" for a in alerts)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subject = f"QUTVIEW corridor alert — {n_high} high, {n_watch} watch ({stamp})"

    lines = ["QUTVIEW — UAE Food Corridor Risk", f"Alert digest · {stamp}", "",
             f"{n_high} HIGH · {n_watch} WATCH standing alerts on current data."]
    if not alerts:
        lines += ["", "No active alerts on current data."]
    for sev in ("high", "watch"):
        group = [a for a in alerts if a["severity"] == sev]
        if not group:
            continue
        lines += ["", f"{sev.upper()} ({len(group)}):"]
        lines += [f"  - {a['name']} - {a['message']}" for a in group]
    lines += ["", "-",
              "Standing alerts computed from current trade data; provisional "
              "(mirror-derived) years are labelled inline. Draft for procurement "
              "review - QUTVIEW recommends, a human decides."]
    return subject, "\n".join(lines)


def send_digest(subject: str, body: str, cfg: dict) -> None:
    """Send the digest via SMTP (STARTTLS). Raises on failure."""
    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from_addr"]
    msg["To"] = ", ".join(cfg["to_addrs"])
    msg.set_content(body)
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as s:
        s.starttls()
        s.login(cfg["user"], cfg["password"])
        s.send_message(msg)


def main() -> None:
    conn = get_connection()
    subject, body = compose_digest(conn)
    conn.close()
    print("SUBJECT:", subject, "\n")
    print(body)
    if "--send" in sys.argv:
        cfg = get_smtp_config()
        if not cfg:
            print("\n[not sent - SMTP not configured in .env; see .env.example]")
            sys.exit(1)
        try:
            send_digest(subject, body, cfg)
            print(f"\n[sent to {', '.join(cfg['to_addrs'])}]")
        except Exception as exc:
            print(f"\n[send failed: {exc}]")
            sys.exit(1)


if __name__ == "__main__":
    main()
