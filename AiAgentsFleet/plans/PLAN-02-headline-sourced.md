# PLAN-02: The headline size must be backed by the desk research that produced it

Read first: `plans/README.md`, `plans/FINDINGS-2026-10-15.md` (X4; C headline; D headline; E headline; F headline).

## Principle
The fleet's desk research is the **single source** of the market size. No
medians, no blending with other approaches. What this plan adds is a check that
the number the agent returns really comes from its evidence, plus a re-ask when
it doesn't.

## Problem
- The `market_size` agent (fleet group in `field_groups.py`) returns a 2025
  value and a CAGR, but it never says **which snippet** they came from. So an
  invented number looks the same as a sourced one.
- The report then dresses this up:
  - "Published 2025 estimates … vary" even when no published figure was found
    (poultry feed).
  - "The gap reflects scope…" as an invented explanation (vitamin C).

## Changes

### 1. The agent cites its number (`field_groups.py` → `market_size` entry only)
- Add to the schema:
  - `"basis": [{"quote": <exact evidence text>, "value_usd_mn": <number>, "year": <int>, "cagr_pct": <number|null>}, ...]`
  - `"how_derived": <1 sentence>`
- Prompt rule: `base_year_value_usd_mn` and `cagr_pct` must be taken from, or
  directly derived from, the quotes in `basis`. Derivation means normalising a
  2024/2026 value to 2025 with the quoted CAGR, or a USD conversion. If several
  quotes disagree, pick the one whose scope matches the market definition best,
  and say why in `how_derived`.
- Add more market-size queries so there is usually something to cite:
  - `"{market} market size 2024"`
  - `"{market} market size 2025"`
  - `"{market} market worth billion"`
  - `"{market} market valued at"`

### 2. Verify the citation (new function in `consistency.py` or `cmi_report/verify_size.py`)
`verify_market_size(ms, sourced_snippets) -> {"status": "sourced" | "unsourced", "notes": [...]}`
- **sourced**:
  - At least one `basis.quote` is found in the snippets, using the same
    normalised match as `run_data._verified_published`.
  - Its number appears in the quote.
  - The headline is that number, or derived from it within ±2% of the stated
    derivation.
- **unsourced**: otherwise.
  - **Re-ask the agent once**, telling it which quote failed.
  - If it is still unsourced, the report labels the headline a "CMI estimate"
    and prints no published-comparison wording.
  - Record this in the internal QA file (PLAN-08) so you can see which reports
    rest on an unsourced headline.
- `field_agent.run_field_group` must keep the `sourced` list for this group, so
  the check can see the snippets. Store it under `_sources.snippets` for
  `market_size` only.

### 3. Report text says only what is true
- Save `market_size_check.json` in the market folder. `model.build` copies it
  to `m["size_check"]`.
- Section 9.1 / methodology and `charts.published_vs_cmi`:
  - **sourced**: name the source (publisher and site) of the figure used.
    Published figures from the `published` group may still be listed for
    reference, but the text may not invent reasons for gaps.
  - **unsourced**: no published-comparison chart, and no "published estimates"
    or "triangulation" wording.
- `prose.py`: add `size_check` to `base_facts`. Add a guard (one function, since
  PLAN-07 owns `guard.py`) that drops any sentence explaining a CMI-vs-published
  gap.

### CMI's own figure (user decision, 2026-10-15)
When the evidence holds a verified quote from Coherent Market Insights for
this market, it is the headline source.
- A CMI quote is recognised by the snippet's website (`coherentmarketinsights.com`
  or a subdomain), never by the agent's publisher text.
- It must name this market: every key word from `sources.market_tokens` is in
  the quote. A CMI figure for a parent or adjacent market is not used at all.
- A verified CMI entry wins over every other verified quote. A non-base-year
  value is carried to the base year with the usual rules (quoted CAGR, max 2
  years, ±2%). A headline that doesn't match it is unsourced, and the re-ask
  tells the agent to use the CMI figure.
- The check records `source_is_cmi`; 9.1 then reads "CMI's published estimate".
- The `market_size` agent gets the query `"{market} Coherent Market Insights"`
  and a prompt rule to use a CMI line as the headline basis.

### 4. Scenarios
In `consistency._reconcile_scenarios`, keep the agent's bear/bull CAGRs when
bear < base < bull. Today the GenAI raw 18/32/45 was overwritten. Clamp only
values that break the order, and log it.

## Acceptance
- `tests/test_verify_size.py` covers:
  - A quote that is present with a matching number → sourced.
  - A quote that is not in the snippets → unsourced.
  - A number not in the quote → unsourced.
  - 2024 → 2025 normalisation → sourced.
- Regenerate biostimulants: `market_size_check.json.status == "sourced"`, and
  the cited quote is in the evidence.
- For a market whose headline stays unsourced, the report has no "published"
  or "triangulat*" wording, and the headline says "CMI estimate".

## Do not
- Do not blend or average with other sources or approaches.
- Do not feed human ME or truth workbooks in as evidence.
