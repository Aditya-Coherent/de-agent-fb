# AiAgentsFleet

A standalone agent fleet: give it a market name, it fills in every numeric
field the whitepaper template (`vitamin_c_market_whitepaper_v2.docx`, see
[../DEMAND_SIDE_EVIDENCE_PLAN.md](../DEMAND_SIDE_EVIDENCE_PLAN.md) for the
broader project context) needs, by querying live web search and having an
LLM synthesize a concise structured answer per field group. Numbers are
kept exactly as the agent returned them: the old +/-5-10% jitter is **off**
(see "Randomization rule" below).

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

## Randomization rule (off for reports — see `randomizer.py`)

**Randomization is off by default, and CMI reports never use it.**
`randomizer.randomize_json` returns an unchanged copy unless the switch
`randomizer.ENABLED` is on (env `AIFLEET_RANDOMIZE=1`).

Why: the jitter multiplied every numeric leaf, including both the base
value **and** the CAGR, by 1 ± U(5%, 10%). The errors compounded (2033
values off by up to ~12%), and rankings and ties came from noise, e.g. a
0.1 pp gap reported as "the faster-growing subregion", and history that
went down from one year to the next (FINDINGS-2026-10-15 X1, X2). With it
off, the same evidence gives the same numbers, and every number can be
traced back to the desk research that produced it.

- `run_fleet.py` writes each agent's answer to `fields_raw` and the
  (identical, when jitter is off) values to `fields`; the metadata key
  `"randomization"` reads `"none"`.
- `cmi_report/run_cmi_report.py` and `cmi_report/run_batch.py` stop with an
  error if `AIFLEET_RANDOMIZE` is set, unless `--allow-randomize` is passed.
  Never pass it for a report that goes to a client.
- The `randomize` keys in `cmi_report/groups.py` are kept, but they only
  take effect when the switch is on.

When the switch is on, the old behaviour applies: each numeric leaf is
multiplied by `1 + sign * magnitude` (magnitude uniform in 5-10%, sign a
coin flip). Fields whose key ends in `_year` (or is exactly `year`) are
never jittered.

## Source quality (`sources.py`)

Both search paths (`field_agent.run_field_group` and
`cmi_report/run_data.search`) drop snippets from blocked sites before the
LLM sees them: video, social, marketplace, Q&A, horoscope sites, and
throwaway-TLD link farms (`.so`, `.tk`, `.xyz` …), then keep only snippets
that mention the market (`on_topic`); the fleet path retries the search once
if fewer than 3 remain. Each group's `_sources` records `num_sources`,
`domains`, `dropped_domains`, `off_topic_dropped` and a `quality` block
(`n`, `distinct_domains`, `trusted`, `score`). A numeric group
(`market_size`, `regional`, `countries_*`, `segments_*`,
`segmentation_grade_form`, `competitive_landscape`, `vendors`) with fewer
than 3 kept snippets or no trusted domain (government, company IR/press
release, trade association, recognised research publisher, news/trade
press) gets `_sources.low_evidence = true`.

## Historical series

`consistency._reconcile_market_size` rescales `historical_usd_mn` so it
leads into the base year, then checks it: it must not fall year on year
(for a growing market), and no year-on-year move may exceed 2× the CAGR.
The result is stored in `market_size.history_check`. If the check fails,
the raw history is kept, a warning is logged in `consistency_changes`, and
no chart, table or prose prints the history.

## Architecture

- `field_groups.py` — pure data: the 13 groups, their search-query
  templates, system prompts, and expected JSON shape. No numbers live here.
- `field_agent.py` — one function, `run_field_group()`: web search → source
  filter → LLM synthesis (→ jitter only if switched on). Never raises — a failed group degrades to
  `{"error": ...}` so one bad group never aborts the whole run.
- `randomizer.py` — the (off-by-default) jitter logic, isolated and independently testable.
- `sources.py` — blocked/trusted source lists, `filter_sources()`, `quality()`.
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

Pipeline (`run_report.py` runs the consistency pass, then these three, each
independently runnable):

1. `report/prose_writer.py` — AI-written section text, number-guarded
   (cached in `prose.json` until the numbers or `PROSE_VERSION` change).
   It first names what each segment list really is ("Shrimp Species",
   "Application Methods" — see `report/labels.py`), then drops any text
   that restates the headline size outside Sections 1/2/14/15, contradicts
   the competition-structure label, or repeats an earlier heading.
2. `report/make_charts.py` — every figure as a PNG via matplotlib, using
   the Vitamin C template's colors; donut/mix labels use the same dimension
   names as the headings.
3. `report/build_from_template.py` — clones `vitamin_c_market_whitepaper_v2.docx`
   and fills it. Section titles, the TOC, sub-headings and captions all use
   the dimension labels; figures/tables are numbered by section
   (Figure 9.2); a "grade" list that just repeats Section 6's applications
   is left out. Ends with a QA summary (duplicate/near-duplicate headings,
   restated headline, competition-label contradictions, Vitamin C leftovers).

Output: `AiAgentsFleet/output/<slug>/<slug>-whitepaper.docx` (plus
the intermediate `charts/` folder and `prose.json`).

## CMI whitepaper template (`cmi_report/`)

A second report format: the CMI whitepaper (`templates/cmi_whitepaper_template.docx`,
originally the drone-market template) -- Global vs Middle East & Africa,
2026E-2033F, country tables for every region, per-country segment charts
for the Middle East and Africa, regulation, vendors, recommendations,
methodology. Nothing in it is market-specific: run any market name.

```bash
# one market (runs the fleet first if the market is new)
python AiAgentsFleet/cmi_report/run_cmi_report.py --market "Global Drone Market"
python AiAgentsFleet/cmi_report/run_cmi_report.py --market-slug agricultural-biostimulants-market

# many markets: one name per line, done markets skipped, outcomes in output/batch_log.jsonl
python AiAgentsFleet/cmi_report/run_batch.py --markets markets.txt
```

Output: everything for a market in one folder, `output/<slug>/`:
`<slug>-cmi-whitepaper.docx` (+ `.pdf` when MS Word is installed), `charts/`,
`qa_report.json`, and the data it was built from (`whitepaper_fields.json`,
`cmi_fields.json`, `cmi_model.json`, `cmi_prose.json`).

How numbers and text are kept consistent:

1. `run_data.py` runs ten more agents (`groups.py`): country splits, Middle
   East / Africa split, type + demand-segment mixes (the demand dimension --
   end user, application, crop type... -- is named per market), dynamics,
   use cases, regulation, vendors, strategy, published third-party figures.
   Search results that don't mention the market are discarded and the search
   is retried; published figures are kept only when found verbatim in a
   snippet, as that year's value, credited to the snippet's own website.
2. `model.py` builds ONE numeric model from the fleet's reconciled market size
   and the agents' shares/CAGRs: regions sum to global every year, countries
   to their region, segments to their geography, Middle East + Africa to MEA.
3. Every table, chart label and sentence prints numbers through `fmt.py` from
   that model. The LLM writes the text (`prose.py`) from pre-formatted facts;
   `guard.py` then drops any sentence with a number not in the model, a
   largest/fastest/smallest claim that is false for the entity it names, a
   category the data doesn't have ('Rest of North America'), or draft/
   disclaimer wording.
4. `build.py` fills the template element by element and removes the Data
   status box, the Contact section and the Disclaimer. `run_cmi_report.py`
   then sets the real TOC page numbers via Word and runs a final QA audit
   (`qa_report.json`) over the finished document.

### Report standard and audit

The CMI pipeline follows the report standard (consistency, accuracy, relevance):
client market definition stated first and obeyed by every agent; countries chosen
per market by real relevance (`geography` agent, validated against region
membership) with a profile for every table country; parent/subsidiary groups and
non-comparable businesses kept out of like-for-like share charts; rankings stated
for their year and ties stated as ties; growth-vs-global claims checked; text
copied from the template's drone example dropped; estimates labelled, unverified
regulators / milestones / vendor moves marked [to verify]; executive summary and
recommendations written last from the body, each recommendation citing its
finding; no blank or "-" cells; an appendix "Consistency & Verification Log"
(master data sheet, rankings register, [to verify] list, corrections).

Number integrity: `register.py` builds a Number Register (one row per fact, with an
ID, its exact meaning, its entity and its single display format) before any text is
written. Every number in the text, tables and the final sweep must be the register
value of an entity named in the same sentence or table row (a share of the region
is never accepted as a share of global; "$4.3B" is rejected where the register says
"$4.25B"). The sweep runs on the finished document, flagged sentences are removed and
the sweep repeated; the counts go into the appendix log. The complete register is
saved as `<slug>-number-register.csv`; the appendix prints its derived facts.
Every named Middle East / Africa country and each "Rest of" group gets a full
profile with segment charts; other regions' countries get size-and-reasons profiles
and no segment claims. Each chapter closes with a sentence setting up the next; the
executive summary opens with the report's thesis. UK English.

`python AiAgentsFleet/cmi_report/audit.py --market-slug <slug>` writes
`audit_report.json`: exact computed checks (sums, CAGRs, share claims, ties, blank
cells, announced counts, profiles, summary-vs-body, labels) plus an AI review of
judgement calls (missing countries, double counting, segments vs definition,
regulators, recommendation support) -- the AI review is an assessment, not proof.

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
  themselves ARE cached via the shared `GenLLM` cache, so with a warm
  cache and the same evidence a re-run gives the same `fields`).
