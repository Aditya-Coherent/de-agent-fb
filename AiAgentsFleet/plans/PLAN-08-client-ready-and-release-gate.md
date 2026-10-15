# PLAN-08: Client-ready document and a blocking release gate

Read first: `plans/README.md`, `plans/FINDINGS-2026-10-15.md` (X3, X12, X17; D QA false positive; E9).

## Problem
1. **The client .docx contains internal pipeline output.**
   - A.4 is duplicated and holds a truncated "About this whitepaper" fragment
     ("...may require additi"). It lists 0 items while A.6 says "2 item(s)".
   - A.5 says "51 generated sentence(s) removed before publication".
   - A.6 says "0 sentence(s) removed or corrected", which contradicts A.5.
   - Built by `build.add_appendix` (`build.py:232-342`).
2. **QA can't fail a report.** `run_cmi_report.qa` and `audit.py` write JSON, but
   nothing stops a report with wrong numbers from being produced and treated as
   finished.
3. **The QA has false positives.** `factcheck.py` attributes "$0.35M … in 2033F"
   to 2033 when the number belongs to 2026E. This came from a year-attribution
   bug in `_year_near`.
4. **Small data errors in the appendix.**
   - Value-chain "LOW 33% vs HIGH 32%" (low > high) after the rescale in `model.py`.
   - Duplicate register IDs (`S-MOROCCO-ASCORBICAC-*` for two segments, from
     `register._code` truncation).
   - Chart files for countries with no section (e.g. `demand_qatar.png`).
   - Empty headings ("9.1 Data triangulation").

## Changes

### 1. Two documents: client and internal
- `build.add_appendix`: the client version keeps only:
  - **A.1 Master data sheet**
  - **A.2 Number register (derived facts)**, but without vendor rows when
    PLAN-05 has dropped shares
  - **A.3 Rankings register**
- Move A.4–A.6 (to-verify list, corrections, consistency log) and the
  allocation and fact-check logs into a separate **internal QA report**:
  `output/<slug>/<slug>-internal-qa.md`, or `.docx` if the template is handy;
  markdown is fine.
- Fix the A.4 duplication bug either way: `to_verify_items` picks up the
  "About" box sentence because it contains the literal "[to verify]" in its
  explanatory text (`build.py:670`). After PLAN-06 there are no markers. Remove
  that explanatory sentence from the About box too.
- Remove any heading whose section has no body (generic check in `Builder`
  before save: a heading followed directly by another heading of the same or
  higher level, or by the end).

### 2. Data hygiene fixes
- **Value-chain rescale** (`model.py` ~line 410): after scaling, enforce
  `low <= high` for every stage, and keep ranges at least 1 pt wide.
- **Register IDs** (`register.py`): make `_code` collision-safe. Append a short
  hash or index when two entities map to the same code, and assert the IDs are
  unique.
- **Charts** (`run_cmi_report.make_charts`): generate country charts only for
  countries in `m["profiled"]`. Delete stale PNGs in `charts/` before each run.

### 3. Fix the QA year attribution (`factcheck._year_near`)
- For patterns like "in 2026E at $X and in 2033F at $Y", bind each number to the
  nearest **preceding** year token within the same clause, not the nearest one
  overall.
- Add a test using the GenAI sentence from FINDINGS D. It must produce no flag.

### 4. Blocking release gate (`audit.py --gate`)
`audit(slug, gate=True)` returns `{"ready": bool, "blockers": [...], "warnings": [...]}`
and exits non-zero when not ready.

`run_cmi_report.py` runs it at the end. When it fails, it renames the output to
`<slug>-cmi-whitepaper.DRAFT.docx` (and .pdf) and prints the blockers. Batch
runs (`run_batch.py`) record `ready` in `batch_log.jsonl`.

**Blockers (any one → not ready):**
- Any "[to verify]", "[unverified]", "TBD", "lorem" or "drone" leftover in client text.
- Any internal-pipeline wording in client text: "generated sentence", "sweep",
  "register ID" outside A.2, "self-audit".
- `qa_report.numbers_not_in_model > 0` or `same_fact_inconsistencies > 0` (after
  the factcheck fix).
- `computed.math_sums_cagr_increments` not empty.
- `market_size_check.status == "unsourced"` while the text mentions published
  estimates or triangulation (needs PLAN-02's `market_size_check.json`; skip if absent).
- A country split that still fails `check_split` after the re-ask (PLAN-03's
  `m["geo_review"]`; skip if absent).
- Any vendor share > 20% without a source (PLAN-05).
- An empty heading, a "Key conclusions" with < 3 items, or a country profile
  with no lead sentence.
- The **AI plausibility review** (below) returns any `severity: "high"` item.

**AI plausibility review.** Extend `audit.ai_review` with a second prompt. It
reads the finished report text plus the master data sheet, acts as a sceptical
industry analyst for this market, and returns:
```
[{"quote", "issue", "category": "number|fact|logic|scope|vendor|regulation", "severity": "high|medium|low", "fix"}]
```
Use the 5 per-market sections of FINDINGS-2026-10-15.md as few-shot examples of
what "high" means. Save the result to `audit_report.json` → `plausibility_review`.

### 5. The internal QA report must contain
- The gate result and blockers.
- The market-size check: the quote and source the headline came from, or "unsourced" (PLAN-02).
- The geography review: flags, re-asks and outcomes (PLAN-03).
- Fact checks (PLAN-06).
- Dropped sentences with reasons.
- Raw-vs-final deltas for the headline, regions and top-10 countries.
- The plausibility review.

The user reads this file to decide whether to ship.

## Acceptance
- `tests/test_gate.py`: a doc with "[to verify]" → not ready; a clean doc → ready.
- The GenAI factcheck test passes (no false flag).
- The value-chain low ≤ high test passes.
- Register IDs are unique in all 5 regenerated registers.
- A regenerated report's client .docx contains no "A.4", "A.5" or "A.6" headings
  and no "generated sentence".
- The `<slug>-internal-qa.md` file exists and lists the gate result.

## Do not
- Do not change prose generation (PLAN-07) or fact verification (PLAN-06). Only
  read their outputs, if present.
