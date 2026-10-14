# Demand-Side Market Sizing Agent — System Design

**Version:** 1.0 · **Date:** 2026-10-12 · **Covers:** strategies 2A, 2B, 2C
**LLM backend:** DeepSeek (hosted API or self-hosted open weights)
**Companion files:** `documentation.md` (number-level documentation), `flowchart.mermaid` (pipeline + progress)

---

## 0. Design principles (non-negotiable)

1. **LLM finds, code computes.** DeepSeek plans searches, extracts numbers, judges relevance and reviews results. It never produces a number that enters the ledger by arithmetic. Every multiplication, conversion, sum and scaling is done in Python.
2. **Every number carries a label.** `verified_observed`, `derived`, `source_found_unverified`, `assumption`, `estimated_from_facts`. No unlabelled number can be written (hard check).
3. **Checks warn; they do not silently edit.** Cross-checks against published sizes raise flags only. The only components allowed to change a number are the 2B/2C **anchor** and the **Publisher**, and both write an explicit adjustment factor to the ledger.
4. **Raw bottom-up is always reported.** Even when the Publisher blends with consensus, the unblended figure is shown beside it.
5. **Google AI Overview is a lead, never evidence.** A number seen in an AI Overview is only usable after it is found in the primary page it cites and passes the Verifier.
6. **Reproducible.** Every fetched document is snapshotted with a content hash; every LLM call is cached by prompt hash; every run has a frozen config.

---

## 1. Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│ ORCHESTRATOR  (deterministic Python state machine, not an LLM)       │
│   run → pass(1..N) → stage → country/cell task                       │
└───────┬───────────────┬──────────────────┬────────────────┬──────────┘
        │               │                  │                │
┌───────▼──────┐ ┌──────▼───────┐ ┌────────▼───────┐ ┌──────▼────────┐
│ LLM LAYER    │ │ FETCH LAYER  │ │ VERIFIER       │ │ CALC ENGINE   │
│ DeepSeek     │ │ custom       │ │ value-in-text, │ │ units, FX,    │
│ roles (§2)   │ │ scripts (§3) │ │ metric/geo/    │ │ q × p, roll-  │
│              │ │ + AI Overview│ │ year/unit (§5) │ │ ups (§6)      │
└───────┬──────┘ └──────┬───────┘ └────────┬───────┘ └──────┬────────┘
        └───────────────┴─────────┬────────┴────────────────┘
                         ┌────────▼─────────┐
                         │ EVIDENCE STORE   │  DuckDB + snapshot dir
                         │ + NUMBER LEDGER  │  (§4)
                         └────────┬─────────┘
          ┌───────────────┬───────┴──────┬──────────────┬─────────────┐
   ┌──────▼─────┐  ┌──────▼─────┐ ┌──────▼─────┐ ┌──────▼─────┐ ┌─────▼──────┐
   │ 2A / 2B /  │  │ AGENT LOOP │ │ GAP FILL   │ │ CHECKS     │ │ PUBLISHER  │
   │ 2C pipes   │  │ review/redo│ │ signal +   │ │ hard/soft  │ │ + REPORTER │
   │ (§7)       │  │ (§8)       │ │ estimator  │ │ (§11)      │ │ (§12–13)   │
   └────────────┘  └────────────┘ └────────────┘ └────────────┘ └────────────┘
```

Control flow is owned by the orchestrator. The LLM is called for bounded, schema-constrained tasks only. This keeps runs debuggable and stops the model from "deciding" to skip a check.

---

## 2. LLM layer — DeepSeek

### 2.1 Roles

| Role | Model tier | Temp | Output | Used in |
|---|---|---|---|---|
| Scoper | `fast` | 0.2 | JSON: scope, inclusions, exclusions, unit, base year | all |
| Taxonomist | `fast` | 0.3 | JSON: use cases / product types + scope verdict per item | 2B, 2C |
| Driver designer | `reasoning` | 0.2 | JSON: driver formula per country/use case, required inputs | all |
| Driver-validity judge | `reasoning` | 0.0 | JSON: does this driver measure this product? yes/no + reason | agent loop |
| Evidence planner | `fast` | 0.2 | JSON: per input → source tier, query strings, file types | all |
| Extractor | `fast` | 0.0 | JSON: candidate numbers with exact quote, metric, geo, year, unit | all |
| Metric matcher | `fast` | 0.0 | JSON: match / no-match + reason | Verifier |
| Pricing analyst | `reasoning` | 0.2 | JSON: value basis (manufacturer vs consumer), markup chain | pricing |
| Reviewer | `reasoning` | 0.3 | JSON: rubric scores + issues + redo instructions | agent loop |
| Estimator | `reasoning` | 0.2 | JSON: estimate + list of fact IDs used | gap fill |

`fast` and `reasoning` are aliases mapped in config, so model names can change without code changes.

### 2.2 Client

DeepSeek's API is OpenAI-compatible, so the standard OpenAI SDK works with a different `base_url`.

```python
# llm/client.py
import os, json, hashlib
from openai import OpenAI
from pydantic import BaseModel, ValidationError

client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url=CFG.llm.base_url)

def call_json(role: str, system: str, user: str, schema: type[BaseModel], max_retries=3):
    key = hashlib.sha256(f"{role}|{CFG.llm.models[ROLE_TIER[role]]}|{system}|{user}".encode()).hexdigest()
    if (hit := cache.get(key)): return schema.model_validate_json(hit)
    msgs = [{"role": "system", "content": system + "\nRespond only with a json object."},
            {"role": "user", "content": user}]
    for attempt in range(max_retries):
        r = client.chat.completions.create(
            model=CFG.llm.models[ROLE_TIER[role]], messages=msgs,
            temperature=ROLE_TEMP[role], response_format={"type": "json_object"})
        txt = r.choices[0].message.content
        try:
            out = schema.model_validate_json(txt); cache.put(key, txt); log_call(role, key, r.usage); return out
        except ValidationError as e:
            msgs += [{"role": "assistant", "content": txt},
                     {"role": "user", "content": f"Invalid against schema: {e}. Return corrected json only."}]
    raise LLMSchemaError(role, key)
```

Notes:

- DeepSeek's JSON mode expects the word "json" to appear in the prompt; the wrapper appends it.
- If the configured reasoning model does not support JSON mode or tool calling, the wrapper runs **think-then-format**: the reasoning model answers in prose, then the fast model converts that answer into the schema (the reasoning text is stored for audit).
- Model names, context length and feature support change between DeepSeek releases. Check the current API docs and update `llm.models` in config; nothing else needs to change.
- **Data sensitivity:** the hosted API processes prompts on DeepSeek's servers. For confidential client work, point `base_url` at a self-hosted deployment of the open-weight models (e.g., vLLM serving an OpenAI-compatible endpoint). Same code path.

### 2.3 Prompt contracts (abridged)

Extractor system prompt:

> You extract numeric facts from the supplied text. For each number, return `value_text` exactly as written, `value`, `unit`, `metric`, `geo`, `year`, and `quote` (the full sentence containing it, copied verbatim). Do not compute, convert, round or infer. If the year or geography is not stated in the text, return null. Return json.

Reviewer system prompt (market-researcher rubric, each 1–10):

> (1) Does each driver measure this product and only this product? (2) Are per-capita quantities physically plausible for each country? (3) Does the use-case mix match how the product is actually consumed? (4) Is the price basis consistent across countries? (5) What share of value rests on verified evidence? Name concrete issues and the stage that should be redone. Do **not** score on closeness to published market sizes. Return json.

---

## 3. Data fetching layer (custom scripts)

### 3.1 Fetcher interface

```python
class Fetcher(Protocol):
    tier: int                       # 1 = most authoritative
    name: str
    def search(self, need: EvidenceNeed) -> list[SourceRef]: ...
    def fetch(self, ref: SourceRef) -> RawDocument: ...
    # RawDocument: url, retrieved_at, sha256, mime, text, tables[], snapshot_path
```

### 3.2 Fetchers and source order

| Tier | Fetcher | Method | Notes |
|---|---|---|---|
| 1 | `national_stats` | One plugin per country (API, bulk CSV, or HTML/PDF scraper) | Plugin registry keyed by ISO3. Missing plugin → skip to tier 2. |
| 2 | `faostat` | FAOSTAT bulk/API: food balance sheets, production, fertilizer | Item codes mapped in `config/codes/fao_items.yaml`. |
| 3 | `comtrade` | UN Comtrade API (key required) | HS codes in `config/codes/hs.yaml`; mirror data used when reporter is missing; respects rate limits. |
| 3b | `worldbank` | World Bank Indicators API | GDP per capita, population, urbanisation for the signal model and price curve. |
| 4 | `industry` | Scrapers per body (e.g., International Olive Council for olive oil) | Each scraper declares the metrics it can return. |
| 5 | `web` | SERP API → page fetch → `pdfplumber` / `pandas` for PDF/CSV/XLSX | Generic fallback. |
| lead | `ai_overview` | SERP API that exposes Google AI Overview blocks | **Off by default.** See §3.4. |

**Order rule (2A):** for each (metric, geo, year) need, tiers are tried in order; the first tier that yields a Verifier-passed number becomes the **primary**. Lower tiers are still fetched (budget permitting) for triangulation but never override the primary.

### 3.3 Fetch hygiene

Every response is saved under `snapshots/{run_id}/{sha256}.{ext}` before parsing. HTTP cache with ETag; per-domain rate limits; `robots.txt` respected for the generic web fetcher; retries with backoff; a per-run budget for requests and for LLM tokens.

### 3.4 Google AI Overview module (optional)

Purpose: find *where* a number might live quickly, especially for niche markets where normal search is noisy.

Access: use a SERP API provider that returns AI Overview content and its cited links in structured form (some commercial SERP APIs offer this; confirm current support and terms with the vendor). Do not scrape google.com directly: it breaches Google's terms, is blocked aggressively, and breaks whenever the page layout changes.

Flow:

1. `ai_overview.query(need)` → AI Overview text + cited URLs. Snapshot both (AI Overviews change over time and are not reproducible later).
2. Extractor pulls candidate numbers from the overview text → stored as **leads** in the `leads` table, never in the ledger.
3. For each lead, fetch every cited URL with the normal tier-5 fetcher.
4. Run the Verifier against the **cited page**, not the overview. Pass → a fact whose label is set by the cited page's own tier. Fail → lead discarded with reason `not_found_in_cited_source`.
5. The AI Overview is never cited as a source in any output.

Why this is strict: an AI Overview is generated text. It can merge numbers from different years, geographies or scopes, and it can state numbers that appear in none of its citations.

Config: `ai_overview.enabled`, `max_queries_per_run`, `only_when_tiers_1_4_fail: true`.

---

## 4. Evidence store and number ledger

DuckDB file per project; snapshots on disk.

| Table | Key columns |
|---|---|
| `runs` | run_id, strategy, market, base_year, config_hash, started_at, status |
| `passes` | pass_id, run_id, n, plausibility_score, reviewer_json, selected |
| `sources` | source_id, url, publisher, tier, retrieved_at, sha256, snapshot_path |
| `leads` | lead_id, origin (`ai_overview`/`web`), value_text, cited_urls, status |
| `facts` | fact_id, source_id, value, unit, metric, geo, year, quote, verifier_result |
| `ledger` | number_id, run_id, pass_id, name, value, unit, geo, year, label, weakest_input_label, formula, input_ids[], fact_id, module, created_at, note |
| `flags` | flag_id, run_id, severity (`hard`/`soft`), check, scope, message, number_ids[] |
| `stage_status` | run_id, pass_id, stage, scope, status, started_at, ended_at |

The `ledger` is the single source of truth for every number in every output. `documentation.md` is generated from it (format in the companion file).

---

## 5. Verifier — the acceptance rule

A candidate number becomes a fact only if **all five** checks pass:

1. **Value present:** after normalisation, the value appears in the source text. Normalisation handles separators (`1,200,000` / `1.200.000`), scale words (`1.2 million`, `1.2 mn`, `12 lakh`, `0.12 crore`), and percentages. The Extractor's `quote` must be a verbatim substring of the snapshot.
2. **Metric matches:** e.g., "olive oil consumption" ≠ "olive oil production". Decided by a synonym table first, Metric-matcher LLM only for leftovers.
3. **Geography matches:** resolved to ISO3; "EU" ≠ "Spain".
4. **Year matches:** exact, or via a declared mapping (e.g., crop year 2023/24 → base year 2024, recorded in the ledger note).
5. **Unit matches:** same unit, or convertible by a declared conversion (e.g., litres → kg via density). The conversion becomes its own `derived` ledger row.

Label on pass: `verified_observed` if the source is tier 1–4 and is the primary publisher of the number; `source_found_unverified` if the source is tier 5 or is quoting another, untraced source. Any failed check → rejected, logged in `facts.verifier_result`.

---

## 6. Calculation engine

Pure Python with `pint` for units and a fixed FX table per base year (annual average, source recorded).

```python
def mul(name, a: Num, b: Num, **ctx) -> Num:
    v = a.value * b.value; u = a.unit * b.unit
    return ledger.write(name=name, value=v, unit=u, label="derived",
                        weakest_input_label=weakest(a, b),
                        formula=f"{a.id} × {b.id}", input_ids=[a.id, b.id], **ctx)
```

Rules:

- Every operation writes a `derived` ledger row with formula and input IDs.
- `weakest_input_label` propagates the least reliable input (order: `verified_observed` > `source_found_unverified` > `estimated_from_facts` > `assumption`). A total built on one assumption is reported as resting on an assumption.
- Full precision is stored; rounding happens only at display.
- Roll-ups (`cell → application → country → region → global`) assert that children sum to parent within tolerance (§11).

---

## 7. Strategy pipelines

### 7.1 Shared stages

`scope → [strategy-specific quantity build] → evidence → verify → gap-fill → price → calc → roll-up → [anchor] → cross-check → segments → checks → review → publish → report`

### 7.2 Strategy 2A — country total, then segments top-down

| Stage | Input | Output |
|---|---|---|
| Scope | brief | scope JSON; country→region map; base year; unit |
| Driver | scope | one driver per country, e.g. `consumption = production + imports − exports` (optionally `− Δstocks`, see documentation.md) |
| Evidence | driver inputs × countries | facts, tiers 1→5 |
| Gap fill | countries without facts | signal-model estimates under saturation ceiling |
| Price | facts + Comtrade unit values + GDP pc | price per unit per country |
| Calc | quantity, price | country value → regions → global |
| Cross-check | global value, published sizes | soft flag only |
| Segments | per-country shares per dimension (Product Type, Application, Channel, Form) | tables that re-add to the country total |

### 7.3 Strategy 2B — use case × country, other segments top-down

| Stage | Detail |
|---|---|
| Taxonomy | Taxonomist lists end uses; scope check drops uses that do not consume this product. |
| Drivers | One driver per use case. A driver-uniqueness check rejects any taxonomy where two use cases share an input series (prevents double counting). |
| Evidence | Per-driver evidence plan routes to web pages, PDFs, CSV/XLSX or APIs. |
| Pricing | Price per use case. Code traps: pack price applied per unit (detects when price ÷ typical pack size lands inside the observed unit-price band), and outliers (> 3× IQR from the per-use-case median). |
| Anchor | See algorithm below. |
| Segments | Application stays bottom-up; Grade, Channel, Packaging etc. top-down. |
| India variant | Extra level: Major Use Case → Actual Use Case; same rules apply one level deeper. |

Anchor algorithm (runs per country, only if a 2A run exists for the same market and base year):

```python
r = sum_use_cases[c] / total_2A[c]
if r > BAND:                                   # BAND = 1.5
    f = (BAND / r) if CFG.anchor.target == "ceiling" else (1 / r)
    scale(c, f, reason="above_2A_band")
elif r < 1 / BAND and label(total_2A[c]) == "verified_observed":
    f = ((1 / BAND) / r) if CFG.anchor.target == "ceiling" else (1 / r)
    scale(c, f, reason="below_2A_band_official")
# scale() writes one derived ledger row per scaled number plus the factor f itself
```

### 7.4 Strategy 2C — product type × application × country (build plan)

1. Reuse 2B scope, taxonomy and use-case sizing unchanged.
2. New LLM step: **Type lister** returns product types inside each application with a scope check (e.g., Extra Virgin, Virgin, Refined, Pomace).
3. New evidence need per (country, application): **volume mix** across types (shares must sum to 1.0; hard check).
4. Price per type per country; where only grade *ratios* are known, store ratios as `assumption` and apply to the use-case price.
5. Roll-up by arithmetic only: type → application → country → global.
6. Pass Application and Product Type to the top-down layer as bottom-up dimensions.
7. **Cell budget:** before research, compute each cell's expected share of the anchor total. Cells below `min_cell_share` (default 0.5%) are filled from the estimator, not researched, and labelled accordingly. This addresses the "researching the cell costs more than it is worth" problem.
8. **Coverage rule:** if recipe-built use cases explain less than `min_explained_share` of the top-down anchor (default 60%), the residual is reported as an explicit "unattributed" line, not spread silently across known use cases.

---

## 8. Agent loop (replaces human approval gates)

```
for n in 1..MAX_PASSES:
    1. Driver-validity judge: does each driver measure this product?  → fail = redesign driver
    2. Size the market (full pipeline for the strategy)
    3. Hard checks                                                    → fail = pass invalid
    4. Reviewer scores rubric (§2.3)
    5. plausibility = weighted score (below)
    6. if plausibility ≥ MIN_SCORE and no hard flags: stop
       else: feed reviewer's redo instructions into pass n+1 (targeted stages only)
select pass with highest plausibility (ties → higher verified-value share)
```

Plausibility score (0–10), weights configurable:

| Component | Weight | Measure |
|---|---|---|
| Reviewer rubric mean | 0.40 | §2.3 |
| Physical plausibility | 0.20 | share of countries with per-capita inside [floor, ceiling] |
| Evidence coverage | 0.20 | share of global value whose weakest input is `verified_observed` |
| Soft-flag count | 0.10 | 10 − penalties |
| Mix sanity | 0.10 | no use case above dominance threshold without reviewer justification |

Distance to published market sizes is **deliberately excluded**, so the loop cannot learn to copy consensus.

---

## 9. Gap filling

### 9.1 Signal model (countries with no data)

```
ln(q_pc,c) = β0 + β1·ln(GDPpc_c) + β2·urban_c + β3·ln(apparent_trade_pc_c + ε) + β4·web_score_c
q_pc,c     = min(exp(fit), CEILING_pc)
```

- Fitted on countries with verified quantities (calibration set).
- `web_score` ∈ [0, 1] is an LLM-scored strength of web evidence of consumption; the score, prompts and evidence URLs are stored.
- `CEILING_pc` is a physical saturation ceiling (e.g., highest verified per-capita in the calibration set × (1 + margin), or a dietary/physical limit when one exists).
- Reported diagnostics: n calibration countries, leave-one-out MAPE, share of global quantity that is model-filled.
- Label: `estimated_from_facts`.

### 9.2 Estimator (related markets/countries)

Uses facts already in the store for related markets or neighbouring countries. Must list the fact IDs it used. Output label `estimated_from_facts`, never verified.

---

## 10. Pricing agent

1. **Value basis** (LLM decides, with written rationale): manufacturer selling price or consumer spending. One basis per run; mixing is a hard failure.
2. **Price curve:** fit `ln(P_c) = a + b·ln(GDPpc_c)` on customs import unit values (Comtrade value ÷ net weight for the HS code), after removing unit-value outliers (> 3× IQR). Countries with a verified local price use it; others use the curve.
3. **Basis conversion:** customs unit values are border prices. Converting to consumer spending applies a markup chain (duties, distribution, retail), each step an `assumption` row unless sourced.
4. **Unit traps** caught by code: per-litre vs per-kg (needs density), pack vs unit, currency and year mismatch.

---

## 11. Checks

| Check | Severity | Rule (default) |
|---|---|---|
| Missing label / NaN / negative | hard | any |
| Sum conservation | hard | children vs parent differ > 0.1% |
| Segment re-add | hard | every segment table re-adds to the same total within 0.1% |
| Mix shares sum | hard | type or segment shares ≠ 1.0 ± 0.001 |
| Basis mixing | hard | more than one value basis in run |
| Magnitude | soft | per-capita outside [floor, ceiling] |
| Triangulation | soft | |bottom-up − published median| / published median > 25% |
| 2A vs 2B gap | soft | ratio outside [1/1.5, 1.5] after anchoring |
| Use-case dominance | soft | one use case > 50% of the market |
| Evidence coverage | soft | < 50% of value rests on verified inputs |
| Recipe coverage (2C) | soft | recipe use cases explain < 60% of anchor |

Hard failure → nothing is written to outputs; the run report lists the failure.

---

## 12. Publisher (optional, off by default)

```
B = bottom-up global value
C = median of published sizes, normalised to the same scope, year and value basis
P = w·B + (1 − w)·C                     (w = weight_bottom_up, default 0.5)
k = P / B                               price-level adjustment factor
```

`k` is applied to the price in every country; quantities are untouched. The output states B, C, P, k, w and the list of published sources. Published sizes enter as `source_found_unverified`.

---

## 13. Outputs and progress tracking

Per run, under `outputs/{run_id}/`:

- `summary.md` — headline, flags, coverage, chosen pass and why
- `tables/*.csv` and `market_tables.xlsx` — country, region, global, every segment
- `ledger.parquet` — every number
- `documentation.md` — generated from the ledger
- `progress.mermaid` — live flowchart (see below)

**Progress:** every stage writes to `stage_status`. The reporter regenerates `progress.mermaid` from `flowchart.mermaid`, assigning each node a class (`pending`, `running`, `done`, `flagged`, `failed`) and a counter like `Evidence 41/58 countries`. A CLI command `mktagent status RUN_ID` prints the same table.

---

## 14. Repository layout

```
mktagent/
  config/
    default.yaml
    codes/{hs.yaml, fao_items.yaml, country_regions.yaml}
  llm/{client.py, roles.py, prompts/, schemas.py}
  fetch/
    base.py
    national/{IND.py, ESP.py, ...}
    faostat.py  comtrade.py  worldbank.py  industry/  web.py  ai_overview.py
  verify/{normalise.py, verifier.py, synonyms.yaml}
  calc/{units.py, fx.py, engine.py, rollup.py}
  strategies/{s2a.py, s2b.py, s2c.py, anchor.py, segments.py}
  agent/{loop.py, review.py, plausibility.py}
  gapfill/{signal_model.py, estimator.py}
  pricing/{basis.py, curve.py, traps.py}
  checks/{hard.py, soft.py}
  publish/{publisher.py, reporter.py, docgen.py, progress.py}
  store/{db.py, ledger.py, snapshots.py}
  cli.py
tests/  (golden runs: olive oil 2A, olive oil 2B, vinegar India 2C)
```

---

## 15. Config (default.yaml)

```yaml
llm:
  base_url: https://api.deepseek.com      # or self-hosted endpoint
  models: {fast: deepseek-chat, reasoning: deepseek-reasoner}   # verify current names
  token_budget_per_run: 3_000_000
fetch:
  tiers: [national_stats, faostat, comtrade, industry, web]
  max_requests_per_run: 5000
ai_overview:
  enabled: false
  provider: serp_api_vendor
  max_queries_per_run: 50
  only_when_tiers_1_4_fail: true
anchor: {band: 1.5, target: ceiling}
agent: {max_passes: 3, min_score: 7.0}
checks:
  sum_tolerance: 0.001
  triangulation_warn: 0.25
  use_case_dominance: 0.50
  min_verified_coverage: 0.50
  min_explained_share_2c: 0.60
  min_cell_share_2c: 0.005
publisher: {enabled: false, weight_bottom_up: 0.5}
display: {value_sig_figs: 3, pct_decimals: 1}
```

---

## 16. Build roadmap

| Phase | Deliverable | Exit test |
|---|---|---|
| 0 | Port 2A to orchestrator + ledger + DeepSeek client | Olive oil 2A reproduces the existing run within 1% |
| 1 | Verifier with Indian number formats, snapshotting | 100% of ledger facts have a verbatim quote |
| 2 | 2B on new stack, anchor with config target | Olive oil 2B reproduces, anchor factors logged |
| 3 | Agent loop + plausibility | Pass selection independent of published sizes (unit test) |
| 4 | Signal model + estimator | LOO MAPE reported; ceiling never breached |
| 5 | AI Overview lead module | 0 ledger rows sourced to an overview |
| 6 | 2C pipeline + cell budget | Vinegar India reproduces walkthrough; unattributed residual shown |
| 7 | Publisher + progress flowchart | k stated in every published output |

---

## 17. Known risks

- **Apparent consumption ignores stock changes**, which can be large for crops with alternating harvests (olive oil). Use `− Δstocks` where a stock series exists.
- **Customs unit values are not shelf prices**; the basis decision must be explicit.
- **AI Overview drift:** the same query can return different text next week; snapshots are the only record.
- **LLM extraction errors** are contained by the verbatim-quote rule but metric mismatches with near-synonyms remain the main residual risk; maintain `synonyms.yaml` per market.
- **2C cost explosion:** cells = product types × applications × countries; the cell budget is mandatory, not optional.
