# PLAN-06: Regulation and fact verification

Read first: `plans/README.md`, `plans/FINDINGS-2026-10-15.md` (X11; B1, B8; C9-C10; D3; E4; F4).

## Problem
The `regulation` group asks the LLM to set `verified=true` only when the
evidence confirms an item. In practice:
- **Wrong facts are printed.**
  - The AU fertiliser plan is given as "2023-2033" (it is 2024–2034).
  - The African Medicines Agency is given as 2023 (it was 2021).
  - Turkey's supplement regulator is given as TITCK (it is the Ministry of
    Agriculture).
  - South Africa gets a "2023 Animal Feed Act revision" that does not exist.
  - Germany gets a "coccidiostat phase-out" (it was the AGP ban).
- **Outdated ministry names.** Israel, South Africa (DALRRD), Nigeria and
  Egypt's GAFRD (now LFPDA).
- **Correct facts carry "[to verify]".** MEWA and MOCCAE in shrimp.
- **"[to verify]" goes into the client document**: 6–33 markers per report.
  The markers are added by `build.py:1017`, `build.py:1076`, `charts.py:380` and
  the prose system prompt at `prose.py:52`.

The core issue: verification is the same LLM's self-report. It needs an
independent, claim-by-claim check.

## Changes

### 1. New module `cmi_report/verify_facts.py`
- **Input:** a list of atomic claims: `{"id", "kind": "regulator|milestone|law|vendor_move|policy|date", "country", "text", "year"?}`.
- **For each claim:**
  - Run 1–2 targeted web searches (`gather_with_sources`, filtered by PLAN-01's
    `sources.filter_sources` if merged). Use queries built from the claim:
    `"<regulator> <country> registration <product>"`, `"<law name> <year>"`.
  - Ask a **separate** LLM call (a different system prompt, and if possible a
    different model via `GenConfig`) to judge one of:
    `supported | contradicted | renamed | not_found`.
  - It returns: a corrected value when contradicted or renamed (e.g. the new
    ministry name or the right year), the quote, and the URL.
- **Output:** `fact_checks.json` in the market folder.
- Cache results by claim text in the shared GenLLM cache.
- **Cost control:** cap at 40 claims per report. Order by visibility: tables >
  timeline > profiles > other text.

### 2. Apply results (no more "[to verify]" in the client doc)
- `supported` → print, with no marker.
- `renamed` / `contradicted` with a corrected value → print the corrected value.
  Log the correction to the internal QA file (PLAN-08).
- `not_found` → **omit the item**:
  - **Regulator snapshot table:** keep the country row, because the table lists
    every regulation country. Put "Competent authority not confirmed" in the
    regulator cell, and keep only the requirements text that was itself
    supported.
  - **Timeline milestones, vendor moves and named policies or laws:** drop them
    entirely. If fewer than 3 milestones remain, drop the timeline chart (use
    `sections["milestones"]["show"] = False`).
- Remove the `" [to verify]"` concatenation at `build.py:1017`, `build.py:1076`
  and `charts.py:380`.
- Change the prose system prompt (`prose.py:52`) so the LLM **never** writes
  "[to verify]". It may name only regulators, laws and dates present in the
  verified fact list passed into the prompt facts. Bump `PROSE_VERSION`.
- Add a guard in `guard.py`: a sentence naming a regulator, act, law, decree,
  "Vision 20xx" or a year-dated policy that is not in the verified fact list is
  dropped. Coordinate with PLAN-07 by adding one function, `unverified_fact(sentence, facts)`.

### 3. Prompts (`groups.py` → `regulation` entry only)
- Ask for the **legal instrument name and number** (e.g. "Act 36 of 1947").
- Ask for the competent authority for this product category (food supplement vs
  medicine vs fertiliser vs feed). Ministries are renamed often, so the agent
  must give the name as of the current year.
- Milestones must be the year the instrument entered into force, not when it
  was proposed. Each needs an `instrument` field.

### 4. Seed list of known traps (tests only, not runtime data)
Put the confirmed corrections from FINDINGS into
`tests/fixtures/fact_traps.json` as regression cases for `verify_facts`, marked
"must not be printed as stated":
- the AU fertiliser plan years
- the AMA year
- Turkey TITCK
- SA Animal Feed Act 2023
- the German coccidiostat phase-out
- Egypt GAFRD
- Israel's ministry name

## Acceptance
- `grep -c "\[to verify\]"` on every regenerated report's text equals **0**.
- `tests/test_verify_facts.py` (mock the search and LLM): each verdict branch
  prints or omits correctly, and the corrected value replaces the original.
- Regenerate biostimulants: the AU plan reads 2024–2034, or the plan is not mentioned.
- Regenerate vitamin C: there is no "TITCK" as the Turkish supplement regulator.
- `fact_checks.json` exists and lists every regulator and milestone printed.

## Do not
- Do not hard-code corrected facts into runtime code; the fixtures are for tests
  only. Corrections must come from evidence at run time.
