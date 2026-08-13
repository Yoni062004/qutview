"""Render a per-corridor intelligence brief to a clean, printable PDF.

The realistic way this product spreads inside a customer organisation is a
procurement contact forwarding a document to their director — so the artifact
has to look professional and open anywhere. Pure-Python (reportlab): it
installs on Streamlit Community Cloud with no system binaries, unlike
weasyprint / wkhtmltopdf.

Honesty discipline, unchanged from the rest of QUTVIEW: every figure comes
from the assemble_facts() fact dict passed in — nothing is recomputed or
re-derived here, so the grounding guarantee is preserved. Provenance labels
and caveats carry through verbatim; a provisional (mirror-derived) year is
labelled provisional in the PDF exactly as in the app (via the same
vintage string the app builds).

Currency renders literally: "$" is safe in reportlab — the Streamlit
markdown-LaTeX problem does not exist in this layer — so md_safe() (which is
only for the markdown renderer) must NOT be applied to this text, or it would
leave stray backslashes. The one escaping this layer needs is XML-special
characters (&, <, >) inside Paragraphs — e.g. the commodity "Sheep & goat
meat" — handled by escape() at the render boundary.
"""
from datetime import date
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (HRFlowable, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

_DISCLAIMER = ("Decision support — recommends, does not decide. "
               "Generated from public data (UN Comtrade, World Bank, FAOSTAT).")
_URL = "qutview.streamlit.app"

# Line markers mirror the format contract of corridor_brief.py (both the
# deterministic template and the LLM system prompt emit these exact prefixes),
# so parsing the brief into sections is reliable across both modes.
_M_RECOMMENDED = "Recommended for review:"
_M_CAVEATS = "Confidence & caveats:"
_M_DECISION = "Analyst decision:"


def _fmt_m(value_usd) -> str:
    """USD -> '$107.0M'. Same value/1e6 scaling the brief uses — no new
    number, just a display unit."""
    return f"${value_usd / 1e6:,.1f}M"


def _split_brief(text: str):
    """Pull (narrative, recommended-action, caveats) out of the generated
    brief. Drops the brief's own header line(s) — the PDF has its own header —
    and the trailing analyst-decision placeholder (the PDF renders a live
    decision block in the app, not here)."""
    lines = text.splitlines()

    def find(prefix):
        return next((i for i, ln in enumerate(lines)
                     if ln.strip().startswith(prefix)), None)

    rec_i = find(_M_RECOMMENDED)
    conf_i = find(_M_CAVEATS)
    dec_i = find(_M_DECISION)
    dec_i = dec_i if dec_i is not None else len(lines)
    body_end = rec_i if rec_i is not None else (conf_i if conf_i is not None else dec_i)

    # Skip the brief's DRAFT header line, and the template's second header line
    # ("<name> · data year <year>") when present. The LLM brief has only the
    # one header line, so this stays correct for both modes.
    start = 0
    if lines and lines[0].strip().startswith("DRAFT"):
        start = 1
    if len(lines) > start and "data year" in lines[start].lower():
        start += 1
    narrative = "\n".join(lines[start:body_end]).strip()

    action = lines[rec_i].partition(":")[2].strip() if rec_i is not None else ""
    caveats = ""
    if conf_i is not None:
        caveats = " ".join(ln.strip() for ln in lines[conf_i:dec_i]).partition(":")[2].strip()
    return narrative, action, caveats


def build_brief_pdf(facts, brief_text, brief_mode, brief_detail, vintage) -> bytes:
    """One clean, printable PDF for a corridor. `facts` is the assemble_facts()
    dict (the only source of figures); `brief_text` is the generated brief
    (LLM or template); `vintage` is the app's vintage_label() string so the
    data-year provenance reads identically to the dashboard."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=22 * mm,
        title=f"QUTVIEW Corridor Brief — {facts['commodity']['name']}",
        author="QUTVIEW",
    )

    dark = colors.HexColor("#1f2933")
    accent = colors.HexColor("#0072B2")
    muted = colors.HexColor("#52606d")
    rule = colors.HexColor("#d7dbe0")
    zebra = colors.HexColor("#f2f4f6")

    ss = getSampleStyleSheet()
    s_title = ParagraphStyle("qtitle", parent=ss["Title"], fontName="Helvetica-Bold",
                             fontSize=16, textColor=dark, leading=19, spaceAfter=2)
    s_sub = ParagraphStyle("qsub", parent=ss["Normal"], fontSize=9, textColor=muted,
                           leading=12, spaceAfter=1)
    s_h = ParagraphStyle("qh", parent=ss["Heading2"], fontName="Helvetica-Bold",
                         fontSize=10.5, textColor=accent, leading=13,
                         spaceBefore=11, spaceAfter=4)
    s_body = ParagraphStyle("qbody", parent=ss["Normal"], fontSize=9.5,
                            leading=13.5, textColor=dark, spaceAfter=3)
    s_small = ParagraphStyle("qsmall", parent=ss["Normal"], fontSize=8,
                             leading=11, textColor=muted, spaceAfter=2)
    s_lbl = ParagraphStyle("qlbl", parent=ss["Normal"], fontSize=8.5,
                           leading=11, textColor=muted)
    s_val = ParagraphStyle("qval", parent=ss["Normal"], fontName="Helvetica-Bold",
                           fontSize=9.5, leading=12, textColor=dark)
    s_cell = ParagraphStyle("qcell", parent=ss["Normal"], fontSize=9,
                            leading=12, textColor=dark)
    s_num = ParagraphStyle("qnum", parent=s_cell, alignment=TA_RIGHT)
    s_numh = ParagraphStyle("qnumh", parent=s_lbl, alignment=TA_RIGHT)

    def P(text, style=s_body):
        return Paragraph(escape(str(text)), style)

    r = facts["risk_latest"]
    c = facts["commodity"]
    prov = facts["provenance"]
    story = []

    # 1. Header -----------------------------------------------------------
    story.append(P("QUTVIEW — Corridor Intelligence Brief", s_title))
    story.append(P(f"{c['name']}  ·  HS {c['hs_code']}", s_sub))
    story.append(P(f"Data year {vintage}  ·  generated {date.today().isoformat()}", s_sub))
    story.append(HRFlowable(width="100%", thickness=0.6, color=rule,
                            spaceBefore=5, spaceAfter=2))

    # 2. Headline metrics -------------------------------------------------
    story.append(P("Headline metrics", s_h))
    dep = facts.get("dependency")
    exp = facts.get("exposure_adjusted_risk")
    dep_s = f"{dep['dependency_pct']:.0f}%" if dep else "not available"
    exp_s = f"{exp:.1f} / 100" if exp is not None else "not available"
    top_s = f"{r['top_origin']} ({r['top_origin_share'] * 100:.0f}%)"
    metric_rows = [
        ("Composite risk", f"{r['composite_risk']:.1f} / 100",
         "Origin concentration (HHI)", f"{r['hhi']:.2f}"),
        ("Top origin (share)", top_s, "Distinct origins", str(int(r["n_origins"]))),
        ("Import dependency", dep_s, "Exposure-adjusted risk", exp_s),
    ]
    mt = Table([[P(a, s_lbl), P(b, s_val), P(cc, s_lbl), P(d, s_val)]
                for (a, b, cc, d) in metric_rows],
               colWidths=[40 * mm, 47 * mm, 42 * mm, 45 * mm])
    mt.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 1),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, rule),
    ]))
    story.append(mt)

    # 3. Supply mix -------------------------------------------------------
    story.append(P("Supply mix — top origins", s_h))
    rows = [[P("Origin", s_lbl), P("Import value", s_numh), P("Share", s_numh)]]
    for o in facts["top_origins"]:
        rows.append([P(o["origin"], s_cell), P(_fmt_m(o["value_usd"]), s_num),
                     P(f"{o['share'] * 100:.0f}%", s_num)])
    stbl = Table(rows, colWidths=[92 * mm, 42 * mm, 40 * mm])
    stbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, muted),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, zebra]),
    ]))
    story.append(stbl)
    story.append(P(f"Total reported import value: {_fmt_m(facts['total_import_value_usd'])}.",
                   s_small))

    # 4. Price & forecast -------------------------------------------------
    story.append(P("Price & forecast", s_h))
    lp, fc, bt = facts.get("latest_price"), facts.get("forecast"), facts.get("backtest")
    bits = []
    if lp and lp.get("price") is not None:
        bits.append(f"Latest price {lp['price']:.2f} {c['price_unit']} ({lp['period']}).")
    else:
        bits.append("Latest price not available.")
    if fc:
        bits.append(f"6-month forecast: {fc['direction']} "
                    f"({fc['pct_vs_latest_price']:+.1f}% by {fc['end_period']}).")
    else:
        bits.append("No price forecast available.")
    if bt:
        bits.append(f"Backtested MAPE {bt['mape_pct']:.1f}% over {bt['horizon_months']} months.")
    story.append(P(" ".join(bits)))

    # 5. Intelligence brief (the generated body, incl. "What moved") ------
    narrative, action, caveats = _split_brief(brief_text)
    story.append(P("Intelligence brief", s_h))
    src = ("LLM-generated · " + brief_detail if brief_mode == "llm"
           else "Template (deterministic, no AI) · " + brief_detail)
    story.append(P(f"Brief source: {src}. Draft for human review, not a decision.", s_small))
    for para in (ln for ln in narrative.split("\n") if ln.strip()):
        story.append(P(para))

    # 6. Recommended for review ------------------------------------------
    if action:
        story.append(P("Recommended for review", s_h))
        story.append(P(action))

    # 7. Confidence & caveats --------------------------------------------
    story.append(P("Confidence & caveats", s_h))
    if caveats:
        story.append(P(caveats))
    if prov["provisional"]:
        story.append(P("This data year is provisional (mirror-derived): reconstructed from "
                       "partner countries' reported exports to the UAE (FOB values), not UAE "
                       "customs figures — treated as directional, not settled, and labelled "
                       "provisional throughout.", s_small))

    # 8. Footer (every page) ---------------------------------------------
    def _footer(canvas, doc_):
        canvas.saveState()
        y = 14 * mm
        canvas.setStrokeColor(rule)
        canvas.setLineWidth(0.5)
        canvas.line(doc_.leftMargin, y + 5 * mm, A4[0] - doc_.rightMargin, y + 5 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(doc_.leftMargin, y + 1 * mm, _DISCLAIMER)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(doc_.leftMargin, y - 3 * mm, f"Page {doc_.page}")
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.setFillColor(accent)
        canvas.drawRightString(A4[0] - doc_.rightMargin, y - 3 * mm, _URL)
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
