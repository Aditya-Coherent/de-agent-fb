# Demand-Side Evidence Baseline — V3

**Phase**: Phase 0 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) — Baseline & Contracts.
**Generated**: fresh, live re-run of the current CLI (`demand_usage_pipeline.py`) for all three
markets on 2026-08-19, IST. Every number below comes from an actual `setup -> approve-setup ->
taxonomy -> approve-taxonomy -> size` run executed in this session — none of it is copied from
the prior informal validation session referenced in `DEMAND_SIDE_EVIDENCE_PLAN.md`'s intro.

Raw artifacts backing this document:
- `output/demand_usage/crude-oil/02_sizing.json` / `.xlsx`
- `output/demand_usage/cement/02_sizing.json` / `.xlsx`
- `output/demand_usage/avocado-oil/02_sizing.json` / `.xlsx`

## Run notes (operational, honest disclosure)

- All three markets ran successfully end to end; no live run failed outright.
- One operational irregularity in **this session's execution**, not the pipeline itself: the
  Crude Oil `size` command was accidentally launched as two concurrent OS processes against the
  same output path (a shell-backgrounding mistake on the operator's side, not a pipeline bug).
  Both processes ran the full evidence-retrieval flow independently; the committed
  `02_sizing.json`/`.xlsx` is whichever process's write landed last. The file was verified to be
  valid, non-corrupted JSON with internally consistent attempt counts, so the numbers below are
  real — but this run's *wall-clock cost* (not its correctness) was doubled for Crude Oil only.
  Cement and Avocado Oil each ran as a single process.
- One real pipeline-level failure occurred organically during the Crude Oil run: the Transportation
  Fuels pricing LLM call failed after 3 attempts (`Expecting ',' delimiter: line 1 column 24571 ...`
  — a malformed/truncated JSON response), floored to $1/unit per the existing safety net and flagged
  `llm_call_failure`. This is recorded as-is, not smoothed over.

## Cross-market summary

| Metric | Crude Oil | Cement | Avocado Oil | Total |
|---|---|---|---|---|
| Usage buckets | 6 (1 excluded: unit contract violation) | 6 | 7 | — |
| Total evidence attempts (driver+intensity+price+conversion) | 356 | 375 | 384 | **1,115** |
| Attempts that yielded a directly-cited figure | 0 | 0 | 0 | **0** |
| Evidence quality: observed / derived / assumption | 0% / 15.6% / 84.4% | 0% / 0% / 100% | 0% / 0% / 100% | — |
| Included Demand | $443,450.0M | $10,395,166,209.8M | $5,328.7M | — |
| Reported Market View (incl. residual) | $466,789.5M | $12,229,607,305.7M | $5,609.1M | — |
| Plausibility score | 25% | 50% | 60% | — |
| Deterministic score | 70% | 80% | 90% | — |

**Cement's total is flagged `implausible_total` and `dominant_bucket_unit_mismatch_risk`** — one
bucket ("Soil stabilization and road base") is ~100% of the total, a symptom of a driver/price unit
mismatch, not a real market-size finding. This is exactly the kind of run the existing quality-flag
layer (`quality.py`) is designed to catch, and it did.

## Per-facet hit rate (driver / intensity / price / price-conversion)

| Market | Driver found/attempts | Intensity found/attempts | Price found/attempts | Price-conversion found/attempts |
|---|---|---|---|---|
| Crude Oil | 0/308 | 0/12 | 0/24 | 0/12 |
| Cement | 0/322 | 0/13 | 0/24 | 0/16 |
| Avocado Oil | 0/350 | 0/7 | 0/27 | 0/0 |

**This is a materially worse result than the plan's intro baseline**, which reported intensity as
"the one facet that already works" (50% / 16.7% / 50% observed across the three markets) and 1
successful attempt out of 1,046. This fresh run found **zero** successful attempts out of 1,115 —
intensity retrieval, which previously worked at least partially, produced 0 hits in all three
markets this time. This delta is recorded honestly, not reconciled with the earlier numbers: LLM
outputs and live web/search conditions are non-deterministic run to run (the plan's own §"How to
use this document" anticipates this), and this is exactly the kind of regression-or-noise question
Phase 1+'s asset-verification work needs a frozen, real baseline to be measured against — this file
is that baseline, not the more optimistic prior-session numbers.

## Rejection-reason breakdown (misses only)

| Reason | Crude Oil | Cement | Avocado Oil | Total |
|---|---|---|---|---|
| `no_source_found` | 117 | 144 | 163 | **424** |
| `download_failed` | 173 | 138 | 93 | **404** |
| `extraction_failed` | 63 | 90 | 127 | **280** |
| `not_relevant_page` | 3 | 3 | 1 | **7** |

Dominant failure modes are unchanged in *kind* from the plan's intro diagnosis
(`download_failed` / `no_source_found` / `extraction_failed` account for effectively all misses),
confirming Phase 1's target (shared source-discovery/URL-verification layer) is still the right
first fix.

## Source-type attempt volume (all facets combined)

| Source type | Crude Oil | Cement | Avocado Oil | Total |
|---|---|---|---|---|
| `pdf` | 156 | 160 | 107 | **423** |
| `csv_excel` | 113 | 136 | 150 | **399** |
| `web_page` | 63 | 78 | 126 | **267** |
| `api` | 24 | 1 | 1 | **26** |

PDF and CSV/XLSX remain the largest attempt volumes (as in the plan's intro), confirming Phases
3/4 are targeting the right modules by attempt-volume weight.

## Other quality flags raised (informational)

- **Crude Oil**: `high_assumption_share`, `no_observed_data`, `low_plausibility`,
  `country_cell_outlier`, `llm_call_failure`, `unit_contract_violation`, `low_observed_share`,
  `driver_magnitude_implausible`.
- **Cement**: `implausible_total`, `high_assumption_share`, `no_observed_data`, `low_plausibility`,
  `dominant_bucket_unit_mismatch_risk`, `country_cell_outlier`, `missing_usage_category`,
  `low_observed_share`.
- **Avocado Oil**: `high_assumption_share`, `no_observed_data`, `usage_bucket_overlap`,
  `low_observed_share`, `driver_magnitude_implausible`.

## Regression target for later phases

Any later phase's "did this help" claim (Phase 1's re-run, Phase 3/4/5's per-acquirer re-runs,
Phase 12's final cross-market report) must diff against **this file's numbers**, not the plan's
intro numbers. At minimum, track:
- Total evidence attempts and total found (this baseline: 1,115 attempts / 0 found).
- Per-facet (driver/intensity/price) found/attempts ratios (this baseline: 0% in all three, all
  three markets).
- Rejection-reason distribution (this baseline: `no_source_found` 424, `download_failed` 404,
  `extraction_failed` 280, `not_relevant_page` 7).
- Evidence-quality percentages per market (this baseline: see summary table above).

## Verification-status schema (this phase's other deliverable)

`VerificationStatus` (`verified_observed` / `source_found_unverified` / `derived` / `assumption`)
and `legacy_evidence_type()` were added to `src/me_engine/demand_usage/evidence.py` as a pure
additive change in this same session — see `tests/test_demand_usage_baseline_contract.py`. Nothing
in this baseline run exercises the new enum values; every fact in the three sizing runs above was
tagged in the existing 3-value `evidence_type` scheme, unchanged.
