# PLAN-03: Correct country and region numbers (regions, countries, "Rest of", MEA)

Read first: `plans/README.md`, `plans/FINDINGS-2026-10-15.md` (X6, X7, X8; B5, B9; C2-C4; D6, D8; E5-E6; F2).

## Principle
Country and region numbers come from the fleet's desk research agents, and
nowhere else. This plan stops the code from **distorting** those numbers, and
re-asks the agent when its answer is visibly wrong. Nothing is blended or
averaged.

## Problem
1. **The code invents the "Rest of" share.** In `model._country_parts`, when the
   agent's named-country shares sum to > 92%, they are rescaled to 88%. That
   makes "Rest of" exactly **12.0%** in every region. Rest of Europe (incl.
   Netherlands, Poland, Nordics) ends up smaller than Spain, and Rest of LatAm
   (incl. Mexico) smaller than Argentina.
2. **The code silently rescales incoherent agent answers.**
   - The regional agent returned regions summing to 4.6× global (GenAI) and
     ~0.5× global (shrimp). The code scaled everything to fit, instead of
     rejecting the answer.
   - Vitamin C's MEA was cut from $95M to $65M, and the gap was handed to
     countries the agent never sized.
3. **Missing countries.** Iran is missing in 3 reports; Mexico is misplaced.
4. **Visibly wrong rankings that nobody catches.**
   - Poultry: Turkey < UK, and Brazil < Germany and Japan.
   - Vitamin C: Turkey < UAE.
   - Shrimp: Vietnam $370M vs US $136M.
   - North America: US share frozen at 78.9%, and Canada ties the US on CAGR.

## Changes

### 1. Ask for the whole split, including "Rest of" (`groups.py` → `countries_main`, `countries_mea` entries)
- The schema must include an explicit `"Rest of <region>": {"share_pct_of_region", "cagr_2026_2033_pct", "members_note"}`.
- Shares must sum to 100 ±1, **including** Rest of.
- The prompt states that "Rest of" covers every other country in the region.
  Name the members from `MEMBERS[region]` minus the named countries, so the
  agent sizes them knowingly (e.g. Rest of Europe includes Netherlands, Poland,
  the Nordics…).

### 2. Validate, then re-ask; never rescale (`model.py`, plus a small `cmi_report/geo_check.py`)
- Delete the `> 92 → 88` rule in `_country_parts`. Use the agent's Rest-of share as given.
- `check_split(region, answer)` returns a list of problems:
  - The sum is outside 99–101.
  - A share is ≤ 0.
  - Rest of is smaller than a named country while its members include a
    country the agent itself ranks larger elsewhere. Flag only; the re-ask
    decides.
  - The same Rest-of share appears in ≥ 3 regions.
  - Regional values sum to outside 90–110% of the global headline.
- If there are problems, **re-ask the same agent once** with the problems
  listed and its previous answer attached. Keep the second answer.
  - If it still fails on the sum only, normalise proportionally and log it.
  - If it fails for any other reason, record a blocker for the release gate (PLAN-08).
- North America: ask for separate US and Canada CAGRs. Flag the same CAGR to
  one decimal as a re-ask reason.

### 3. Mandatory countries (`groups.py` → `GEOGRAPHY_GROUP`, `validate_geography`)
- The Rest-of member list printed in the report is built **from data**:
  `MEMBERS[region]` minus the named countries. It is not LLM text. Add
  `m["rest_members"]` and pass it into prose facts. Iran, Iraq and Jordan then
  appear in "Rest of Middle East" when they are not named.
- Enforce the region convention in `validate_geography` (Mexico → Latin
  America, Turkey/Israel → Middle East, Egypt → Africa). Re-ask if it is broken.

### 4. Sanity review by the same desk-research agent (`cmi_report/geo_check.py`)
- After allocation, send the agent one "sceptical analyst" call with the final
  country table for the market. Ask it to list any country whose size or rank
  is implausible for this market, with a reason backed by evidence (e.g. "Turkey
  produces more poultry feed than the UK").
- For each flagged country, re-ask the country-split agent for that region,
  with the flag attached. Keep the re-asked answer only if it passes `check_split`.
- Write every flag and its outcome to `m["geo_review"]`, which goes into the
  internal QA file (PLAN-08). Any flag still open after the re-ask is a
  **warning** in the gate.

## Acceptance
- `tests/test_geography.py`:
  - No 88% rescale: a 95% named + 5% Rest-of answer stays 5%.
  - A sum of 130% triggers a re-ask (use a mocked LLM).
  - Identical Rest-of shares across regions are flagged.
  - Iran is in `rest_members["Middle East"]` when not named.
- Regenerate poultry feed and GenAI:
  - The "Rest of" shares differ by region.
  - Regional values sum to within 90–110% of global without silent rescaling.
  - `geo_review` lists the checks.

## Do not
- Do not edit the `published`, `vendors`, `regulation` or `segments_*` entries in `groups.py`.
- No hard-coded country numbers, and no second data source.
