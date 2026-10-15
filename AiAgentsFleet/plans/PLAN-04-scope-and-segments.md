# PLAN-04: Market scope and segment coherence

Read first: `plans/README.md`, `plans/FINDINGS-2026-10-15.md` (B4; C1; E2-E3; F1).

## Problem
1. **The scope is not pinned down**, so different agents size different things.
   - Poultry feed: "functional feed excluding additives", but additive trends
     and meat-integrator vendors are used.
   - Shrimp: farm-gate output value mixed with aeration/monitoring equipment.
   - Vitamin C: the definition's "included" field is empty, and animal feed is
     sized although the definition excludes it.
2. **Segments are not mutually exclusive, or the split is false.**
   - Vitamin C "Synthetic (Reichstein)" vs "Fermentation-Derived" (the
     mainstream process is fermentation).
   - Biostimulants "Extract-Based" overlaps "Seaweed Extracts".
3. **The global mix contradicts the country mixes.** Biostimulants: microbial is
   9.4% globally but the largest type in most ME/Africa countries. Each country
   mix is generated independently by `segments_mea`, with no tie to the global mix.

## Changes

### 1. Stronger market definition (`market_definition.py`)
- The schema must return:
  - `measures`: one sentence on **what revenue is counted**, and **at which
    level of the value chain** (e.g. "ex-factory sales of …", "farm-gate value
    of …", "vendor revenue from …").
  - `value_chain_level`: one of `producer | manufacturer | vendor/software |
    service | farm-gate output | retail`.
  - `included`: a non-empty list.
  - `excluded`: a non-empty list.
  - `unit`.
  - `typical_published_size_usd_bn`: if the evidence shows one; this helps PLAN-02.
- Fail and re-ask if `included` or `excluded` is empty, or `value_chain_level` is missing.
- Print `measures` and `value_chain_level` verbatim in Section 1 / About scope.
  Guard: no other chapter may describe the market at a different level (see check 3).

### 2. Segments obey the definition (`groups.py` → `segments_global` and `segments_mea` only)
- Pass `value_chain_level` into the prompt explicitly.
  - For `farm-gate output`, demand segments are buyers or uses of the output,
    and **technology/equipment rows are forbidden**.
  - For `vendor/software`, segments are buyer types or applications.
- Require `segment_definitions` for every segment. Add a validation step in
  `model.py` (or a new `cmi_report/segments_check.py`) that:
  - drops any segment whose definition matches an `excluded` term (reuse
    `consistency.exclusion_terms`), then renormalises and logs the change;
  - flags a pair of segments whose definitions share > 60% content words
    (overlap). Re-ask the agent once with the overlap named; if it is still
    overlapping, merge them.
  - Add a "false split" check: ask the agent, in the same call, whether each
    pair of type segments differs in a way buyers actually price or specify
    (grade, form, purity, application), not production history. Drop the
    dimension (`type_applicable=false`) if not.
- **Tie country mixes to the global mix.** In `model.build`, after the
  `segments_mea` allocation:
  - For each segment, compute the share-weighted sum of the country values. If
    it diverges from the global share by more than ×2 (or ×0.5), blend each
    country's mix 50/50 with the global mix and log it.
  - The MEA aggregate must then be within ×2 of the global mix for every
    segment, unless the agent supplies an explicit `mea_reason`.

### 3. Scope consistency guard (prose)
Add to `guard.py` (or `prose.py` facts):
- A list of forbidden phrases derived from `excluded` and from levels other than
  `value_chain_level`. For example, for `farm-gate output`, sentences about
  "equipment sales", "aerator revenue" or "technology spending" as part of the
  market are dropped.
- Heat-map and use-case rows (the `use_cases` group) must also be checked
  against `excluded`.

Coordinate with PLAN-07 (it owns `guard.py`). Implement this as a separate
function, `scope_violation(sentence, definition)`, and call it from
`clean_text`.

## Acceptance
- `tests/test_segments.py`:
  - An excluded segment is dropped.
  - An overlapping pair is detected.
  - A divergent global vs MEA mix gets blended.
- Regenerate vitamin C:
  - The definition's `included` is non-empty.
  - No Animal Feed segment if feed is excluded, or feed is in `included`.
  - The type dimension is not Synthetic vs Fermentation.
- Regenerate shrimp: no equipment or technology rows in the inclusion table or
  heat maps when the level is farm-gate.
- Regenerate biostimulants: the global and MEA type mixes are within ×2 per segment.

## Do not
- Do not edit the vendor, regulation, published or geography groups.
