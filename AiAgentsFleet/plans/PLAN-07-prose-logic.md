# PLAN-07: Prose logic (ties, rankings, recommendations, unsupported claims)

Read first: `plans/README.md`, `plans/FINDINGS-2026-10-15.md` (X2, X13-X18; B2, B6, B7, B10; C11; D1, D4-D5, D7; E6-E8; F5-F9).

## Problem
`guard.py` checks that numbers come from the model and that
largest/fastest/smallest claims are true for the entity named. It misses:
1. **Near-ties called "fastest".** `prose._rank` and `audit.check_ties` treat
   values as tied only when equal at printed precision, so 12.3% vs 12.2% is
   "faster".
2. **Ordinals.** "Europe is the second-largest region" when it is third.
   `ClaimGuard._QUALIFIED` explicitly lets "second/third…" pass.
3. **Gap signs.** `register.py:51`, `:63` and `:94` use `abs(gap)`, so a
   negative gap prints as positive.
4. **Basis mismatch.** The global "Primary constraint" is stated in pp of MEA
   CAGR. The driver/restraint pp don't reconcile with the CAGR.
5. **Recommendations don't follow from their cited finding.** For example,
   "Secure fermentation-derived supply" cites the synthetic segment.
6. **Unsupported causal or comparative claims.**
   - "local distribution shapes share more than brand" when the data shows the opposite.
   - "high mobile penetration" next to a "limited digital infrastructure" restraint.
   - "tighter registration sustains demand".
   - "India is a key producer".
   - "pilot-scale trials" for a mature technology.
7. **Methodology claims that didn't happen:** "expert panel", "primary
   interviews", "bottom-up build-ups" (`build.py:1109`, `charts.py:480-484`,
   `prose.py:257`).
8. **Boilerplate and fragments.**
   - All 6 trends say "most visible in the Middle East".
   - Profiles with no lead sentence.
   - "Key conclusions" has one item.
   - A dangling "these factors".

## Changes

### 1. Materiality threshold for rankings (`prose._rank`, `audit.check_ties`, `guard.ClaimGuard`)
- Add `TIE_PP = 0.3` (CAGR) and `TIE_REL = 0.03` (values within 3%) in one place
  (`fmt.py` or a new `cmi_report/rules.py`).
- `_rank`: if the top two are within the threshold, the result is
  `"A and B (similar)"`. The prompt facts then say "grow at a similar rate",
  never "fastest".
- `ClaimGuard`: reject "fastest/largest/slowest/smallest" when the named entity
  is within the threshold of the runner-up, unless the sentence also says
  "similar", "tied" or "level with".

### 2. Ordinal check
- Extend `ClaimGuard`: parse "(second|third|fourth|fifth)[- ](largest|fastest|smallest|slowest)"
  plus the entity, and check the entity's rank in its peer group. Drop the
  sentence if it is wrong.
- Remove the blanket pass in `_QUALIFIED` for these words; keep it for "one
  of" and "among".

### 3. Signs and basis
- `register.py`: store the signed gap. Display it with `fmt.pp` (signed). Update
  `FactGuard._check` so "1.2 pp below" matches −1.2, and "1.2 pp above" or
  "+1.2 pp" matches +1.2.
- Global vs MEA basis: in `prose.chapters`, the global-column slots get only
  global facts. Add a guard that a sentence in a global slot mentioning "MEA
  CAGR" is dropped.
- **Driver/restraint reconciliation.** Either:
  - (a) rescale the drivers and restraints so that `baseline + Σdrivers + Σrestraints = MEA CAGR`,
    defining the baseline as the global CAGR and printing it; or
  - (b) relabel them as "relative impact score (1-5)" with no pp.

  Pick (a) if the numbers allow it; otherwise (b). The GenAI case (net +3.0 pp
  while MEA grows slower than global) must come out consistent.

### 4. Recommendation and finding support check
- In `prose.py`, after the recommendations / priority actions are written, run
  one LLM judge call per action: does the cited finding logically support the
  action (`yes | weak | no`), and does the action's geography match the report's
  focus (MEA)?
  - `no` → regenerate the action once with the finding; if it is still `no`, drop it.
  - Also check that every entity named in an action (country, segment) is
    consistent with its data. For example, "target high-growth Saudi Arabia" is
    rejected when Saudi grows below the MEA average. Use the register.

### 5. Claim check against data (contradiction guard)
- Add `contradiction_guard`: for comparative or causal sentences mentioning two
  quantities we hold in the model ("more concentrated", "share more than",
  "faster than", "momentum"), compute the truth from the model and drop the
  sentence if it is false. Start with these patterns:
  - concentration (top-N share MEA vs global)
  - growth comparisons between two named entities
  - "momentum" for an entity that is not above its peer average
- Add a domain-sanity LLM pass (cheap model) over the finished body text. It
  lists sentences that state a real-world fact likely to be false or
  unsupported (e.g. "India is a key vitamin C producer", "pilot-scale" for a
  mature industry, post-Brexit "gateway to Europe").
  - Drop each flagged sentence, or rewrite it without the claim.
  - Log every drop for the internal QA file.
  - Bump `PROSE_VERSION`.

### 6. Truthful methodology
- Replace the methodology wording in `build.py:1109`, `charts.py:480-487`
  (`PROCESS`) and `prose.py:257` (`method_para` spec) with what the pipeline
  actually does:
  - web-evidence agents
  - market size taken from cited desk-research evidence (PLAN-02)
  - country splits checked and re-asked when incoherent (PLAN-03)
  - claim-level fact verification (PLAN-06)
  - automated consistency checks
- No "expert panel", "primary interviews", "bottom-up" or "triangulation"; the pipeline does none of these.

### 7. Boilerplate and structure
- Add a repeated-phrase check: any 5+ word phrase appearing in ≥ 3 sentences
  triggers a rewrite of all but the first. Example: "This trend is most
  visible in the Middle East".
- "Key conclusions" needs ≥ 3 items. If prose gives fewer, build the rest from
  the top body findings (`prose.body_findings`). Otherwise remove the heading.
- Profiles: every country profile starts with a lead sentence (size, share,
  CAGR), using `region_country_fallback` when the LLM text was dropped.
- Grammar pass for plural segment names ("Microbial Biostimulants remain").

## Acceptance
- New tests in `tests/`:
  - Near-tie wording.
  - A wrong ordinal is dropped.
  - A negative gap prints with its sign.
  - A global-slot MEA basis sentence is dropped.
  - A recommendation with an unsupported finding is dropped.
  - Contradiction-guard cases built from the FINDINGS quotes (B2, D4, C11).
- Regenerate GenAI: there is no "Europe is the second-largest".
- Regenerate poultry feed: gaps are signed; no "Target … Saudi Arabia and South
  Africa" as high-growth.
- No report contains "expert panel" or "primary interview".

## Do not
- Do not touch the appendix or QA code (PLAN-08), or the regulation fact
  verification (PLAN-06). Just call `unverified_fact` / `scope_violation` if
  those plans have added them.
