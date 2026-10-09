"""PDF export and markdown-escaping smoke tests."""
import ast
import io

import pytest
from pypdf import PdfReader

from brief import corridor_brief as cb
from brief.brief_pdf import _split_brief, build_brief_pdf
from conftest import PROJECT_ROOT

M = 1e6


def _load_md_safe():
    """md_safe lives in app/dashboard.py, which runs the whole Streamlit app on
    import. Pull just that function out of the source instead."""
    src = (PROJECT_ROOT / "app" / "dashboard.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "md_safe")
    ns = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "dashboard.py", "exec"), ns)
    return ns["md_safe"]


md_safe = _load_md_safe()


@pytest.fixture
def mutton_facts(db, score):
    """'Sheep & goat meat' exercises XML escaping; provisional year exercises the
    provisional footnote."""
    flows = {"Australia": 300 * M, "Kenya": 60 * M, "India": 40 * M}
    db.add_flows("fact_imports", 2023, "mutton", flows)
    db.add_flows("fact_imports_mirror_annual", 2023, "mutton", flows)
    db.add_coverage("mutton", 2023, 92.0)
    db.add_flows("fact_imports_mirror_annual", 2024, "mutton",
                 {"Australia": 330 * M, "Kenya": 50 * M})
    score()
    conn = db.connect()
    f = cb.assemble_facts(conn, "mutton")
    conn.close()
    return f


def _pdf_text(pdf: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) >= 1
    return "\n".join(p.extract_text() for p in reader.pages)


def test_build_brief_pdf_produces_a_valid_pdf(mutton_facts):
    text = cb.build_template_brief(mutton_facts)
    pdf = build_brief_pdf(mutton_facts, text, "template", "forced offline",
                          "2024 (provisional)")
    assert pdf.startswith(b"%PDF-")
    assert pdf.rstrip().endswith(b"%%EOF")
    body = _pdf_text(pdf)
    assert "Sheep & goat meat" in body       # escaped for XML, rendered literally
    assert "Australia" in body
    assert "$" in body                       # currency survives (no md_safe here)
    assert "\\$" not in body
    assert "provisional" in body.lower()
    assert "Recommended for review" in body


def test_pdf_handles_missing_optional_facts(facts_factory):
    f = facts_factory(share=0.7, risk=60)
    f["top_origins"] = [{"origin": "Brazil", "value_usd": 70 * M, "share": 0.7}]
    f["total_import_value_usd"] = 100 * M
    pdf = build_brief_pdf(f, cb.build_template_brief(f), "template", "test", "2024")
    body = _pdf_text(pdf)
    assert "not available" in body  # dependency / exposure gaps shown, not crashed
    assert "No price forecast available" in body


def test_split_brief_extracts_sections(mutton_facts):
    narrative, action, caveats = _split_brief(cb.build_template_brief(mutton_facts))
    assert narrative and not narrative.startswith("DRAFT")
    assert action and "Recommended" not in action
    assert caveats and "Analyst decision" not in caveats


@pytest.mark.parametrize("raw, escaped", [
    ("+$42M ... $107M", "+\\$42M ... \\$107M"),
    ("$5", "\\$5"),
    ("no currency", "no currency"),
    ("", ""),
])
def test_md_safe_escapes_dollar(raw, escaped):
    assert md_safe(raw) == escaped


def test_md_safe_leaves_no_unescaped_dollar_pair(mutton_facts):
    out = md_safe(cb.build_template_brief(mutton_facts))
    assert "$" in out
    assert all(out[i - 1] == "\\" for i, ch in enumerate(out) if ch == "$")
