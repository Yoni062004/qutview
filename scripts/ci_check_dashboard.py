"""CI gate for app/dashboard.py — catch what would break the Streamlit deploy.

1. Compile check: a syntax error fails here, not on Streamlit Cloud.
2. Headless render: runs the real script once with Streamlit's AppTest against
   the committed data/qutview.db (the same DB the deployed app reads), so a
   missing dependency, a broken import or an exception on first render fails
   CI before master auto-deploys.

Run:  python scripts/ci_check_dashboard.py
"""
import os
import py_compile
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = PROJECT_ROOT / "app" / "dashboard.py"


def main() -> None:
    py_compile.compile(str(DASHBOARD), doraise=True)
    print(f"compile OK: {DASHBOARD.relative_to(PROJECT_ROOT)}")

    # Never let a CI runner reach a paid API or send mail; the password gate
    # would stop the render before the app body runs.
    for var in ("ANTHROPIC_API_KEY", "SMTP_HOST", "QUTVIEW_PASSWORD"):
        os.environ.pop(var, None)

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(DASHBOARD), default_timeout=90).run()
    if at.exception:
        for exc in at.exception:
            print(f"dashboard raised: {exc.message}\n{''.join(exc.stack_trace)}")
        sys.exit(1)
    print(f"render OK: {len(at.main)} top-level elements, no exceptions")


if __name__ == "__main__":
    main()
