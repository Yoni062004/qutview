# QUTVIEW Glossary

Plain-English definitions of every business, trade, data, and startup term used
across the QUTVIEW project. Doubles as interview prep — you should be able to
define any word here in your own words before a Hub71 conversation.

Each entry is one or two sentences, tied back to the project where it helps.

---

## Trade & economics

- **FX (foreign exchange)** — foreign money; converting one currency to another. "FX earnings" = the hard currency (dollars, euros) a country earns from exports.
- **FX reserves** — a country's stockpile of foreign currency, held by its central bank. Low reserves = can't pay for imports = crisis. Ethiopia's gold push is about refilling these.
- **Commodity** — a raw, interchangeable good traded in bulk: wheat, coffee, gold, sugar. One ton of a grade is the same as any other, so price is set globally.
- **Corridor** — a trade route between two countries for one good. "The UAE–Russia wheat corridor" = wheat flowing from Russia to the UAE. QUTVIEW scores these.
- **Concentration** — how much of something depends on one source. High concentration = fragile (one disruption hurts). **Diversification** is the opposite and the cure.
- **HHI (Herfindahl-Hirschman Index)** — the 0-to-1 number that *measures* concentration. Near 1 = one supplier dominates (dangerous); near 0 = spread across many (safe). QUTVIEW's core risk metric.
- **Import dependency** — the share of a good a country must buy from abroad because it can't produce enough itself. The UAE is ~85–90% import-dependent on food.
- **Net importer / net exporter** — a country that buys more of something than it sells (UAE, food) vs. sells more than it buys (Ethiopia, coffee/gold). The risk *flips meaning* depending on which one you analyze.
- **Price-taker / price-maker** — a price-taker must accept the world price (Ethiopia can't set the coffee price); a price-maker is big enough to move it. Almost everyone is a taker.
- **Volatility** — how much a price jumps around. High volatility = unpredictable = risky. One of the three risk ingredients.
- **Staple** — a basic dietary essential (wheat, rice). "Strategic staples" are the ones a government stockpiles for security.
- **Tariff** — a tax a government puts on imports. Raises costs and can disrupt trade routes.
- **Hedging** — buying financial protection against a price swing (like insurance for commodity prices).

## Trade-data specifics (the plumbing of the project)

- **Mirror statistics / mirror-gap** — when Country A doesn't report its trade, you look at what its *partners* reported trading *with* it. The **gap** between the two sides can reveal under-reporting or smuggling. QUTVIEW's signature trick.
- **Under-invoicing** — declaring a shipment as worth less than it really is (to dodge tax or move money illicitly). Shows up as a mirror-gap.
- **Re-exports** — goods that pass *through* a country and get shipped onward (lots of gold transits Dubai). They muddy trade numbers.
- **FOB / CIF** — two ways to price a shipment: **FOB** (Free On Board) = value at the exporting port, no shipping; **CIF** (Cost, Insurance, Freight) = value at the arriving port, shipping included. The two never match — a source of the mirror-gap.
- **Landed cost** — the *total* cost of a good once it arrives: price + shipping + insurance + duties. What a buyer actually pays. The alternative-sourcing feature needs this data (why it's roadmap, not done).
- **Comtrade** — the UN's global trade database. QUTVIEW's main import-flow source.
- **Pink Sheet** — the World Bank's monthly commodity-price report (nicknamed for its old pink paper). The price source; already includes coffee.
- **FAOSTAT** — the UN Food & Agriculture Organization's statistics database. The source for domestic production.

## Data science & modeling

- **Time series** — data measured over time (monthly prices). The kind of data QUTVIEW forecasts.
- **SARIMAX** — the specific forecasting model used for prices. Short version: "a standard, explainable statistical forecast — defensible, not a black box."
- **Backtest** — testing a forecast by pretending you're in the past and checking if it would've been right. Proves honesty.
- **MAPE (Mean Absolute Percentage Error)** — the score a backtest produces: "on average my forecast was off by X%." QUTVIEW reports the *real* number instead of claiming accuracy — that's the credibility.
- **Forecast horizon** — how far ahead you predict (QUTVIEW: 6 months).
- **Confidence interval (90% interval)** — the shaded band around a forecast: "we're 90% sure the real value lands in here." Honesty about uncertainty.
- **Star schema** — a clean database layout: one central "fact" table (the events) linked to "dimension" tables (the labels). How the SQLite database is organized.
- **Descriptive → diagnostic → predictive → prescriptive** — the four "rungs" of analytics: *what happened → why → what's coming → what to do*. Value roughly triples each rung. QUTVIEW is climbing to rung 4.
- **LLM (Large Language Model)** — the AI that turns data tables into written analysis. The engine of the "generative brief" feature.
- **Grounding** — forcing an AI to use *only* your real data, not its imagination. Prevents made-up numbers.
- **Hallucination** — when an AI confidently states something false. Grounding is the defense; avoiding it is the whole pitch.

## Startup & business

- **B2G / B2B / SaaS** — who you sell to: **B2G** = business-to-government (the Silal customer), **B2B** = business-to-business, **SaaS** = Software as a Service (software rented monthly online).
- **Pre-seed / seed** — the earliest funding stages. Pre-seed = idea + prototype. Seed = a bit of traction.
- **SAFE note** — a simple contract for early investment (money now, equity later). Standard pre-seed instrument.
- **Anchor customer** — the first big, credible buyer whose name validates you (Silal).
- **Moat** — your durable advantage a competitor can't copy. QUTVIEW's = origin-market ground truth from an Ethiopian network.
- **Wedge** — the narrow, specific entry point where you win first before expanding. Here = UAE food-corridor risk.
- **Traction** — evidence people actually want it (users, pilots, a demo someone reacts to).
- **Go-to-market** — the plan for *how* you reach and sell to customers.
- **Optionality** — keeping multiple future doors open at once (the analyst-job path *and* the venture path).
- **Portfolio piece** — a finished project that proves your skill to recruiters/committees.
- **Cohort** — a batch of startups admitted to a program together (Hub71 intakes).
- **Incubator / accelerator** — a program that funds and mentors early startups (Hub71).
- **Pitch / deck** — your spoken sell / the slide presentation.
- **Sovereign / sovereign cloud** — "sovereign" = controlled by the nation, data stays in-country. **Sovereign cloud** = government-controlled servers (Core42 in the UAE). Big deal for food/security data.

## Abu Dhabi / regional

- **Hub71** — Abu Dhabi's flagship startup hub (the target program).
- **AGWA** — Abu Dhabi's AgriFood, Growth & Water Abundance cluster — the government economic zone the project fits.
- **ICV (In-Country Value)** — a UAE scoring system rewarding companies that spend locally. Weighs into government contracts.
- **ESG** — Environmental, Social, Governance: sustainability/ethics reporting (Silal's "ZERO" platform is an ESG tool).
- **MRV** — Measurement, Reporting, Verification: the standard for tracking emissions (from the discarded ATHAR idea).
- **MoU (Memorandum of Understanding)** — a non-binding "we intend to work together" agreement.

## Ethiopia

- **Artisanal mining (ASM)** — small-scale, hand-tool mining by individuals, not big companies. Where most of Ethiopia's gold (and its smuggling problem) comes from.
- **Formalize / informal market** — pulling activity from the untaxed, unregulated "informal" economy into the official "formal" one. Ethiopia's gold reform in a nutshell.
- **Contraband** — smuggled goods.
- **ECX (Ethiopian Commodity Exchange)** — Ethiopia's official marketplace where coffee and other commodities are traded and graded.

---

*Add new terms here as the project introduces them. Keep definitions plain — if you can't say it in one sentence, you don't understand it yet.*
