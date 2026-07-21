# QUTVIEW — Product Guide

A plain-English tour of the whole product: what every part does, why it exists,
and what it's for. Read this to understand QUTVIEW well enough to explain any
piece of it unaided — in an interview, a demo, or a pitch. Every number below is
real and comes from the running system.

---

## 1. What QUTVIEW is, in one breath

QUTVIEW is an early-warning risk system for the UAE's food imports. It watches
where the country's staple foods come from, scores how dangerously dependent
each supply route is on a single source, forecasts price stress, and — when a
route is fragile — explains why and drafts what to do about it. It runs on
public data and is obsessively honest about what it does and doesn't know.

**The one-liner:** *"The UAE imports ~90% of its food. QUTVIEW tells you which
of those supply corridors is about to become a problem — before it does."*

---

## 2. The problem it solves

- The UAE imports **85–90% of its food**, so food security is treated as
  national security (the country holds a **4–6 month strategic reserve**).
- To manage that, you need to know **where the food comes from and how
  concentrated each route is.** If 87% of your poultry comes from one country
  and that country has a bird-flu outbreak or an export ban, you have a crisis.
- **The hidden gap QUTVIEW discovered:** the public trade record can't easily
  answer "where did the UAE's wheat come from last quarter" — **the UAE stopped
  publishing monthly customs data to the UN after 2019**, and its annual data
  lags ~18 months. So there's a visibility hole in something a government treats
  as strategic. QUTVIEW closes that hole (see mirror statistics, §4).

**Who it's for:** an anchor customer like **Silal** (Abu Dhabi's agri-food
company that manages strategic food reserves and is mandated to diversify
sources). The UAE government already monitors *retail shelf prices* downstream;
QUTVIEW is the **upstream** complement — it watches the *import corridors* that
will move those prices next.

---

## 3. The mental model: the four rungs

Everything in QUTVIEW climbs a ladder of value. This is the backbone of the
whole product — every feature answers one of these four questions:

| Rung | Question | QUTVIEW feature |
|---|---|---|
| 1. Descriptive | *What happened?* | import flows, concentration score |
| 2. Diagnostic | *Why does it matter?* | dependency, exposure-adjusted risk, "what moved" |
| 3. Predictive | *What's coming?* | price forecast, rising-trend alerts |
| 4. Prescriptive | *What do I do?* | alerts, the AI brief, diversification candidates |

Value roughly triples each rung. A tool that only does Rung 1 is a report; one
that reaches Rung 4 is a decision system. QUTVIEW reaches Rung 4.

---

## 4. The data foundation (where the numbers come from)

QUTVIEW invents nothing — every number traces to one of these public sources:

- **UN Comtrade** — the global trade database. Gives UAE **import flows by origin
  country** (who supplies what). The backbone.
- **World Bank Pink Sheet** — monthly **world commodity prices** (wheat, rice,
  soybean meal, etc.). Feeds the forecast and the volatility part of risk.
- **FAOSTAT** — UN Food & Agriculture stats. Gives **UAE domestic production**,
  so we can compute how import-dependent each food is.
- **Mirror statistics** — *the signature trick.* When the UAE doesn't report its
  own imports (post-2019 monthly, or the ~18-month annual lag), we flip the
  question: instead of "what did the UAE report importing," we read "what did
  the UAE's *suppliers* report exporting *to* the UAE." Russia, India, Brazil
  report faster, so their export reports reconstruct the recent picture.
- **The coverage gate** — the honesty rule on mirror data. We check how much of
  a *known* year the mirror captures; if it's below **75%**, we refuse to score
  newer years for that commodity rather than name a wrong top supplier. This is
  why **wheat honestly stays at 2023** (its mirror coverage is only 58% —
  Russia stopped publishing — so extending it would be misleading).

**Why this matters:** the mirror trick is what lets QUTVIEW be current at all,
and the coverage gate is what keeps it honest while doing so. Together they're a
big part of the moat — a naive dashboard would either be 18 months stale or
confidently wrong.

---

## 5. The risk score (Rung 1 → 2)

Each corridor gets a **composite risk score, 0–100**, blended from three things:

- **50% — origin concentration (HHI).** HHI (Herfindahl-Hirschman Index) is a
  0-to-1 number for "how much does this depend on one source." Near 1 = one
  supplier dominates (dangerous); near 0 = spread across many (safe).
- **30% — top-origin dependency.** The single biggest supplier's share.
- **20% — price volatility.** How much the world price bounces around.

**Real example:** poultry scores ~72/100 — because **Brazil supplies 87%** of
2025 imports (very concentrated). Wheat scores ~32/100 — because its top origin
(Russia) is only ~39%, spread across 31 countries (well diversified). The score
is per corridor, per year.

---

## 6. Provenance & honesty (the actual moat)

This is the part most tools skip, and it's what makes QUTVIEW credible:

- **Every year is labelled UAE-reported or mirror-derived.** Reconstructed years
  are marked **"provisional."** Nothing is dressed up as more certain than it is.
- **The coverage gate** (§4) refuses to score corridors it can't see clearly.
- **Grounding.** The AI brief (§10) can *only* use numbers pulled from the
  database. A mechanical check verifies every figure in the AI's text traces to
  a real value — if one doesn't, the whole output is rejected and the offline
  template is shown instead. **A fabricated number can never reach the screen.**
- **Honest forecasts.** We report the model's *real* error rate, not a claim of
  accuracy (see §7).

**The pitch line:** *"Most demos overclaim. Ours refuses to. In a food-security
context, a tool that confidently makes things up is worse than nothing — so the
whole system is built to say only what the data supports, and to flag the rest."*

---

## 7. The forecast (Rung 3)

- Each commodity gets a **6-month price forecast** using **SARIMAX** — a
  standard, explainable statistical model (not a black box).
- **Backtesting:** we pretend we're in the past, forecast forward, and measure
  how wrong we were — the **MAPE** (Mean Absolute Percentage Error). The
  dashboard shows the *real* MAPE (e.g. wheat ~12%, soybean meal ~4%) instead of
  claiming the forecast is perfect. Honest error beats a confident guess.
- The forecast carries a **90% confidence interval** — a shaded band saying
  "we're 90% sure the real value lands in here."

---

## 8. Monitoring & dependency (Rung 2 deepened)

- **Monthly corridor monitor** — a rolling 12-month risk series built from
  mirror data, so risk shifts show up within months, not once a year. (Read it
  for *direction*, not absolute level — it only tracks top corridors.)
- **Import dependency** — imports ÷ (imports + UAE production), by weight. Most
  staples are ~100% imported; a few (maize, poultry, beef) have small domestic
  cushions.
- **Exposure-adjusted risk** — corridor risk × import dependency. A concentrated
  corridor matters more when you can't grow any of it yourself.
- **The feed-dependency insight** — maize and soybean meal are both ~100%
  import-dependent (the UAE *banned* domestic fodder farming in 2006 to save
  water). So the UAE's "local" poultry, dairy, and meat production actually rests
  on imported feed — a hidden exposure sitting one layer under the food itself.
  *This is a "nobody showed us that" insight for a Silal buyer.*

---

## 9. Alerts (Rung 3 → 4)

Turns the data into a short list of "look here now," tuned so the safest
corridors stay silent (an alert feed people trust beats one they mute). Rules,
each firing only on a real threshold crossing:

- **Single-source exposure** — top origin over 80% (high) or 60% (watch).
- **Elevated risk** — composite risk over 65 (high) or 45 (watch).
- **Rising trend** — the rolling monitor climbing sharply.
- **Concentration climbing** — one supplier's share growing year-over-year.
- **Top-origin shift** — the biggest supplier *changing country* (a different
  story from one supplier growing — flagged separately so it's never confused).

Every alert names the number that tripped it and carries its provenance label.

---

## 10. The AI brief (Rung 4 — the centerpiece)

For any corridor, QUTVIEW writes a short **intelligence brief** with a
recommended action — the leap from "here are charts, you interpret" to "here's
the analysis and the suggested move."

- **Two layers, kept separate.** A deterministic *fact assembler* pulls every
  allowed number from the database into one bundle (the single source of truth).
  Then the *brief generator* writes from that bundle only.
- **Two modes.** **LLM mode** (Claude) writes the real narrative; **template
  mode** is a deterministic fallback so it always runs offline. Every LLM brief
  must pass the grounding check (§6) or it's rejected to the template.
- **Decision-support framing.** The brief is a **DRAFT for a human to approve** —
  it *recommends*, it never decides or claims an action was taken. It's headed
  "DRAFT — for procurement review" and ends with an analyst approve/revise line.
  A sovereign customer trusts "smart draft my analyst signs off on" far more than
  "AI that decides."

**Why this is the answer to "what does Abu Dhabi want from AI":** not "we bolted
on a chatbot" — an analyst-in-a-box that reads the real data, writes the brief a
junior analyst would, and *refuses to make things up.*

---

## 11. Alternative sourcing (Rung 4)

When a corridor is dangerously concentrated, QUTVIEW answers the next question —
*"who else could supply this?"* — using only origins already shipping to the UAE:

- Ranks **diversification candidates** by consistency (years present) then size.
- Shows each candidate's **implied unit value** (trade value ÷ weight) so you can
  compare suppliers — clearly labelled *not* landed cost (product mix differs;
  full landed cost needs freight/duty data we don't have yet).
- Honestly reports the *negative* case too: sugar's only real alternative (India)
  is *shrinking* — "your one backup corridor is getting smaller" is itself
  valuable to a buyer.

---

## 12. "What moved" — the drivers (Rung 2, honest attribution)

Explains *why* a corridor's risk changed, from two internal signals:

- **Price momentum** — the world price's recent move (context: "accompanied by a
  +20% 12-month price rise"). Correlation, stated as context, never as cause.
- **Flow decomposition** — the arithmetic of which origins' values rose and fell
  ("driven by India +135%").
- **The reporting-lag guard** — the sharp bit. When origins drop to exactly $0 in
  a provisional year, that's usually *not* a real exit — it's a country that
  hasn't reported yet. QUTVIEW excludes those from attribution and discloses them
  separately. This guard **caught two stories we initially believed wrong:**
  poultry's Brazil "surge" (Brazil was flat; the share rise was $210M of
  unreported suppliers) and sunflower's "supply shift" (provisional — Ukraine
  hadn't reported). *The system catches the reporting-lag trap instead of
  laundering it into a confident sentence.*

---

## 13. The commodity basket (9, across the essential categories)

Tracked today: **wheat, rice, sugar, palm oil, sunflower oil, poultry, beef,
maize, soybean meal.** These cover Silal's essential categories — cereals, oils,
sugar, protein/meat, and animal feed.

You don't need to match Silal's 350+ products — those roll up into ~13 strategic
commodity risks, and 9 already cover the essential categories. On the roadmap:
**dairy** (the #1 UAE food import) and **pulses** (a reserve staple) need a
non-Pink-Sheet price source; **barley** is honestly parked (the World Bank killed
its price series in 2020 — we refused to fake a proxy); **live sheep** is the
moat-relevant stretch (ties to the Ethiopia export corridor).

---

## 14. The two front-ends

- **Streamlit dashboard** — the live, screen-shareable app: alerts at top, risk
  cards, per-commodity detail with charts, the brief, alternatives, and "what
  moved." This is what you demo.
- **Power BI report** — a 3-page report over the same data, for a
  recruiter/portfolio audience. Same numbers, different viewer.

---

## 15. Honest limits (know these before anyone asks)

- **External data only.** QUTVIEW runs on public data. It does *not* see Silal's
  actual stock levels or contracts — the deep "your 4-month reserve + rising risk
  = act now" integration needs *their* data and only comes after they're a
  customer. Today it's an external intelligence layer, which is right for a pitch.
- **Provisional recent years.** The rock-solid numbers are UAE-reported (2023);
  2024–25 are mirror-derived and labelled provisional. Cite the solid ones as
  fact, the provisional ones with the label.
- **Implied unit value ≠ landed cost.** A supplier can look cheaper purely from a
  different product mix.
- **The LLM brief needs an API key** to run in AI mode; without it, it falls back
  to the honest template.

---

## 16. How to talk about it (the 30-second version)

*"The UAE imports about 90% of its food, so food security is national security.
QUTVIEW scores every food-import corridor for how dangerously concentrated it is
— for example, it found the UAE gets 87% of its poultry from one country, Brazil.
It forecasts price stress, alerts when a corridor is getting fragile, and drafts
the analyst brief on what to do. And critically, it's honest — it reports its
real forecast error, labels reconstructed data as provisional, and its AI can't
state a number that isn't in the database. I built the whole thing: the data
pipeline, the risk model, the AI layer, and two dashboards."*

Always lead with a *finding* (the 87% Brazil number), not the method. The method
is your answer to the follow-up question, not your opener.
