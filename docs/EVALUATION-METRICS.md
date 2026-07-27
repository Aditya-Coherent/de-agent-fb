# DeAgent — Evaluation Metrics & Baseline Comparison

**Purpose of this document.** Explain, in plain language, (1) what the *baseline
comparison* measures, (2) exactly how it works under the hood, (3) what each
evaluation metric means and when a number is "good", and (4) the actual results
for the markets tested so far.

---

## 1. What is the baseline comparison?

We generate a market **Input Sheet synthetically** — the multi-agent fleet decides
every number (segment shares, CAGR, anchor value, ASP) from the report **database +
live web evidence only**. It **never reads any number** from the human analyst's
sheet.

The baseline comparison then answers one question:

> *Starting from evidence alone, how close does the machine get to the number a
> human analyst wrote by hand?*

The human sheet is the **ground truth**. The synthetic sheet is the **prediction**.
The metrics quantify the gap between them.

> **No leakage.** The synthetic generator only borrows *labels* (dimension /
> segment / geography names) so the two sheets are comparable cell-for-cell. All
> values are produced independently, so the comparison is a fair test.

---

## 2. How the comparison works (in depth)

Implemented in [`src/me_engine/generation/compare.py`](../src/me_engine/generation/compare.py),
run via [`compare_input_sheets.py`](../compare_input_sheets.py).

### Step 1 — Read both sheets into cells
Each `Data` sheet is walked row by row. A row whose column B starts with `By …`
(or `Region`) is a **dimension header**; the rows beneath it are **segments**, and
columns C onward are **geographies**. Every share value is stored under a key:

```
(segment_label, geography)  ->  share_value
```

Only values in the valid share range **0.0–1.0** are kept (so the geography sizing
block and stray numbers are ignored).

### Step 2 — Align on common ground
We take the **intersection** of the two sheets' keys:

```
overlap = { (segment, geo) present in BOTH the human and synthetic sheet }
```

This is why the header line reports, e.g., `overlapping share cells: 974
(human 1624, synthetic 975)`. Keying on **(segment, geography)** — rather than on
the dimension title — makes the comparison robust to harmless title differences
(e.g. *"By End User"* vs *"By End-use Industry"*).

> **The overlap count is the first thing to check.** A high overlap (most of the
> human cells matched) means a fair, representative comparison. A tiny overlap
> (e.g. 48 of 1624) means the two sheets describe the market with *different
> structure* — the metrics are then meaningless, no matter how good they look.

### Step 3 — Pair up the values
For every overlapping key we form a pair `(human_actual, synthetic_predicted)`.
These pairs are the raw material for every metric.

### Step 4 — Score overall and per dimension
Metrics are computed on **all** overlapping pairs (overall) and again **grouped by
the human sheet's dimension** (per-dimension), so we can see which parts of the
segmentation the agent gets right and which it struggles with. Regional value
share and CAGR are compared separately from the `Market Value` sheet.

---

## 3. The evaluation metrics — plain-language definitions

All share metrics are on **share points** (a share of 0.42 = 42%). "Error" always
means *synthetic − human*.

| Metric | In one sentence | Formula (per cell → averaged) | Range | "Good" |
|--------|-----------------|-------------------------------|-------|--------|
| **MAE** — Mean Absolute Error | On average, how many share points is each prediction off (ignoring direction). | mean( \|pred − actual\| ) | 0 → 1 | **< 0.05** (≈5 pts) |
| **RMSE** — Root Mean Squared Error | Like MAE but punishes big misses harder — sensitive to outliers. | √ mean( (pred − actual)² ) | 0 → 1 | close to MAE (few large errors) |
| **sMAPE** — Symmetric Mean Abs. % Error | Average *percentage* error, scaled so tiny values don't blow it up. | mean( 2·\|pred−actual\| ÷ (\|pred\|+\|actual\|) ) × 100 | 0 → 200% | **< 25–35%** |
| **R²** — Coefficient of Determination | How much of the real variation the prediction explains; 1 = perfect, 0 = no better than guessing the average, negative = worse than the average. | 1 − (Σ error² ÷ Σ (actual − mean)²) | −∞ → 1 | **> 0.7** (strong), > 0.9 (excellent) |
| **Bias** | Does the model systematically over- or under-estimate? Near zero = balanced. | mean( pred − actual ) | −1 → 1 | **≈ 0** |
| **within 2 pts** | % of cells within 2 share points of the human number. | count(\|err\| ≤ 0.02) ÷ N | 0 → 100% | higher is better |
| **within 5 pts** | % of cells within 5 share points of the human number. | count(\|err\| ≤ 0.05) ÷ N | 0 → 100% | **> 60–80%** |

### Why these (and not just "% error")
Plain **Mean Relative Error** is misleading when some shares are tiny, when a few
big outliers dominate, or when cells aren't comparable. So we report a **panel**:
- **MAE / RMSE** — absolute size of the error (share points).
- **sMAPE** — percentage error that behaves well on small values.
- **R²** — whether the pattern (not just the level) is right.
- **Bias** — systematic over/under-estimation.

A result is trustworthy when **MAE and sMAPE are low, R² is high, and bias is near
zero — all at once**, on a **large overlap**.

> **One caveat on share data.** Because segment shares within a group sum to 1, the
> signed errors tend to cancel, so **bias is almost always ≈ 0 for shares by
> construction** — don't over-read a near-zero bias here. R², MAE and sMAPE are the
> informative ones.

---

## 4. Results — synthetic vs human

Two markets below are **valid** comparisons (high overlap). Both were generated
from evidence only, with zero human numbers used.

### 4a. Overall — side by side

| Market | Skeleton source | Overlap cells | MAE | RMSE | sMAPE | R² | Bias | within 5 pts |
|--------|-----------------|--------------:|----:|-----:|------:|---:|-----:|-------------:|
| **Technical Foam** | Human sheet (reference mode) | 974 / 1624 | 0.067 | 0.090 | 34.8% | **0.768** | −0.000 | 52% |
| **Process Analyzer** | Neon DB (any-market mode) | 729 / 1091 | **0.047** | 0.087 | **26.9%** | 0.750 | −0.027 | **79%** |
| Retail Self-Scanning † | Human sheet (hierarchical) | 1360 | 0.100 | 0.134 | 48.5% | 0.636 | −0.000 | 34% |

† Retail is a *hierarchical* market (deeply nested share-of-parent). It is harder
and is shown for context, not as a headline result.

### 4b. Technical Foam — per dimension

| Dimension | Cells | MAE | sMAPE | R² | within 5 pts | Read |
|-----------|------:|----:|------:|---:|-------------:|------|
| **By Product Type** | 598 | 0.061 | 36.9% | **0.868** | 51% | Strong — pattern well captured |
| **By Density** | 130 | 0.039 | 23.6% | 0.742 | 64% | Good |
| **By Form** | 130 | 0.054 | 28.7% | 0.585 | 58% | Moderate |
| **By Distribution Channel** | 116 | 0.143 | 43.3% | 0.415 | 39% | Weak — only a 2-way split, coarse |
| **Overall** | 974 | 0.067 | 34.8% | 0.768 | 52% | Solid evidence-only baseline |

### 4c. Process Analyzer — per dimension

| Dimension | Cells | MAE | sMAPE | R² | within 5 pts | Read |
|-----------|------:|----:|------:|---:|-------------:|------|
| **By Sampling Type** | 40 | 0.013 | **3.1%** | **0.993** | 100% | Near-perfect |
| **By Technology** | 100 | 0.017 | 10.7% | 0.875 | 97% | Excellent |
| **By Analyzer Type** | 180 | 0.048 | 26.3% | 0.884 | 56% | Strong |
| **Region (value share)** | 49 | 0.021 | 18.0% | 0.958 | 94% | Excellent |
| **By End-use Industry** | 220 | 0.091 | 53.7% | −0.44 | 69% | Weak — 11 flat segments, hardest to split |
| **Overall** | 729 | 0.047 | 26.9% | 0.750 | 79% | Best overall result so far |

---

## 5. How the agents arrive at the numbers

The numbers are **not** guessed in one shot. A LangGraph fleet of specialist agents
each decides one family of numbers **from evidence**, a reward loop refines them,
and a "brain" agent records *why*. The flow:

```
structure → market_size → geo → shares ⇄ reward → finalize → asp → brain
```

| Agent | What it decides | Evidence it uses | How it decides |
|-------|-----------------|------------------|----------------|
| **structure** | (no numbers) builds the evidence brief | Neon DB segmentation + market narrative; live web snippets | Fetches the report, parses the segmentation tree, runs web searches on size/regions/pricing |
| **market_size** | Global 2026 value, overall CAGR, per-region value share + CAGR | DB narrative (e.g. "USD 3,982.7 Mn in 2026 … CAGR 13.7%") + web | Adopts the DB figure when present, adjusts with reasoning; regional split from evidence |
| **geo** | Each country's share of its region + its CAGR | Region membership + evidence | Larger economies get larger shares; reflects market maturity |
| **shares** | For every sibling group, a share vector that sums to 1 | Evidence brief + the reviewer's critique from the previous loop | Assigns each segment a share of its parent, grounded in the evidence |
| **reward** | A score (0–1) + a targeted critique, per dimension | The proposed shares + market context | Consistency check (do groups sum to 1?) + an LLM skeptic rating plausibility |
| **finalize** | — | best score seen | Restores the **best-scoring** shares across all loop iterations |
| **asp** | Average selling price per product | Evidence + domain pricing knowledge | Sets realistic relative prices for every priced product |
| **brain** | The **artifacts** (the "why") | All decided numbers + evidence | Poses why-questions and answers them with drivers + current events |

**Core design principle.** Agents emit **judgements + reasoning**, never raw
arithmetic. All cell math (expanding a share vector to 34 country columns, rolling
up children into parents, `volume = value ÷ ASP`) is done by deterministic code.
This means a single bad LLM answer can't corrupt the workbook's internal
consistency — and every number is traceable to an agent decision.

**The reward loop ("RL-style" refinement).** After `shares` proposes numbers,
`reward` scores them and writes a *per-dimension critique* ("segment X is
over-weighted, shift it down"). That critique feeds back into `shares` for another
attempt. The loop runs a minimum number of times and keeps the **best** result —
so the numbers improve rather than drift.

---

## 6. Artifacts — the reasoning behind the numbers

Every generated sheet carries an **Artifacts** tab: for each headline number, a
why-question, an answer, the **drivers**, and the **current events** the agent
weighed. These are pulled verbatim from real runs.

### Process Analyzer  (anchor **$9,714.7 Mn**, CAGR **6%**, reward 0.73)

| Number explained | The agent's reasoning | Current events weighed |
|------------------|-----------------------|------------------------|
| **Overall growth (CAGR 6%)** | Driven by industrial automation, stricter environmental regulation, and demand for real-time quality control. | Net-zero emissions targets; digital transformation in manufacturing |
| **Asia Pacific highest CAGR (7.5%)** | Rapid industrialization/urbanization in China & India plus infrastructure and renewable-energy investment. | Supply-chain diversification; manufacturing expansion in Asia |
| **North America largest share (35.6%)** | Heavy automation investment and real-time analytics in oil & gas and pharma; stringent regulation. | Reshoring initiatives; environmental compliance |
| **Installation-cost barrier** | High capital + skilled-labour needs limit SME adoption; IoT/predictive-maintenance is lowering total cost. | IoT adoption; predictive-maintenance trends |

### Retail Self-Scanning  (anchor **$3,982.7 Mn**, CAGR **13.7%**, reward 0.78)

| Number explained | The agent's reasoning | Current events weighed |
|------------------|-----------------------|------------------------|
| **Overall growth (CAGR 13.7%)** | AI/RFID/IoT adoption to speed checkout and cut labour cost; contactless-payment demand post-pandemic. | Post-pandemic contactless shift; retail labour shortages driving automation |
| **Asia Pacific highest CAGR (16%)** | Rapid urbanization, retail expansion and high smartphone penetration outpace North America's mature, upgrade-cycle market. | China's smart-retail/cashless push; India's UPI & digital-payment growth |
| **Hardware priced above software** | Hardware carries R&D, manufacturing and install costs (multi-plane scanners, smart carts); software scales at low marginal cost. | Supply-chain disruptions raising hardware cost; cloud-software subscriptions rising |

**How to read an artifact.** Each row is the agent's *audit trail* for one number:
the question shows what was being decided, the reasoning shows the causal logic, and
the current-events column shows the real-world context that nudged the figure. This
is what turns "a number the model produced" into "a number with a defensible,
inspectable justification" — the core goal of the system.

---

## 7. How to read a comparison (checklist)

1. **Overlap first.** Is the overlap a large fraction of the human cells? If it is
   tiny, stop — the sheets are structurally different and the metrics don't mean
   anything (this is what a wrong skeleton produces).
2. **R² for the pattern.** > 0.7 means the agent captured the shape of the
   segmentation, not just the average.
3. **MAE / sMAPE for the size.** Low = the actual share *levels* are close.
4. **within-5-pts for practicality.** "What fraction of numbers are within 5
   points of the analyst?" is the most intuitive headline.
5. **Per-dimension to find the weak spot.** Broad, flat dimensions with many
   near-equal segments (e.g. *End-use Industry*, *Distribution Channel*) are the
   hardest and drag the overall number down.

---

## 8. Reproduce

```bash
# Generate (reference mode: mirror a human sheet's real structure, numbers fresh)
python generate_synthetic_input.py \
  --market "Global Technical Foam Market" \
  --skeleton "my_inputs/Data File - Global Technical Foam Market.xlsx" \
  --output  "agentGeneratedInputSheet/SYN - Global Technical Foam Market.xlsx"

# Compare synthetic vs human
python compare_input_sheets.py \
  --human     "my_inputs/Data File - Global Technical Foam Market.xlsx" \
  --synthetic "agentGeneratedInputSheet/SYN - Global Technical Foam Market.xlsx" \
  --save-report "output/compare-technical-foam.json"
```

Or use the web app (backend: `python backend/main.py`, frontend: `npm run dev` in `frontend/`) → generate → **Baseline compare** tab.
