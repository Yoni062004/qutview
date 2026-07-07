# QUTVIEW Power BI Report — Build Guide

Step-by-step guide to build the Power BI report over the same data as the
Streamlit dashboard. Everything Power BI needs is exported as CSVs by:

```powershell
python scripts/export_powerbi.py     # writes data/powerbi/*.csv
```

Re-run that (after the pipeline) whenever data refreshes, then **Home →
Refresh** in Power BI picks up the new numbers. No ODBC/SQLite driver needed.

---

## 1. Prerequisites

- **Power BI Desktop** (free): Microsoft Store → search "Power BI Desktop", install.
- CSVs present in `data/powerbi/` (11 files — run the export script above if missing).

## 2. Import the data

1. Open Power BI Desktop → **Blank report**.
2. **Home → Get data → Text/CSV**, pick `data/powerbi/commodities.csv` → **Load**.
3. Repeat for the rest (or use **Get data → Folder** pointed at `data/powerbi`
   and expand each file). Tables to load:
   `commodities, annual_imports, monthly_mirror_flows, prices, annual_risk,
   monthly_risk, forecasts, backtest_metrics, dependency_ratios,
   mirror_coverage, data_provenance`.

## 3. Add real Date columns (for time axes)

Tables with a `period` text column (`monthly_mirror_flows, prices,
monthly_risk, forecasts`) need a Date. For **each** of those tables:
**Table view → select table → New column**, paste:

```dax
Date = DATE ( VALUE ( LEFT ( [period], 4 ) ), VALUE ( RIGHT ( [period], 2 ) ), 1 )
```

Set its data type to **Date** (Column tools → Data type).

## 4. Model relationships

**Model view** — create (drag `commodity_id` onto `commodity_id`), all
**one-to-many, single direction** from `commodities`:

- `commodities[commodity_id]` → `annual_imports`, `monthly_mirror_flows`,
  `prices`, `annual_risk`, `monthly_risk`, `forecasts`, `backtest_metrics`,
  `dependency_ratios`, `mirror_coverage`.

`data_provenance` stays unrelated (it feeds one footer card).

## 5. Measures (paste each: Table view → commodities table → New measure)

```dax
Latest Risk =
VAR y = MAX ( annual_risk[year] )
RETURN CALCULATE ( AVERAGE ( annual_risk[composite_risk] ), annual_risk[year] = y )
```

```dax
Latest Dependency % =
VAR y = MAX ( dependency_ratios[year] )
RETURN CALCULATE ( AVERAGE ( dependency_ratios[dependency_pct] ), dependency_ratios[year] = y )
```

```dax
Exposure Adjusted Risk = [Latest Risk] * [Latest Dependency %] / 100
```

```dax
Import Value ($M) = SUM ( annual_imports[trade_value_usd] ) / 1e6
```

```dax
Monthly Flow ($M) = SUM ( monthly_mirror_flows[trade_value_usd] ) / 1e6
```

```dax
Latest Monthly Risk =
VAR p = MAX ( monthly_risk[period] )
RETURN CALCULATE ( AVERAGE ( monthly_risk[composite_risk] ), monthly_risk[period] = p )
```

```dax
Forecast MAPE % = AVERAGE ( backtest_metrics[mape_pct] )
```

```dax
Mirror Coverage % = AVERAGE ( mirror_coverage[coverage_pct] )
```

## 6. Report pages

Use commodity **name** (from `commodities[name]`) on all axes/slicers, never
the id. Chart colors — use these in order for origins/series so the report
matches the Streamlit dashboard (CVD-safe):
`#0072B2, #D55E00, #009E73, #CC79A7, #56B4E9, #E69F00`.

### Page 1 — "Risk Overview"
- **Clustered bar chart:** Y = `commodities[name]`, X = `[Latest Risk]`,
  sorted descending. Title: "Corridor risk by commodity (latest year)".
- **Table:** `commodities[name]`, `[Latest Risk]`, `[Latest Dependency %]`,
  `[Exposure Adjusted Risk]` — sort by Exposure Adjusted Risk.
- **Cards row:** `[Exposure Adjusted Risk]` filtered to Sugar (top exposure),
  plus a card off `data_provenance[loaded_at]` (latest refresh) for honesty.
- Text box footer: "Risk = 50% origin concentration (HHI) + 30% top-origin
  share + 20% price volatility. Dependency = imports / (imports + UAE
  production), by weight (FAOSTAT)."

### Page 2 — "Monthly Corridor Monitor"
- **Slicer:** `commodities[name]` (single-select, dropdown).
- **Stacked area chart:** X = `monthly_mirror_flows[Date]`,
  Y = `[Monthly Flow ($M)]`, Legend = `monthly_mirror_flows[origin]`.
- **Line chart:** X = `monthly_risk[Date]`, Y = average of
  `monthly_risk[composite_risk]`. Fix Y axis 0–100.
- **Card:** `[Mirror Coverage %]` with title "share of UAE-reported imports
  covered by tracked origins (2023 cross-check)".
- Text box: "Mirror data: origin-reported exports to the UAE (UAE stopped
  publishing monthly customs data in 2019). Read direction and shifts, not
  absolute levels."

### Page 3 — "Prices & Forecasts"
- **Slicer:** `commodities[name]` (single-select).
- **Line chart:** X = `prices[Date]`, Y = average of `prices[price]`.
  Add `forecasts` as a second line chart beside it (X = `forecasts[Date]`,
  Y = `forecast`, with `lower_ci`/`upper_ci` as additional lines — Power BI
  has no native CI band on line charts; two thin gray lines read honestly).
- **Card:** `[Forecast MAPE %]`, title "6-month backtest error (MAPE)".

## 7. Save

Save as `docs/QUTVIEW.pbix`. The `.pbix` is a binary — commit it if you want
the repo to carry the report, or keep it local and screenshot pages for the
README instead (screenshots are friendlier for a portfolio repo).

## Troubleshooting

- **Wrong types after import:** period must be Text before the Date column
  is added; `Value` columns Decimal. Fix in Power Query (Transform data).
- **Blank charts after slicing:** check the relationship direction (single,
  from commodities) and that the slicer uses `commodities[name]`.
- **Refresh fails:** the CSVs moved — Transform data → Data source settings →
  update the folder path.
