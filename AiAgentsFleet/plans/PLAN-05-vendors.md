# PLAN-05: Vendors and competitive landscape

Read first: `plans/README.md`, `plans/FINDINGS-2026-10-15.md` (X9, X10; B3; C6-C9; D2, D7; E1; F3).

## Problem
The vendor shares are invented. Examples from the 5 reports:
- Vitamin C: DSM-Firmenich at 23%, while CSPC, NEPG, Luwei and Tianli are missing.
- GenAI: Alibaba at 15.4%, which is more than the whole China market.
- Poultry feed: meat integrators are listed as feed vendors.

Other problems:
- **Parent merges are wrong.** "Cargill (incl. Sanderson Farms)"; Climate LLC is
  listed next to Bayer.
- **HQs are wrong.** RYNAN is shown as Singapore, Aquapulse as Israel and CP
  Vietnam as Thailand.
- **Contradictions.**
  - BASF is "excluded" globally but appears in the MEA chart.
  - Shrimp's MEA text drops NAQUA, the 19.4% leader.
  - RYNAN and Aquapulse are excluded globally but shown in MEA.
- **Year mismatch.** Chart "2025E" vs text "2026E".

The global shares come from the fleet `competitive_landscape` group, and the MEA
shares from `vendors.mea_shares`. Both were randomised, and neither is sourced.

## Changes

### 1. Two-tier output: sourced numbers or qualitative positioning
- A numeric vendor share is printed **only** if it is sourced: a quote in the
  evidence gives that company's share of *this* market, or its segment revenue
  for this market. Store `{"vendor", "share_pct", "quote", "url", "verified": true}`.
  - Extend the `vendors` schema in `groups.py` and the fleet
    `competitive_landscape` group in `field_groups.py`.
  - Verify each quote the same way `run_data._verified_published` does: the
    quote must be in a snippet and the number must be in the quote.
- If fewer than 3 vendors have sourced shares, **drop the share donuts**
  (Figures 44/45 and the vendor rows of A.2). Replace them with a
  **positioning table**: vendor | HQ | parent group | business type | role in
  this market | MEA presence.
  - Positioning tiers ("Leader / Challenger / Niche") are assessments, labelled
    as such, with no percentages.
- Never randomise vendor data. Remove `vendors` from `_PARTIAL_RANDOMIZE` and
  set `"randomize": False`. PLAN-01 turns jitter off globally, but this must be
  explicit too.

### 2. Entity facts (new `cmi_report/entities.py`)
- One LLM call with targeted search per vendor (cap at 12 vendors). It returns:
  - `{"name", "hq_country", "parent_group", "subsidiaries_in_list", "business_type", "earns_defined_revenue", "evidence_url", "verified"}`.
  - `hq_country` and `parent_group` must be backed by a snippet, otherwise `verified=false`.
- Never print an unverified HQ or parent. If a vendor's HQ is unverified, drop
  that vendor from the dashboard table. Don't print a placeholder.
- `model.merge_parents`: merge **only** when `entities.parent_group` is verified,
  and the subsidiary is fully owned by that parent (Wayne-Sanderson is a JV,
  so no merge).
- Apply one exclusion list to **both** the global and MEA vendor lists:
  `earns_defined_revenue == false` → excluded everywhere. Rebuild the MEA list
  after exclusion and renormalise. Log it once.

### 3. Market-fit check for the vendor list
- Add to the prompt:
  - List the top producers or vendors **that earn the revenue in
    `definition.measures` at `value_chain_level`** (from PLAN-04; fall back to
    `definition` if PLAN-04 is not merged yet).
  - If the market is dominated by one country's producers (e.g. China for
    vitamin C), the list must reflect that.
- Plausibility check (a local helper in `entities.py`):
  - A vendor's implied revenue (`share × market`) must not exceed the market
    value of its home country when that vendor is mainly domestic. For example,
    Alibaba > China is blocked.
  - Any vendor share > 20% without a source → "block".

### 4. Text consistency
- The vendor chapter text gets its vendor facts only from the final chart or
  table data (`m["vendor_share_*"]` or the positioning table). Add a guard: any
  vendor named with a percentage must match the data. Any vendor in the data
  that is the MEA leader must be named in the MEA paragraph.
- **One year everywhere.** The vendor share year is `Y0` (2026E) or 2025E, so
  pick one constant in `model.py`. Use it in chart titles (`charts.vendor_donut`
  callers in `run_cmi_report.make_charts`), table captions, register IDs and
  prose facts.
- "Recent" claims about deals or acquisitions need a year from the evidence.
  Otherwise drop the word "recent", and drop the claim if it has no year.
- Ownership notes (`dashboard.ownership_note`, e.g. "Italpollina, now Hello
  Nature"; "Biolchim, owned by Huber") must be printed when present and verified.

## Acceptance
- `tests/test_vendors.py`:
  - An unsourced share is dropped.
  - Fewer than 3 sourced shares → no donut, positioning table instead.
  - A JV is not merged.
  - An excluded vendor is absent from both charts.
  - The vendor year is the same in chart, caption and text.
- Regenerate vitamin C: no DSM-Firmenich at #1 without a quote. The Chinese
  producers appear in the positioning table.
- Regenerate shrimp: NAQUA is in the MEA paragraph. RYNAN's HQ is Vietnam, or
  the HQ is omitted.
- Regenerate poultry feed: no "Cargill (incl. Sanderson Farms)".

## Do not
- Do not edit `groups.py` entries other than `vendors`. In `field_groups.py`,
  edit only `competitive_landscape`.
