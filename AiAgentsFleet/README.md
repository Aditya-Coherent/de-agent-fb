# AiAgentsFleet

A standalone agent fleet: give it a market name, it fills in every numeric
field the whitepaper template (`vitamin_c_market_whitepaper_v2.docx`, see
[../DEMAND_SIDE_EVIDENCE_PLAN.md](../DEMAND_SIDE_EVIDENCE_PLAN.md) for the
broader project context) needs, by querying live web search and having an
LLM synthesize a concise structured answer per field group — then
randomizing every numeric value by **+/-5-10%**, per request.

## Important caveat — what "Google AI Overview" means here

There is **no public, ToS-compliant API for Google's AI Overview feature**,
and scraping its rendered HTML is fragile (JS-dependent, subject to
anti-bot measures) and against Google's Terms of Service. This fleet
instead reuses the multi-backend live web search already used throughout
this codebase (`me_engine.generation.web_evidence.gather`, backed by
`ddgs`, which queries Google/Bing/DuckDuckGo/Mojeek) and has an LLM
synthesize a concise, cited-style answer from those results — functionally
the same *shape* of output an AI Overview box gives (a short, confident,
multi-source-grounded answer), just not a literal scrape of that specific
Google UI element. If you specifically need Google's actual AI Overview
text (not just web-search-grounded synthesis), that would require a paid
third-party SERP API that captures it (e.g. SerpApi's `ai_overview` field)
— not wired up here; say the word if you want that added instead.

## Usage

```bash
python AiAgentsFleet/run_fleet.py --market "Global Vitamin C Market"

# only some field groups
python AiAgentsFleet/run_fleet.py --market "..." --groups market_size,regional

# more/fewer parallel agents (default 4)
python AiAgentsFleet/run_fleet.py --market "..." --workers 6
```

Requires the same `.env` this repo already uses for Desk Research
(`DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `OPENROUTER_API_KEY`) — no new
credentials needed.

Output: `AiAgentsFleet/output/<market-slug>/whitepaper_fields.json`.

## The 13 field-group agents

One agent per whitepaper section (`field_groups.py` is the single source of
truth — read it for the exact query templates and JSON schema each returns):

| Group key | Whitepaper section |
|---|---|
| `market_size` | Sec 2 — Global Market Size Outlook (base/forecast value, CAGR, full historical + forecast year series) |
| `drivers_restraints` | Sec 3 — Market Dynamics (named drivers/restraints, each with a bps CAGR contribution) |
| `porters_five_forces` | Sec 4 — Porter's Five Forces (score 1-5 + label per force, overall score) |
| `segmentation_grade_form` | Sec 5 — Segmentation by Grade/Type and Product Form |
| `end_use_industry` | Sec 6 — End-Use Industry Analysis (share + CAGR per application) |
| `sub_segments` | Sec 7 — High-Growth Sub-Segment Opportunities |
| `derivatives_pricing` | Sec 8 — Derivatives and Pricing Structure (product values + price-tier table) |
| `regional` | Sec 9 — Regional Outlook (value/share/CAGR per fixed region, country breakdown, application mix — per region) |
| `competitive_landscape` | Sec 10 — Named-competitor market share |
| `regulation` | Sec 11 — Regulatory compliance cost by area |
| `willingness_to_pay` | Sec 12 — Consumer willingness-to-pay matrix |
| `investment_priorities` | Sec 13 — Investment/ROI opportunity matrix |
| `scenarios` | Sec 14 — Bear/Base/Bull scenario triad |

`regional` always uses the fixed 5-region list (North America, Europe,
Asia Pacific, Latin America, Middle East & Africa) — same convention as the
Desk Research Segment Definition gate elsewhere in this repo.

## Randomization rule (exact behavior — see `randomizer.py`)

Every numeric leaf value in a field group's output is jittered
independently: magnitude is drawn uniformly from **5% to 10%**, sign
(+/-) is a coin flip, applied as `value * (1 + sign * magnitude)`. This
means running the fleet twice for the same market **never** produces
identical numbers.

**Not jittered**: any field whose key ends in `_year` (or is exactly
`year`) — e.g. `base_year: 2025`, `forecast_year: 2035` — since jittering a
year would corrupt the field, not the data. A dict entry keyed *by* a year
string (e.g. `"historical_usd_mn": {"2020": 1720.0}`) still has its VALUE
jittered — only the year key itself is left alone.

## Architecture

- `field_groups.py` — pure data: the 13 groups, their search-query
  templates, system prompts, and expected JSON shape. No numbers live here.
- `field_agent.py` — one function, `run_field_group()`: web search → LLM
  synthesis → randomize. Never raises — a failed group degrades to
  `{"error": ...}` so one bad group never aborts the whole run.
- `randomizer.py` — the jitter logic, isolated and independently testable.
- `run_fleet.py` — CLI orchestrator: runs the requested groups in parallel
  (`ThreadPoolExecutor`), merges results, writes the combined JSON.

Reuses `me_engine.generation.config.GenConfig`, `.llm.GenLLM`, and
`.web_evidence.gather` — the exact same LLM client and web-search
infrastructure the existing Desk Research pipeline already uses, so no new
dependencies or credentials.

## Generating the actual .docx whitepaper

`report/` turns a market's `whitepaper_fields.json` into a full `.docx`
report in the same visual structure as `vitamin_c_market_whitepaper_v2.docx`
(cover page, table of contents, 15 numbered sections each with a DATA
CONFIDENCE badge, body text, figures, tables, and a disclaimer page).

> Note: this was briefly trimmed down to a narrower 6-section structure to
> match an internal AGROIQX whitepaper-guidelines email, then reverted back
> to this full 15-section version per explicit request — this IS the
> intended/current structure, not a stale one.

```bash
# one command, after run_fleet.py has already produced whitepaper_fields.json
python AiAgentsFleet/report/run_report.py --market-slug super-intensive-shrimp-farming-market
# or
python AiAgentsFleet/report/run_report.py --market "Global Vitamin C Market"
```

Requires `matplotlib` (`pip install matplotlib`, one-time) and the `docx` +
`image-size` npm packages (already installed under `report/node_modules/`
via `npm install` in that folder).

Pipeline (`run_report.py` chains these three, each independently runnable):

1. `report/make_charts.py` — regenerates every figure as a PNG via
   matplotlib, using colors sampled directly from the Vitamin C template's
   own chart images (`NAVY #0A2540`, `TEAL #00B4A6`, `ORANGE #F59E0B`,
   `PURPLE #8B5CF6`, `GREEN #10B981`, `GRAY #9CA3AF`, `RED #DC2626`).
2. `report/build_content.py` (+ `report/narrative.py`) — turns the field
   JSON into the actual section text (paragraphs, bullets, table rows),
   mirroring the Vitamin C paper's sentence patterns
   ("We estimate the global X market at USD Y in {base_year}...") but
   entirely data-driven per market. Writes `content_plan.json`.
3. `report/build_docx.mjs` (docx-js) — a pure, market-agnostic layout
   renderer: reads `content_plan.json` + the chart PNGs, emits
   `<slug>-whitepaper.docx`. Validated against Office Open XML's schema via
   the `docx` skill's `validate.py` (`All validations PASSED!` for all 4
   markets run so far).

Output: `AiAgentsFleet/output/<slug>/<slug>-whitepaper.docx` (plus
the intermediate `charts/` folder and `content_plan.json`).

## What this does NOT do (yet)

- Body prose is templated/data-driven, not a literary rewrite per market —
  section structure, headings, and DATA CONFIDENCE labels are identical to
  the Vitamin C template; sentence content is generated from each market's
  JSON fields via `narrative.py`/`build_content.py`, not hand-written.
- Does not cross-validate a field group's numbers against another group's
  (e.g. `regional` totals summing to `market_size`'s base-year value) —
  each group agent runs independently. No reconciliation pass exists yet.
- Does not persist/cache field results across markets the way the main
  Desk Research pipeline's `.gen_cache.db` does for LLM calls (LLM calls
  themselves ARE cached via the shared `GenLLM` cache — only the final
  randomized JSON is not cached, by design, since randomization should
  differ per run).
