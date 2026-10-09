# PROJECT_STRUCTURE.md — DeAgent Market-Sizing Repo

The single orientation doc for humans and AI agents working in this repo. It covers
what every folder holds, what each pipeline does, how the pipelines relate, and how to
run them. For deeper design detail, follow the links in [§9 Docs index](#9-docs-index).

_Last mapped: 2026-09-24 (HEAD `32b1142`)._

---

## 1. What this repo is

DeAgent estimates the size of a market (USD Mn, by geography × segment × year) with
**three independent methodologies**. Each one computes its own number end to end. A
number is compared with the others only **after** it is finished. None of them feeds
another's computation.

| # | Approach | Idea | Engine package | Entry point(s) |
|---|---|---|---|---|
| 1 | **Desk Research** (top-down, secondary research). Previously mislabelled "demand-side". | Cite or estimate numbers from published material (CMI report DB + web), write an **Input Sheet**, then expand it into a full **ME (Market Estimation) workbook** | `src/me_engine/generation/` (Input Sheet) + `curve/`, `io/`, `assembly/`, `validation/` (ME workbook) | Web app tab "Desk Research"; `generate_synthetic_input.py`; `run_pipeline.py` |
| 2A | **Demand-Side, country total** (bottom-up to global, segments top-down) | One total-demand line per country (quantity × price) → region → global; every segment split top-down. Reuses 2B's sizing graph. | `src/me_engine/demandCountry_approach2A/` + `demand_topdown/` | `demand_country_2a_pipeline.py` (CLI only) |
| 2 / 2B | **Demand-Side, usage-based** (bottom-up) | Build the market from demand: use cases → drivers → quantities → prices → value. It never looks up a published market size. Phases 4–5 (`segments` / `report`) keep Application bottom-up and split other segments top-down. | `src/me_engine/demandUseCaseCountry_approach2B/` + `demand_topdown/` | Web app tab "Demand Usage" (phases 1–3); `demand_usage_pipeline.py` |
| 2C | **Demand-Side, product × application × country** | Not implemented; design notes only | `src/me_engine/demandProductUseCase_approach2C/` | — |
| 2b | **Demand-Side, India variant** | Same as #2 with geography fixed to India and one extra level: Major Use Case → Actual Use Case | `src/me_engine/demand_usage_india/` (reuses #2) | Web app tab "Demand India"; `demand_usage_india_pipeline.py` |
| 3 | **Supply-Side** (bottom-up by company) | Sum named companies' per-country revenue, gross up by a coverage fraction, then sum over countries. Also offers an "evidence" method that takes the median of web-cited market sizes and uses no LLM. | `src/me_engine/supply/` | `compare_demand_supply.py` (CLI only, no UI tab) |
| — | **Segmentation Demo / trade-evidence experiments** (vinegar prototype) | UN Comtrade trade data, apparent consumption (production + imports − exports), and a per-leaf deterministic calculator | `src/me_engine/segmentation_demo.py`, `trade_evidence.py`, `apparent_consumption.py`, `demand_calculator.py` | Web app tab "Segmentation Demo"; `artifacts/build_india_vinegar.py` |

```
Desk Research ──► Input Sheet.xlsx ──► ME workbook.xlsx
      │ logs global_anchor_2026
      ▼
output/generation_history.jsonl ◄── read ONLY as a reference by ──┬─ Demand-Side validation (flag only)
                                                                   └─ compare_demand_supply.py (relative-error report)
```

---

Plain-language guide to every approach: `approaches/PRESENTER_GUIDE.txt`.

## 2. Top-level layout

```
de-agent-fb/
├── src/me_engine/            ALL engine code (Python package; scripts add src/ to sys.path)
├── backend/                  FastAPI server that wraps the engine (port 8000)
├── frontend/                 Next.js 16 / React 19 web UI (port 3000)
├── tests/                    pytest suite (~42 files, mostly demand_usage)
├── docs/                     EVALUATION-METRICS.md/.docx (Input Sheet scoring vs human sheet)
├── artifacts/                build_india_vinegar.py + its JSON output
├── ppt/                      presentation walkthroughs, one per approach (index.html); build_ppt_artifacts.py regenerates them
├── approaches/               PRESENTER_GUIDE.txt + examples/ (five-industry results from run_examples.py)
├── schemas/                  legacy segmentation schemas (JSON); only olive_oil.json is loaded
├── my_inputs/                human-authored Input Sheets ("Data File - X.xlsx"), used as skeletons/baselines
├── my_reference/             human ground-truth ME workbooks ("ME - X.xlsx"), used for accuracy checks
├── my_companies/             company lists for Supply-Side (.csv/.xlsx/.docx)
├── synthetic/                one early synthetic Input Sheet (Cyber-Physical Systems Security)
├── agentGeneratedInputSheet/ [gitignored] Input Sheets produced by Desk Research
├── agentGeneratedMEWorkbook/ [gitignored] ME workbooks produced by Desk Research
├── my_outputs/               [gitignored] ME workbooks written by CLI runs
├── output/                   [gitignored] all run artifacts, caches, history (see §7)
├── *.py (root)               CLI entry points and utilities (see §6)
├── *.md (root)               design docs (see §9)
├── Input Sheet - Avocado Oil Market.xlsx   original pilot Input Sheet
├── ME - Global Avocado Oil Market.xlsx     pilot ground-truth ME; the canonical curve/premium priors were derived from it
├── vinegar.txt               scratch notes: vinegar Segmentation Demo result + Comtrade "Revenue = Volume × Price" reasoning
├── smoke_test_data_5000.json synthetic demand-usage fixture used by run_pipeline_smoke_test.py
├── requirements.txt          Python deps
├── .env / .env.example       API keys and config (never commit .env)
├── .mcp.json                 Claude Code MCP config: the code-review-graph server
├── .claude/                  Claude Code settings, hooks (graph auto-update) and skills
├── .code-review-graph/       code-review-graph's knowledge-graph data
└── .vscode/settings.json     points VS Code at the .venv Python interpreter
```

---

## 3. Approach 1 — Desk Research (`src/me_engine/generation/` + ME core)

### 3.1 Flow

**Stage A: Input Sheet generation (LangGraph)**

1. **Skeleton** (labels only, no numbers):
   - From the Neon DB (`dynamic_skeleton.load_skeleton_from_db`): LLM extraction from segmentation HTML, with a regex fallback.
   - Or from a human sheet (`skeleton.load_skeleton_from_human_sheet`).
2. **Phase 0 gate** (web only): the user submits a base-year segment definition (`segment_definition.py`). The approved shares bypass the LLM.
3. **Graph** (`graph.build_graph`): `structure → market_size → geo → shares → asp → reward`, then either loop back to `market_size` or `finalize → brain → END`.
   - Loop bounds: `DEAGENT_MIN/MAX_LOOP_ITERS`, `DEAGENT_REWARD_THRESHOLD`.
   - `finalize` keeps the best iteration: a pass grounded in a cited market size always beats an ungrounded one (reward breaks ties; ungrounded passes get a -0.15 reward penalty). If ungrounded values differ >2× across passes, the median is used and `market_size_unstable` is flagged.
4. **Output:** `sheet_writer.write_input_sheet` → `agentGeneratedInputSheet/Data File - <market>.xlsx`, with sheets Data, ASP, Market Value and Artifacts. A row is appended to `output/generation_history.jsonl`.

**Stage B: Input Sheet → ME workbook (`run_pipeline.py`, always run as a subprocess via `generation/me_runner.py`)**

1. `WorkbookInspector.inspect`: detect the sheets, dimensions, geographies and ASP products.
2. `build_taxonomy_from_workbook`: build the in-memory `RuntimeTaxonomy`.
3. **Gate 1** `InputValidator`: hard stop on missing CAGR/anchor, shares ≠ 1, or ASP ≤ 0.
4. `InputDriverBuilder`: CurveAgent per geography, SegmentationAgent per dimension and AspAgent produce a `DriverSet`.
5. `Assembler`: deterministic identities (segment = total × share; volume = value / ASP × 1000).
6. `MEWorkbookWriter(template)` if a template is given, otherwise `MEWorkbookBuilder`.
7. **Gate 3** `OutputValidator`: identity checks, plus a diff against `--reference` if one is supplied.

The output is 3 bands (Value / ASP / Volume) × 13 years (2021–2033) per geography sheet.

### 3.2 Modules

**`generation/`: Input Sheet generator**

| File | Purpose |
|---|---|
| `config.py` | `GenConfig.load()`: DeepSeek/OpenAI/OpenRouter key, model, flags |
| `llm.py` | `GenLLM`: JSON chat client with SQLite cache (`output/.gen_cache.db`). **Shared by every pipeline.** |
| `graph.py` | `GenState`, `build_graph()`, `run_generation()` |
| `agents.py` | Graph nodes: structure, market_size, geo, shares, asp, reward, finalize, brain |
| `skeleton.py` / `dynamic_skeleton.py` | `Skeleton` dataclass; builders from a human sheet or the DB |
| `db_source.py` | Neon Postgres `cmi_reports` lookup (keyword LIKE + pgvector semantic) and LLM match verification |
| `semantic_search.py` | Local MiniLM embeddings (384-d). Also used by demand_usage. |
| `web_evidence.py` | `ddgs` web snippets (`gather`, `gather_with_sources`) and `fetch_page_text`. Reused by demand_usage and India. |
| `cited_values.py` | Cited market-size figures: regex extraction (unit + year; page title / URL slug as subject when the sentence names none), 2024/2026 → 2025 at the cited CAGR, scope check (keyword, or LLM against `--scope`), units × ASP size check (>3× outside rejected), source tiers (research-report pages + CMI DB anchor; other sites corroborate), outliers >2.5× from the median dropped, median = anchor (disputed → value nearest the size range). Flags: `DEAGENT_TITLE_SUBJECT`, `DEAGENT_SIZE_CHECK`, `DEAGENT_SOURCE_TIERS`, `DEAGENT_WEAK_CONSENSUS` |
| `segment_definition.py` | Phase 0 base-year gate (deterministic) |
| `sheet_writer.py` | Writes the Input Sheet xlsx |
| `me_runner.py` | Runs `run_pipeline.py` as a subprocess and parses the gate status |
| `history.py` | `output/generation_history.jsonl` read/append |
| `quality.py` | Sanity flags on generated values (incl. `ungrounded_market_size`, `market_size_unstable`, `cited_values_disputed`, `final_value_outside_size_range`) |
| `compare.py` | Input Sheet vs human sheet metrics (MAE, RMSE, sMAPE, R², bias) |

**`curve/`: Stage-B agents.** Uses its **own** LLM client (httpx, OpenRouter/OpenAI only). If only a DeepSeek key is set, these agents run offline on canonical priors.

| File | Purpose |
|---|---|
| `agent.py` | `CurveAgent`: growth-curve shape per geography |
| `seg_agent.py` + `segmentation.py` | Segment growth premiums → drifting share paths |
| `asp_agent.py` | ASP inflation per product |
| `evidence_agent.py` | Optional web evidence (`ME_ENABLE_WEB_EVIDENCE=1`) |
| `canonical.py`, `canonical_premiums.py` | Priors reverse-engineered from the avocado ME workbook |
| `shape.py`, `runner.py`, `score.py` | Curve maths, a batch runner, scoring against the truth ME |
| `llm.py`, `config.py`, `cache.py` | Curve LLM client, config and SQLite cache |

**ME core**

| Package | Contents |
|---|---|
| `io/` | `workbook_inspector.py`, `input_reader.py`, `input_drivers.py` (→ `DriverSet`), `layout.py` (ME sheet row/column geometry), `me_writer.py` (template fill), `me_builder.py` (from scratch) |
| `assembly/` | `assembler.py` (deterministic maths), `model.py` (`MarketResult`, `GeographyResult`, …) |
| `domain/` | `runtime_taxonomy.py` (main path), `taxonomy.py` (legacy defaults, loads `schemas/olive_oil.json`, which actually contains avocado data), `drivers.py`, `series.py`, `schema_loader.py` |
| `validation/` | `gate1.py` (input), `gate3.py` (output + reference diff) |
| `tools/` | `extract_schema.py`: Input Sheet → schema JSON (writes `schemas/*.json`) |
| `audit/` | `events.py`, `store.py` (`AuditEmitter` → `audit_log.jsonl`), `redact.py`. Used by demand_usage only. |

---

## 4. Approach 2 — Demand-Side usage-based (`src/me_engine/demandUseCaseCountry_approach2B/`)

### 4.1 Flow

Three phases. Each phase ends at a **human approval gate**.

| Phase | Command | What runs | Output file |
|---|---|---|---|
| 1 Setup | `setup` → `approve-setup` | `market_setup_draft_node`: scope, geography, base year, units | `00_setup_draft.json` → `00_setup_approved.json` |
| 2 Taxonomy | `taxonomy` → `approve-taxonomy` | usage_decomposition → scope_taxonomy_validation → usage_validation → usage_coverage → demand_driver (with an evidence strategy) | `01_taxonomy_draft.json` → `01_taxonomy_approved.json` |
| 3 Size | `size` | LangGraph: demand_quantification → pricing → **calculate** (deterministic) → driver_magnitude_validation → triangulation → magnitude_cross_validation → validation (the last three only raise flags) | `02_sizing.json`, `02_sizing.xlsx`, `SIZE_RUN_RESULT.md` |

Before any file is written, `correctness_gate.run_pre_write_gate` runs: hard failures abort the run, soft issues become flags. Every fact is tagged with a verification status: `verified_observed`, `derived`, `source_found_unverified` or `assumption`.

Run folder: `output/demand_usage/<slug>/` (also holds `.size.lock`, `02_sizing.progress.json` and `audit_log.jsonl`).

### 4.2 Modules

| Group | Files |
|---|---|
| Phases / orchestration | `setup.py`, `taxonomy.py`, `graph.py` (`build_sizing_graph`, `run_sizing_phase`), `agents.py` (~2.3k lines: all LLM nodes + `calculate_node`) |
| Evidence acquisition | `evidence_router.py` (LLM-ranked source plan, then dispatch), `web_acquirer.py`, `pdf_acquirer.py` (pdfplumber), `tabular_acquirer.py` (CSV/XLSX), `api_acquirer.py` (JSON APIs), `asset_verifier.py` (content sniffing), `web_search.py` (ddgs with URLs) |
| Evidence acceptance | `evidence.py` (`verify_and_tag`: the claimed value must appear in the source text), `fact_validator.py` (rejects wrong metric/geo/year/unit) |
| Strategy | `evidence_strategy.py` (per-driver plan), `pricing_strategy.py` (price basis/period, circularity) |
| Maths | `derivation.py` (derived facts, never tagged verified), `conversion.py`, `units.py` (unit-contract gate), `reference_data.py` (population fallback) |
| Validation | `magnitude.py`, `triangulation.py`, `magnitude_validation.py`, `quality.py` (provenance flags), `correctness_gate.py` |
| Run plumbing | `checkpoints.py` (paths/json), `progress.py`, `run_lock.py`, `run_report.py`, `buildup_writer.py` (xlsx/json) |
| Cross-market memory | `driver_library.py` → `output/demand_usage/driver_library.json` |

The phase-by-phase design history (Phases 0–12 plus an addendum) is in `DEMAND_SIDE_EVIDENCE_PLAN.md`. The Phase 0 baseline numbers are in `output/demand_usage/BASELINE_V3.md`.

### 4.3 India variant (`src/me_engine/demand_usage_india/`)

It imports `demandUseCaseCountry_approach2B` and never modifies it. The hierarchy is
`Market → Major Use Case → India → Actual Use Case → driver → quantity → price → value`.

| File | Purpose |
|---|---|
| `setup.py` | Reuses the shared setup node and forces `geography=["India"]` |
| `taxonomy.py` | Shared nodes + `india_actual_use_case_node`, then demand_driver |
| `actual_use_case_agents.py` | The only new LLM node. It rejects generic dimensions (Product, Channel, …) posing as use cases. |
| `graph.py`, `aggregation.py` | Shared sizing, then `rollup_major_use_cases` (pure arithmetic) |
| `buildup_writer.py`, `checkpoints.py` | Adds Major Use Case rollup sheets; outputs go to `output/demand_usage_india/<slug>/` |

The size lock is keyed on `"<market> (India)"`, so an India run never collides with a global run of the same market.

---

## 5. Approach 3 — Supply-Side (`src/me_engine/supply/`)

**Company method** (LangGraph `graph.py`): `geography → companies_revenue → coverage → aggregate`.
- Country total = named-company revenue ÷ coverage fraction.
- Global total = Σ country totals.

**Evidence method** (`evidence_estimator.py`, no LLM): ddgs "market size / CAGR" snippets, filtered by embedding similarity, regex-extracted, median taken, then a Google Trends nudge.

| File | Purpose |
|---|---|
| `companies.py` | Load/save company lists (company-finder CSV/XLSX, JSON, TXT) |
| `discovery.py` | LLM + web discovery of 15–40 companies |
| `verify.py` | HTTP checks on company websites (catches invented companies) |
| `financials.py` | yfinance + InsightSentry/RapidAPI (`RAPIDAPI_KEY`) for real revenue |
| `geography.py` | LLM proposes 15–25 countries + "Rest of" buckets |
| `estimator.py` | Per-company country split (capped at real revenue) and coverage estimate |
| `graph.py` | `run_supply_side()` |
| `evidence_estimator.py` | The evidence method |
| `quality.py`, `web_search.py` | Flags; ddgs wrapper |

The CLI is `compare_demand_supply.py`. It writes `output/supply_side/<slug>.json` and flags `disagrees_with_desk_research` when the relative error against the Desk Research anchor exceeds 40%.

---

## 6. Root scripts

| Script | Approach | Status | What it does |
|---|---|---|---|
| `run_pipeline.py` | Desk (Stage B) | **Core.** The backend calls it via subprocess. | Input Sheet → ME workbook (Gate 1 → agents → assemble → Gate 3) |
| `generate_synthetic_input.py` | Desk (Stage A) | Current, but lags the web app (no Phase 0 gate, no history row) | CLI Input Sheet generation. `--scope "<definition>"` drives the search name, DB lookup and LLM scope check; with no usable DB report it falls back to a generic skeleton |
| `run_all.py` | Desk | Batch utility | `run_pipeline.py` over a whole folder |
| `run_pilot.py` | Desk | Legacy pilot (hard-coded avocado files, `default_taxonomy`) | The original accuracy pilot |
| `export_market_json.py` / `export_me_json.py` | Desk | **Imported by `backend/main.py`. Don't move or rename.** | Input Sheet / ME workbook → hierarchical JSON |
| `compare_input_sheets.py` | Desk | Eval tool | Synthetic vs human Input Sheet |
| `compare_desk_vs_actuals.py` | Desk | Eval tool | Desk global 2025 value vs `actuals_2025.csv`: % error vs median + `in_published_range` (±5%); `--run` reruns the 7 example markets; `--eval` reports all 'ok' markets across `output/desk_eval/{before,current,after}` with the fixed tuning/held-out split (`approaches/examples/DESK_EVAL_SPLIT.json`) |
| `compare_me_workbooks.py` | Desk | Eval tool | Generated vs reference ME, cell by cell |
| `backfill_report_embeddings.py` | Desk | Maintenance | Fills the pgvector `embedding` column in Neon |
| `test_llm.py` | Desk (curve) | Stale. Tests only the curve LLM client. Not a real pytest test: running bare `pytest` at the root may make a live call. | LLM connectivity check |
| `demand_usage_pipeline.py` | Demand | Current | Phases setup / approve-setup / taxonomy / approve-taxonomy / size |
| `demand_usage_india_pipeline.py` | Demand India | Current | Same commands, India variant |
| `demand_country_2a_pipeline.py` | Demand 2A | Current | setup / driver / size / segments / report, each with an approve step |
| `run_pipeline_smoke_test.py` + `generate_smoke_test_data.py` | Demand | Dev tools | Feed `smoke_test_data_5000.json` through `calculate_node` |
| `compare_demand_supply.py` | Supply | **The only Supply-Side entry point** | Runs supply-side and compares it with Desk Research |
| `artifacts/build_india_vinegar.py` | Experiment | Standalone, no LLM | Comtrade + World Bank nested demand, 20k-trial Monte Carlo (P10/P50/P90) |

---

## 7. `output/` (gitignored) — what lives where

| Path | Written by |
|---|---|
| `generation_history.jsonl` | Desk Research + Demand-Side (a `methodology` field tells them apart). This is the only cross-pipeline file. |
| `.gen_cache.db`, `.supply_cache.db` | LLM response caches (SQLite) |
| `demand_usage/<slug>/` | Demand-Side runs (avocado-oil, cement, crude-oil, crude-oil-india, global-bottled-water-market, vinegar, test) |
| `demand_usage/driver_library.json` | Cross-market driver reuse |
| `demand_usage/BASELINE_V3.md`, `PHASE12_FINAL_VALIDATION.md` | Validation write-ups |
| `demand_usage/_*_backup/`, `_*_logs/` | Snapshots taken during regression work (safe to prune once no longer needed) |
| `demand_usage/Global *.json/.xlsx` (flat) | Pre-V3 outputs (legacy) |
| `demand_usage_india/<slug>/` | India runs |
| `supply_side/*.json` | `compare_demand_supply.py` |
| `market_json/*.json` | `export_market_json.py` / `export_me_json.py` |
| `trade_evidence/*.json` | UN Comtrade cache (`trade_evidence.py`) |
| `segmentation_demo/` | Segmentation Demo output + HTML/.docx walkthroughs |
| `bottomup/`, `bottomup-*.json`, `compare-*.json`, `me_avocado_report.json` | Legacy outputs from the retired `me_engine/bottomup/` package (merged into `supply/`) |

---

## 8. Web app

**Start:**
```bash
python backend/main.py                   # http://127.0.0.1:8000
cd frontend && npm install && npm run dev  # http://localhost:3000  (NEXT_PUBLIC_API_BASE in frontend/.env.local)
```

**Backend** (`backend/`, FastAPI; CORS allows only localhost:3000; the run store is in memory and lost on restart):

| File | Prefix | Feature |
|---|---|---|
| `main.py` | `/api/*` | Desk Research: `generate` (SSE), `generate-me`, `compare`, `segment-definition/*`, `history`, `download/*` |
| `demand_usage_routes.py` | `/api/demand-usage` | setup / taxonomy generate+approve, `size/generate` (SSE), download |
| `demand_usage_india_routes.py` | `/api/demand-usage-india` | Same, India |
| `audit_routes.py` | `/api/audit` | Read-only run/event/timeline/lineage over the demand_usage audit logs |
| `segmentation_demo_routes.py` | `/api/segmentation-demo` | generate, trade-evidence, calculate, apparent-consumption, calculator-config |

**Frontend** (`frontend/src/`): a single page (`app/page.tsx`) with four tabs: `desk-research`, `demand-usage`, `demand-india`, `segmentation-demo`. All HTTP calls go through `lib/api.ts`, and types live in `lib/types.ts`.
- Desk tab: `page.tsx`, `DeskSegmentDefinitionPanel`, `ResultsTabs`, `LiveLog`
- `DemandUsageWizard`, `DemandIndiaWizard`
- `SegmentationDemo` → `SegmentationStepWizard` → `TradeEvidencePanel`, `CalculationChain`, `ApparentConsumptionPanel`, `FormulaMechanism`, `ProductApplicationMatrix`, `SegmentationTree`
- `components/audit/*` is a full audit dashboard that **is not wired into `page.tsx`**. It is currently orphaned.
- `frontend/AGENTS.md`: Next.js 16 has breaking changes. Read `node_modules/next/dist/docs/` before editing.

---

## 9. Docs index

| Doc | Read it for |
|---|---|
| `README.md` | Desk Research quick-start, `run_pipeline.py` flags |
| `ARCHITECTURE_LLD.md` | How the three methodologies are separated, and the naming history |
| `PROJECT-STATUS.md` | Gate/agent architecture and avocado accuracy results (dated June 2026) |
| `AGENT-FLEET.md`, `CURVE-AGENT.md` | Curve/seg/ASP agent design; how the growth curve was reverse-engineered |
| `SPEC-stage1-assembler.md` | ME workbook layout and assembler identities |
| `PILOT-RESULTS.md` | Pilot accuracy vs the original ME |
| `DEMAND_SIDE_EVIDENCE_PLAN.md` | Demand-Side Phases 0–12: the evidence contract and acquirers |
| `UI.md` | Pipeline Demo UI design notes |
| `docs/EVALUATION-METRICS.md` | Metrics for synthetic vs human Input Sheets |
| `output/demand_usage/BASELINE_V3.md` | Demand-Side baseline run numbers |

---

## 10. Configuration & external services

`.env` (template: `.env.example`):

| Var | Used by |
|---|---|
| `DEEPSEEK_API_KEY` → `OPENAI_API_KEY` → `OPENROUTER_API_KEY` | `GenLLM` (all pipelines). Default model `deepseek-chat`, set via `DEAGENT_MODEL` / `DEAGENT_LLM_BASE_URL` |
| `OPENROUTER_API_KEY` / `OPENAI_API_KEY`, `ME_CURVE_MODEL`, `ME_LLM_BASE_URL` | `curve/` agents only (DeepSeek not supported; without a key they run offline) |
| `NEON_DATABASE_URL` | Desk Research report DB (Postgres + pgvector) |
| `ME_ENABLE_WEB_EVIDENCE`, `ME_EVIDENCE_*`, `EVIDENCE_WORKERS` | Web evidence toggles |
| `DEAGENT_MIN/MAX_LOOP_ITERS`, `DEAGENT_REWARD_THRESHOLD`, `DEAGENT_PER_REGION_SHARES` | Desk graph loop (note: `.env.example` sets `PER_REGION_SHARES=0`, but the code default is `1`) |
| `RAPIDAPI_KEY` | Supply-side InsightSentry financials |

**External services:**
- LLM (DeepSeek / OpenRouter / OpenAI)
- `ddgs` web search
- Neon Postgres
- Local sentence-transformers
- UN Comtrade public API
- World Bank
- yfinance, pytrends

---

## 11. Tests

Run from the repo root with `pytest tests`. `tests/conftest.py` adds `src/` to the path, and the route tests also need `backend/` importable.

- **Demand-Side** (majority): setup, taxonomy, evidence router/strategy, acquirers, fact validator, derivation, magnitude, triangulation, pricing, units, checkpoints, run lock/report, regression fixes
- **India:** aggregation, setup, taxonomy
- **Desk:** `test_desk_*`
- **Core ME:** `test_pipeline_core.py`
- **Backend routes:** `*_routes`, `test_desk_generate_route.py`, `test_audit_api_routes.py`
- **Audit:** `test_audit_events.py`

---

## 12. Rules of thumb for agents

- Keep the methodologies independent. `demand_usage` and `supply` may reuse **infrastructure** from `generation/` (`GenLLM`, `GenConfig`, `web_evidence`, `semantic_search`, `history`), but never its business logic.
- `demand_usage_india` extends `demand_usage` by import. Don't fork or modify the shared modules for India-only behaviour.
- The LLM researches and extracts. Code does the arithmetic (`calculate_node`, `assembler.py`, `aggregation.py`).
- Don't rename the root `export_*_json.py` or `run_pipeline.py`. The backend imports or execs them.
- This project has a code-review-graph MCP server (see `CLAUDE.md`). Prefer its tools for structural questions.
