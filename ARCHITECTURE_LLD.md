# Low-Level Architecture — Desk Research, Demand-Side, and Supply-Side Pipelines

Three independent market-sizing methodologies live in this repo:

- **Desk Research / Secondary Research** (`src/me_engine/generation/`) — every
  number is cited from, or estimated against, already-published material (a
  DB report or live web search results). This was previously (mis)labelled
  "Demand-Side" in this repo's docs/comments; the underlying agent fleet and
  its business logic are unchanged, only the name is corrected.
- **Demand-Side / Bottom-Up Usage-Based** (`src/me_engine/demand_usage/`) —
  the market size is *constructed* from underlying demand: usage/application
  buckets → demand drivers → quantities → prices → values, per geography,
  summed up. This is the pipeline that earns the name "demand-side": it never
  looks up an already-published market-size figure.
- **Supply-side** (`src/me_engine/supply/`, bottom-up from the companies that
  sell into the market). "Supply-side" and "bottom-up" are not two separate
  things — bottom-up (sum individual companies' revenue from the ground up,
  per country, then across countries) *is* how a supply-side estimate gets
  produced; it is the technique, not a third, separate methodology. A single
  global total is nothing more than `Σ(country_total)` over that same
  breakdown, so there's no separate "single-total" pipeline sitting next to
  it either — that was an earlier, now-retired duplication (see history note
  below).

Each pipeline computes its own number **independently, in full**, from its
own inputs. Only *after* the numbers already exist does a separate,
comparison step read them side by side — nothing feeds into another
pipeline's computation. Desk Research's number is used only as an
*informational reference* inside the Demand-Side pipeline's validation step
(logged, never used to override its bottom-up total) and inside
`compare_demand_supply.py`'s relative-error report against supply-side.

```
 ┌─────────────────┐              ┌───────────────────────┐
 │  Desk Research    │              │   Company sourcing    │
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

 ┌───────────────────────────────────────────────────────────────────┐
 │                Demand-Side / Bottom-Up Usage-Based                 │
 │            (src/me_engine/demand_usage/, LangGraph graph.py)       │
 │  market_understanding -> geography -> usage_decomposition ->       │
 │  demand_driver -> demand_quantification -> pricing -> calculate    │
 │  (deterministic) -> validation                                     │
 │  validation reads Desk Research's total from generation_history    │
 │  as a REFERENCE ONLY -- never feeds back into its own total.       │
 └───────────────────────────────────────────────────────────────────┘
```

Supply-side's own graph reuses only the **company-sourcing utilities** in
the same package (`me_engine.supply.companies` / `discovery` / `verify`) —
reusable plumbing, not Desk Research business logic. It never reads Desk
Research's Input Sheet, ME workbook, or state to compute its own number —
`generation_history.jsonl` is touched only at the very end, to print how far
apart the two already-finished numbers are. The Demand-Side pipeline follows
the same discipline: it only reuses generic infrastructure (`GenLLM`,
`GenConfig`, web evidence search, the local embedding utility, and the
history log) and never imports Desk Research's or supply-side's business
logic.

**History note:** this repo briefly had three sibling pipelines under the
single label "demand-side" and "supply-side" — a single-global-total
"supply-side" (plain functions, no graph), and a separate "bottom-up"
(country-wise, LangGraph) pipeline. A mentor review correctly pointed out
that split was wrong: bottom-up *is* the supply-side technique, not a peer of
it, and a single total is just the country-wise breakdown summed up. The two
were merged — `me_engine/bottomup/` was folded into `me_engine/supply/`, and
`bottomup_pipeline.py` was retired in favor of `compare_demand_supply.py`,
which now runs the country-wise LangGraph graph and reports both the
per-country detail and the headline total. Separately, the *original*
"demand-side" agent fleet (`src/me_engine/generation/`) was later recognised
to actually be Desk Research (it cites/estimates from already-published
material, it never builds demand from first principles) and was relabelled
accordingly; the name "Demand-Side" was reserved for the new, genuinely
bottom-up, usage-based pipeline in `src/me_engine/demand_usage/`.

---

## 1. Desk Research pipeline (formerly labelled "Demand-Side")

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
- Live web evidence (`web_evidence.py`, `ddgs` multi-backend search) — best-effort, degrades to `[]` silently on failure.

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

**Logging:** `src/me_engine/generation/history.py` → `output/generation_history.jsonl`, one line per run (`run_id`, `market_name`, `global_anchor_2026`, `global_cagr`, reward, etc.), merged by `run_id` across the Input-Sheet and ME-workbook generation steps. Rows from this pipeline have no `methodology` field (legacy); rows from the Demand-Side pipeline are tagged `"methodology": "demand_side_usage"` so the two are distinguishable in the same log.

**Export for sharing:** `export_market_json.py` — reads an already-generated Input Sheet `.xlsx` (Data + Market Value sheets only, no live state) and reshapes it into one hierarchical JSON: Global revenue → Region → {countries, segments (value-weighted from country-level shares)}.

---

## 2. Demand-Side pipeline (bottom-up, usage-based)

**Entry point:** `demand_usage_pipeline.py` (CLI).

**Core: LangGraph `StateGraph`** — `src/me_engine/demand_usage/graph.py`

```
market_understanding -> geography -> usage_decomposition -> demand_driver
    -> demand_quantification -> pricing -> calculate -> validation -> END
```

- **State** (`UsageDemandState`, TypedDict): `market_name`, `config`, `llm`, `log`,
  `market_definition`, `countries`, `usage_buckets`, `overlap_flags`, `drivers`,
  `quantities` (country × bucket → `{value, unit, provenance, note}`), `prices`,
  `usage_values` (deterministic), `country_totals` (deterministic),
  `total_market_value` (deterministic), `validation`, `quality_flags`.
- **Nodes** (`src/me_engine/demand_usage/agents.py`):
  - `market_understanding_node` — defines the market's real boundaries (scope), grounded where evidence supports it.
  - `geography_node` — proposes the country/geography scope for the bottom-up build (not borrowed from Desk Research).
  - `usage_decomposition_node` — discovers this market's own mutually-exclusive usage/application buckets from evidence; never a generic default list. Deterministic embedding-similarity check flags (never silently merges) likely double-counted buckets.
  - `demand_driver_node` — per bucket, identifies the single measurable real-world driver of its demand.
  - `demand_quantification_node` — per bucket, per country, quantifies that driver with an explicit provenance tag: `observed` (cited figure) → `derived` (regional/global figure, transparently allocated, basis stated) → `assumption` (no real figure exists, stated plainly) — precision is never fabricated.
  - `pricing_node` — per bucket, an ASP per unit of the driver, grounded where evidence supports it.
  - `calculate_node` — **deterministic, no LLM call**: `usage_value = quantity × price` per bucket × country, summed into country totals and the global total.
  - `validation_node` — 40% deterministic (geography coverage, arithmetic re-check, overlap-flag presence) + 60% LLM judgement (double-counting, missing usage categories, overall plausibility); logs Desk Research's total for the same market as a reference only.

**Evidence sources:** live web evidence (`me_engine.generation.web_evidence.gather`, reused as generic infra) and the local sentence-embedding utility (`me_engine.generation.semantic_search.embed`, reused for the overlap check) — no Neon DB dependency in v1.

**Output** (`src/me_engine/demand_usage/buildup_writer.py`):
- `write_buildup_json(state, path)` — the full auditable chain: Country → Usage → Driver → Quantity (+ provenance) → Price → Value, plus validation and quality flags.
- `write_buildup_workbook(state, path)` — the same chain as an `.xlsx` (`Build-Up` / `Summary` / `Validation` sheets), provenance colour-coded (observed / derived / assumption).

**Logging:** same `output/generation_history.jsonl` via `me_engine.generation.history`, tagged `"methodology": "demand_side_usage"` per entry so it's distinguishable from Desk Research rows sharing the same log file.

**No refine loop in v1** — a single linear pass proves the methodology first; a reward-style refinement loop (as Desk Research's `reward_node` has) can be added later if single-pass plausibility scores show it's needed.

---

## 3. Supply-side pipeline (bottom-up, country-wise)

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
  15–25 entry country/region breakdown for the market. Never reads Desk Research's
  pipeline's Input Sheet or any other file — this was an explicit correction mid-build
  (an earlier version read Desk Research's geography list directly; that coupling was removed).
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
  single figure is what gets compared against Desk Research's total; there's no separate
  single-total code path.

Compared against `generation_history.jsonl`'s `global_anchor_2026` for the same market name
(or `--desk-research-value` override) → relative error %.

**Auto-saved outputs** (no flag needed):
- `my_companies/<market-slug>_discovered.csv` — the sourced/verified company list.
- `output/supply_side/<market-slug>.json` — full report: per-country breakdown, every
  company's per-country revenue + share, grand total, and (if a Desk Research run for
  that market exists) the relative error vs. it.

---

## 4. Running each pipeline

```bash
# Desk Research (web app, recommended)
python backend/main.py            # terminal 1
cd frontend && npm run dev        # terminal 2 -> http://localhost:3000

# Desk Research (CLI)
python run_pipeline.py --input "my_inputs/Data File - X.xlsx" --output "my_outputs/ME-X.xlsx"

# Export a generated Input Sheet to hierarchical JSON
python export_market_json.py --market "Global Smart Phone Market"

# Demand-Side / Bottom-Up Usage-Based
python demand_usage_pipeline.py --market "Global Retail Self Scanning Solutions Market" \
    --output "output/demand_usage/Retail Self-Scanning"

# Supply-side (bottom-up, own geography agent proposes countries)
python compare_demand_supply.py --market "Global Adhesives & Bonding Market" --discover

# Supply-side with an explicit country list and an existing company file
python compare_demand_supply.py --market "Global Smart Phone Market" \
    --companies "my_companies/global_smart_phone_market_discovered.csv" \
    --countries "United States,China,India,Germany,Japan"
```

---

## 5. File map

```
src/me_engine/generation/     Desk Research agent fleet (graph.py, agents.py, sheet_writer.py, db_source.py, ...)
src/me_engine/demand_usage/   Demand-Side (bottom-up usage-based) agent fleet: graph.py, agents.py,
                               buildup_writer.py, quality.py
src/me_engine/assembly/       assembler.py — ME workbook math (rollups, band totals)
src/me_engine/io/             workbook read/write (me_writer.py, me_builder.py, workbook_inspector.py)
src/me_engine/validation/     gate1.py, gate3.py
src/me_engine/supply/         supply-side pipeline: companies.py, discovery.py, verify.py (sourcing),
                               geography.py + estimator.py (tool functions) + graph.py (LangGraph)

backend/main.py                FastAPI web app backend (Desk Research only)
run_pipeline.py                Desk Research CLI
demand_usage_pipeline.py       Demand-Side (bottom-up usage-based) CLI
compare_demand_supply.py       supply-side CLI (the one and only entry point for it)
export_market_json.py          Input Sheet -> hierarchical JSON exporter
compare_me_workbooks.py        manual ME-workbook-vs-reference accuracy check (separate from all of the above)

my_companies/                  sourced/discovered company lists (.csv)
output/generation_history.jsonl   shared run log for Desk Research AND Demand-Side (methodology field
                                   distinguishes rows); only file compare_demand_supply.py reads from
                                   the other pipelines
output/demand_usage/              Demand-Side build-up artifacts (.json + .xlsx)
output/supply_side/               supply-side reports
output/market_json/               export_market_json.py output
```
