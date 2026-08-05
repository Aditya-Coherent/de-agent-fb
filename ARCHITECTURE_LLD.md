# Low-Level Architecture — Demand-Side and Supply-Side Pipelines

Two independent market-sizing methodologies live in this repo: **demand-side**
(top-down, from evidence about consumption/spend) and **supply-side**
(bottom-up, from the companies that sell into the market). "Supply-side" and
"bottom-up" are not two separate things — bottom-up (sum individual
companies' revenue from the ground up, per country, then across countries)
*is* how a supply-side estimate gets produced; it is the technique, not a
third, separate methodology. A single global total is nothing more than
`Σ(country_total)` over that same breakdown, so there's no separate
"single-total" pipeline sitting next to it either — that was an earlier,
now-retired duplication (see history note below).

Each pipeline computes its own number **independently, in full**, from its
own inputs. Only *after* both numbers already exist does a separate, final
comparison step read them side by side — nothing feeds into the other
pipeline's computation.

```
 ┌─────────────────┐              ┌───────────────────────┐
 │   Demand-side    │              │   Company sourcing    │
 │ LangGraph agents  │              │ (companies/discovery/ │
 │ + Neon DB + web   │              │       verify)         │
 └────────┬─────────┘              └──────────┬────────────┘
          │ writes                            │ feeds
          ▼                                    ▼
 Input Sheet ──► ME workbook       ┌───────────────────────┐
          │                        │      Supply-side       │
          │ logs                   │  (LangGraph graph.py)  │
          │ its total              │  bottom-up technique:  │
          ▼                        │  geography -> per-     │
 generation_history.jsonl          │  company country       │
   (global_anchor_2026)            │  revenue -> coverage -> │
          │                        │  aggregate -> ONE total │
          │                        └───────────┬────────────┘
          │                                    │ own total
          └──────────────►  ┌─────────────────────────────────────┐
                             │      Comparison (relative error)     │
                             │   compare_demand_supply.py prints    │
                             │   this LAST, after each side's own    │
                             │        number is already done         │
                             └─────────────────────────────────────┘
```

Supply-side's own graph reuses only the **company-sourcing utilities** in
the same package (`me_engine.supply.companies` / `discovery` / `verify`) —
reusable plumbing, not demand-side business logic. It never reads the
demand-side's Input Sheet, ME workbook, or state to compute its own number —
`generation_history.jsonl` is touched only at the very end, to print how far
apart the two already-finished numbers are.

**History note:** this repo briefly had three sibling pipelines — demand-side,
a single-global-total "supply-side" (plain functions, no graph), and a
separate "bottom-up" (country-wise, LangGraph) pipeline. A mentor review
correctly pointed out that split was wrong: bottom-up *is* the supply-side
technique, not a peer of it, and a single total is just the country-wise
breakdown summed up. The two were merged — `me_engine/bottomup/` was folded
into `me_engine/supply/`, and `bottomup_pipeline.py` was retired in favor of
`compare_demand_supply.py`, which now runs the country-wise LangGraph graph
and reports both the per-country detail and the headline total.

---

## 1. Demand-side pipeline

**Entry points:** `backend/main.py` (`GET /api/generate`, SSE) and `run_pipeline.py` (CLI).

**Core: LangGraph `StateGraph`** — `src/me_engine/generation/graph.py`

```
structure -> market_size -> geo -> shares -> asp -> reward
                 ^                                    │
                 └──────────── refine (loop) ──────────┤
                                                        ▼
                                          reward -> (accept) -> finalize -> brain -> END
```

- **State** (`GenState`, TypedDict): `market_name`, `skeleton`, `config`, `llm`,
  `db_evidence`, `web_snippets`, `regional`, `global_anchor_2026`, `global_cagr`,
  `geo`, `shares`, `asp`, `artifacts`, `reward`, `dim_score`/`market_score`/`asp_score`,
  `best_reward`/`best_state`, `iter`, `log`.
- **Nodes** (`src/me_engine/generation/agents.py`):
  - `structure_node` — builds the runtime taxonomy skeleton (dimensions, geographies) from DB/reference-sheet.
  - `market_size_node` — decides `global_anchor_2026` + `global_cagr`.
  - `geo_node` — per-region/country value share + CAGR.
  - `shares_node` — per-dimension segment shares (per-region or global vector, per `DEAGENT_PER_REGION_SHARES`).
  - `asp_node` — per-product ASP.
  - `reward_node` — critiques market_size/geo/shares/asp against evidence, produces `dim_score`/`market_score`/`asp_score` + textual critique.
  - `_should_refine` (conditional edge) — always refines for the first `min_loop_iters` iterations regardless of reward; after that, refines while `reward < reward_threshold`; forces accept once `iter >= max_loop_iters` no matter the reward.
  - `finalize_node` — restores the best-scoring iteration, not just the last one.
  - `brain_node` — writes `artifacts` (the "why" behind each number, for the UI's Artifacts tab).

**Evidence sources:**
- Neon Postgres `cmi_reports` (`src/me_engine/generation/db_source.py`) — LIKE match, then semantic search (`semantic_search.py`, pgvector + `sentence-transformers/all-MiniLM-L6-v2`) as fallback; `verify_market_match` does an LLM sanity check on the DB hit.
- Live web evidence (`web_evidence.py`, DuckDuckGo HTML) — best-effort, degrades to `[]` silently on failure (network-dependent, occasionally blocked in some environments).

**Output** (`src/me_engine/generation/sheet_writer.py`):
- `write_input_sheet(state, path)` → `Data` / `ASP` / `Market Value` / `Artifacts` sheets, all share/CAGR cells formatted as `0.0%` (not raw decimals). Per-cell confidence tags (High/Medium/Low/Unscored) with fill color + comment, driven by `dim_score`/`market_score`/`asp_score` + evidence presence.
- ME workbook (`run_pipeline.py`), in actual order: `WorkbookInspector` → `RuntimeTaxonomy` →
  **Gate1** (validates the *input* — halts here on failure, before any agent runs) →
  agents build a `DriverSet` → `src/me_engine/assembly/assembler.py` builds the `MarketResult`
  (deterministic identities: `segment_value = total × share`, `volume = value/ASP × 1000`, region
  rollups) → written via `MEWorkbookWriter` (borrows a template's style) with `MEWorkbookBuilder`
  fallback (from-scratch, taxonomy-driven) if the template's structure doesn't match closely enough
  (`TemplateMismatchError`) → **Gate3** (validates the *assembled/written output*, plus an optional
  reference diff) runs last.

**Logging:** `src/me_engine/generation/history.py` → `output/generation_history.jsonl`, one line per run (`run_id`, `market_name`, `global_anchor_2026`, `global_cagr`, reward, etc.), merged by `run_id` across the Input-Sheet and ME-workbook generation steps.

**Export for sharing:** `export_market_json.py` — reads an already-generated Input Sheet `.xlsx` (Data + Market Value sheets only, no live state) and reshapes it into one hierarchical JSON: Global revenue → Region → {countries, segments (value-weighted from country-level shares)}.

---

## 2. Supply-side pipeline (bottom-up, country-wise)

**Entry point:** `compare_demand_supply.py` (CLI).

**Company-sourcing layer** (`src/me_engine/supply/`):
- `companies.py` — `Company(name, facts)` dataclass; `load_companies(path, sections=)` parses `.csv`/`.xlsx` (the company-finder-agent report format: header row + `=== Section (N) ===` breaks, default-filtered to the "Manufacturer" section) or `.json`/`.txt`; `save_companies_csv()` persists any list back to the same shape.
- `discovery.py` — miniature, own-built company discovery (NOT the full vendor-intelligence tool, which takes ~30 min/market): one LLM call proposes search terms, an optional cheap web search runs, then one LLM call turns evidence (or the model's own knowledge) into a company list. Seconds, not 30 minutes.
- `verify.py` — concurrently HEAD/GETs each company's website; "reachable" vs "unreachable" is a cheap real-vs-generic-placeholder signal (catches invented names like "Avocado Oil Chile" that have no real domain).

**Core: LangGraph `StateGraph`** — `src/me_engine/supply/graph.py`

```
geography -> companies_revenue -> coverage -> aggregate -> END
```

- **State** (`SupplySideState`, TypedDict): `market_name`, `companies`, `config`, `llm`, `countries`,
  `per_company_country_revenue`, `named_totals`, `coverage_fraction`, `country_breakdown`,
  `grand_total_usd_mn`, `log`.
- **`geography_node`** (`src/me_engine/supply/geography.py`) — own LLM call proposing a
  15–25 entry country/region breakdown for the market. Never reads the demand-side
  pipeline's Input Sheet or any other file — this was an explicit correction mid-build
  (an earlier version read the demand-side geography list directly; that coupling was removed).
- **`companies_revenue_node`** (`estimator.py`'s `estimate_company_countries`) — one LLM call
  **per company** (not per company×country) asking for that company's revenue broken down
  across *every* target country at once. Keeps call count = company count, not company×country.
- **`coverage_node`** (`estimator.py`'s `estimate_coverage_by_country`) — one LLM call covering
  every country's named-supplier coverage fraction at once, given each country's summed
  named-company revenue.
- **`aggregate_node`** — per country: `country_total = named_total / coverage_fraction`;
  each company's `share = its_revenue / country_total` (so, by construction, every
  company's share plus the residual/unlisted share sums to 1 for that country — the
  "verification" property). `grand_total = Σ(country_total)` across all countries — this
  single figure is what gets compared against the demand-side total; there's no separate
  single-total code path.

Compared against `generation_history.jsonl`'s `global_anchor_2026` for the same market name
(or `--demand-value` override) → relative error %.

**Auto-saved outputs** (no flag needed):
- `my_companies/<market-slug>_discovered.csv` — the sourced/verified company list.
- `output/supply_side/<market-slug>.json` — full report: per-country breakdown, every
  company's per-country revenue + share, grand total, and (if a demand-side run for
  that market exists) the relative error vs. it.

---

## 3. Running each pipeline

```bash
# Demand-side (web app, recommended)
python backend/main.py            # terminal 1
cd frontend && npm run dev        # terminal 2 -> http://localhost:3000

# Demand-side (CLI)
python run_pipeline.py --input "my_inputs/Data File - X.xlsx" --output "my_outputs/ME-X.xlsx"

# Export a generated Input Sheet to hierarchical JSON
python export_market_json.py --market "Global Smart Phone Market"

# Supply-side (bottom-up, own geography agent proposes countries)
python compare_demand_supply.py --market "Global Adhesives & Bonding Market" --discover

# Supply-side with an explicit country list and an existing company file
python compare_demand_supply.py --market "Global Smart Phone Market" \
    --companies "my_companies/global_smart_phone_market_discovered.csv" \
    --countries "United States,China,India,Germany,Japan"
```

---

## 4. File map

```
src/me_engine/generation/     demand-side agent fleet (graph.py, agents.py, sheet_writer.py, db_source.py, ...)
src/me_engine/assembly/       assembler.py — ME workbook math (rollups, band totals)
src/me_engine/io/             workbook read/write (me_writer.py, me_builder.py, workbook_inspector.py)
src/me_engine/validation/     gate1.py, gate3.py
src/me_engine/supply/         supply-side pipeline: companies.py, discovery.py, verify.py (sourcing),
                               geography.py + estimator.py (tool functions) + graph.py (LangGraph)

backend/main.py                FastAPI web app backend (demand-side only)
run_pipeline.py                demand-side CLI
compare_demand_supply.py       supply-side CLI (the one and only entry point for it)
export_market_json.py          Input Sheet -> hierarchical JSON exporter
compare_me_workbooks.py        manual ME-workbook-vs-reference accuracy check (separate from all of the above)

my_companies/                  sourced/discovered company lists (.csv)
output/generation_history.jsonl   demand-side run log (only file the comparison step reads from the other pipeline)
output/supply_side/               supply-side reports
output/market_json/               export_market_json.py output
```
