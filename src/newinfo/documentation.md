# Number Documentation

**Version:** 1.0 · **Date:** 2026-10-12
This file has four parts:

- **Part A** — how every number the system produces is documented (the ledger entry format, labels, rounding, units).
- **Part B** — an audit of every number that appears in the 2A / 2B / 2C strategy notes: what it is, how it was (or must be) derived, its label, and the problems found.
- **Part C** — every threshold and constant the system uses, with where it came from.
- **Part D** — open questions that block full documentation.

All derived values in Part B were recomputed in code, not by hand.

---

## Part A — Documentation standard

### A.1 Number ID

`{RUN}-{STRATEGY}-{GEO}-{KIND}-{SEQ}`, e.g. `R017-2C-IND-QTY-0042`.
KIND ∈ `QTY` (quantity), `PRC` (price), `VAL` (value), `SHR` (share/ratio), `FAC` (adjustment factor), `PAR` (model parameter), `FX`.

### A.2 Ledger entry (every number gets one)

| Field | Meaning |
|---|---|
| `number_id` | as above |
| `name` | plain-language name, e.g. "Vinegar used in eggless cakes, India" |
| `value`, `unit` | full precision, `pint` unit string |
| `geo`, `year` | ISO3 (or region/global code), base year |
| `label` | one of the five labels in A.3 |
| `weakest_input_label` | least reliable label among all inputs, recursively |
| `formula` | e.g. `N-0040 × N-0041` |
| `input_ids` | IDs used in the formula |
| `fact_id` → source | for observed numbers: URL, publisher, tier, retrieval date, snapshot hash, verbatim quote |
| `module` | code module that wrote it |
| `pass_id` | which agent pass |
| `note` | conversions, year mappings, why an assumption was chosen |

### A.3 Labels

| Label | Meaning | Allowed origin |
|---|---|---|
| `verified_observed` | Number found verbatim in a tier 1–4 primary source; metric, geo, year, unit all match | Verifier |
| `source_found_unverified` | Number found verbatim but source is tier 5, a secondary quote, or a published market report with opaque method | Verifier |
| `derived` | Computed by code from other ledger numbers | Calc engine only |
| `estimated_from_facts` | Produced by the signal model or estimator from related facts | Gap fill only |
| `assumption` | Chosen by an analyst or the LLM without a source; must have a written reason | Any, with reason |

Reliability order for `weakest_input_label`: verified_observed > source_found_unverified > estimated_from_facts > assumption.
A `derived` number is only as good as its weakest input, and outputs show both.

### A.4 Display rules

- Market values: 3 significant figures ($15.7B). Stored at full precision.
- Percentages: 1 decimal place.
- Tonnes: nearest whole tonne below 100k, 3 s.f. above.
- Never round before summing; sums are done on stored values.
- Indian currency: show crore/lakh and the converted USD with the FX rate used.

### A.5 Worked example (2C cakes chain as it would appear in the ledger)

```yaml
- number_id: R000-2C-IND-QTY-0001
  name: Cake production, India
  value: 378000   unit: t   label: <must be sourced; see Part D>
- number_id: R000-2C-IND-SHR-0002
  name: Eggless share of cake production
  value: 0.395    unit: dimensionless   label: <must be sourced>
- number_id: R000-2C-IND-SHR-0003
  name: Vinegar dose in eggless cake recipes
  value: 0.02     unit: t/t   label: assumption   note: recipe-based dose
- number_id: R000-2C-IND-QTY-0004
  name: Vinegar used in eggless cakes, India
  value: 2986.2   unit: t   label: derived
  formula: QTY-0001 × SHR-0002 × SHR-0003
  weakest_input_label: assumption
- number_id: R000-2C-IND-VAL-0005
  name: Value of vinegar in cakes, India
  value: 125000000  unit: INR  label: derived
  formula: QTY-0004 × PRC-xxxx      # price not stated in source notes; implied ₹41,859/t
```

---

## Part B — Audit of every number in the strategy notes

### B.1 Cross-strategy constants

| # | Number | What it is | Comment |
|---|---|---|---|
| 1 | 4 labels | verified_observed, derived, source_found_unverified, assumption | The WIP Estimator adds a fifth, `estimated_from_facts`. The system adopts five. |
| 2 | Value = quantity × price | Core identity | Holds only if quantity and price share unit, geography, year and value basis. Each conversion is its own ledger row. |

### B.2 Strategy 2A

| # | Number | Label | Derivation / meaning | Findings |
|---|---|---|---|---|
| 3 | 5 source tiers | design | national stats → FAOSTAT → UN Comtrade → industry bodies → web | Order is sensible. Comtrade supplies trade only, so for the driver it can fill imports/exports but never production. |
| 4 | Driver: production + imports − exports | formula | Apparent consumption per country | **Missing stock change.** Olive oil has large carry-over stocks that swing with alternate-bearing harvests, so one-year apparent consumption can be noticeably off. Use `− Δstocks` where a series exists (the IOC publishes balances on a crop-year basis). Also align crop year (Oct–Sep for olive oil) to base year explicitly. HS scope must be declared: HS 1509 (olive oil) vs 1510 (other olive oils, incl. pomace). |
| 5 | 4 segment dimensions | design | Product Type, Application, Channel, Form | Each dimension's per-country shares must sum to 1.0 (hard check); each table re-adds to the country total. |
| 6 | **$15.7B** | derived | Olive oil global value: Σ countries (quantity × price) | Base year, value basis (manufacturer vs consumer) and country count are not stated in the notes; they must be in the ledger. |
| 7 | **$15.7B (IMARC)** | source_found_unverified | Published market size used in the cross-check | Agreement at 3 s.f. Given rounding (each could be 15.65–15.75), the true gap is anywhere from 0% to ±0.6%. |
| 8 | 0% deviation (6 vs 7) | derived | (15.7 − 15.7) / 15.7 | **Caveats:** (a) agreement with one report is evidence of plausibility, not correctness; IMARC's scope and basis must be checked to match. (b) Confirm the run that produced $15.7B did **not** use the WIP Publisher, which blends toward consensus; if it did, the match is partly by construction. (c) The agent loop deliberately does not optimise closeness, so this number should not be used as the success metric. |
| 9 | Optional sanity check | assumption | Implied price = $15.7B ÷ global consumption | If global consumption is ~3 Mt (order of magnitude from IOC balances; verify), implied average ≈ $5,000/t, i.e. ≈ $5/kg. Whether that is plausible depends on the base year (olive oil prices spiked in 2023–24) and the value basis. Record actual tonnage from the run to make this check exact. |

### B.3 Strategy 2B

| # | Number | Label | Derivation / meaning | Findings |
|---|---|---|---|---|
| 10 | 3 example use cases | design | Home Cooking, Foodservice, Processed Food | Driver uniqueness rule prevents double counting. Note processed food and foodservice can overlap (foodservice buys processed food); the scope note must define which side owns it. |
| 11 | Foodservice driver: meals/yr × oil/meal | formula | Two inputs, each needs its own ledger row | "Oil per meal" is almost never published per country → usually `assumption`. |
| 12 | **1.5×** anchor band | design | Use-case sum must lie within 1.5× of the 2A country total | "Within about 1.5×" is read as the band [1/1.5, 1.5] = [0.667, 1.5]. Not specified: whether scaling goes **to the band edge or to the 2A total**. The system makes this a config switch (`anchor.target`) and logs the factor. |
| 13 | Scale-up condition | rule | Scale up only if 2A total is from official statistics | Implemented as `label == verified_observed` from tier 1–3. |
| 14 | 2 levels (India variant) | design | Major Use Case → Actual Use Case | Same driver-uniqueness and sum checks apply at both levels. |
| 15 | **$9.3B** | derived, weakest input = assumption | Olive oil global value from use cases | The notes say all inputs were estimates, so `weakest_input_label` is `assumption` or `estimated_from_facts`, not verified. |
| 16 | **64%** foodservice | derived | Foodservice value ÷ total | Foodservice ≈ **$5.95B**; all other use cases ≈ **$3.35B**. Triggers the dominance flag (> 50%). In major producing and consuming countries household use is generally expected to be large, so 64% foodservice warrants review of the meals × oil-per-meal inputs. |
| 17 | 2B ÷ 2A = **59.2%** | derived | 9.3 / 15.7 = 0.592 | Gap = **$6.4B**. Band floor at global level = 15.7 / 1.5 = **$10.47B**; ceiling = **$23.55B**. 9.3 sits **below the floor**. Since the anchor should scale up countries whose 2A total is official, possible reasons are: anchor not run (no 2A run linked); 2A totals in the big countries were not labelled official; or the anchor operates per country and the shortfall is concentrated in non-official countries. The run log must state which. |

### B.4 Strategy 2C — olive oil walkthrough

| # | Number | Label | Findings |
|---|---|---|---|
| 18 | 4 grades | design | Extra Virgin, Virgin, Refined, Pomace. Pomace may sit under HS 1510, not 1509; scope must say whether pomace is in. |
| 19 | Grade price gaps | assumption | Notes say "illustrative". They must stay `assumption` in any output and the 2C olive oil total inherits that as its weakest input. |
| 20 | Quantities | derived (from 2B) | Inherit the 2B issues in B.3 (#15–17). |
| 21 | Cell count | formula | cells = product types × applications × countries. Olive oil: 4 × 3 × C = 12C cells (e.g. 600 cells for 50 countries). |

### B.5 Strategy 2C — white vinegar, India

| # | Number | Label | Derivation | Value | Share of 291k t |
|---|---|---|---|---|---|
| 22 | Trade "tiny" | qualitative | imports and exports | not stated | must be numeric in ledger |
| 23 | **291,000 t** | top-down anchor; label not stated | total vinegar use | 291,000 t | 100% |
| 24 | 1,200,000 t | input | ketchup production | — | — |
| 25 | 8% | input | vinegar share of ketchup recipe | — | — |
| 26 | 70% | input, **meaning not stated** | likely share of ketchup made with white vinegar, or share of organised production | — | — |
| 27 | Ketchup vinegar | derived | 1,200,000 × 0.08 × 0.70 | **67,200 t** | 23.1% |
| 28 | 5.4% | input | vinegar share of mayonnaise recipe | — | — |
| 29 | Mayonnaise volume | **not stated** | — | — | — |
| 30 | Mayonnaise vinegar (implied) | derived (back-calculated) | 81,480 − 67,200 − 2,986.2 | **≈ 11,294 t** | ≈ 3.9% |
| 31 | Mayonnaise volume (implied) | derived (back-calculated) | 11,294 / 0.054 | **≈ 209,000 t** (range 180k–239k given rounding of 291k and 28%) | — |
| 32 | 378,000 t | input | cake production | — | — |
| 33 | 39.5% | input | eggless share of cakes | — | — |
| 34 | 2% | input (likely assumption) | vinegar dose in eggless cakes | — | — |
| 35 | Cake vinegar | derived | 378,000 × 0.395 × 0.02 | **2,986 t** | 1.0% |
| 36 | **28%** explained | derived | (67,200 + mayo + 2,986) / 291,000 | **81,480 t** | 28% |
| 37 | Unexplained | derived | 291,000 − 81,480 | **209,520 t** | **72%** |
| 38 | **₹12.5 crore** | derived | cake vinegar value | ₹125,000,000 | — |
| 39 | Implied price | derived (back-calculated) | 125,000,000 / 2,986.2 | **₹41,859/t ≈ ₹41.9/kg** | — |
| 40 | **US$1.5M** | derived | ₹12.5 crore / FX | US$1.5M | — |
| 41 | Implied FX | derived | 125,000,000 / 1,500,000 | **₹83.3 per US$** (USD figure is rounded; exact FX must be in ledger) | — |
| 42 | Whole-market value at cake price | derived, weakest = assumption (uniform price) | 291,000 × 41,859 | **≈ ₹1,218 crore ≈ US$146M** | — |

Findings for B.5:

- **Ratios worth stating up front:** cakes are about 1% of vinegar volume and roughly 1% of the implied market value. This is the concrete case of "each cell is small, so researching it can cost more than it's worth"; the cell budget in system.md §7.4 would send it to the estimator.
- **72% is unexplained.** The recipe build covers three uses only. The top-down 291k t must remain the total, and the residual must be shown as an "unattributed" line. Candidate missing uses (to test, all currently `assumption`): pickles and preserves, other sauces (chilli, soy-based, Indo-Chinese), household use, foodservice kitchens, and non-food or cleaning uses if in scope.
- **Factor 70% (#26) needs a definition** before it can be documented; its meaning changes which evidence can verify it.
- **Mayonnaise volume (#29) is missing** from the notes. The back-calculated ≈209k t is a consistency check, not a fact, and must not be used as an input.
- **Label of 291k t (#23)** is unknown. Since the whole 2C build is judged against it, its source and label are the single most important item to document.

---

## Part C — Constants and thresholds

| Constant | Value | Origin |
|---|---|---|
| Anchor band | 1.5 (band [0.667, 1.5]) | strategy notes |
| Anchor target | `ceiling` | design default |
| Max agent passes | 3 | design default |
| Min plausibility score | 7.0 / 10 | design default |
| Plausibility weights | 0.40 / 0.20 / 0.20 / 0.10 / 0.10 | design default |
| Sum-conservation tolerance | 0.1% | design default |
| Share-sum tolerance | ± 0.001 | design default |
| Triangulation warning | 25% | design default |
| Use-case dominance flag | 50% | design default (would have flagged the 64%) |
| Min verified coverage | 50% of value | design default |
| 2C min explained share | 60% of anchor | design default (vinegar at 28% would flag) |
| 2C min cell share to research | 0.5% of anchor | design default |
| Price outlier rule | > 3× IQR from median | design default |
| Publisher weight on bottom-up | 0.5 | design default |
| Display | 3 s.f. values, 1 dp percentages | design default |

Every "design default" is a starting point to be tuned on golden runs; changes are recorded in the run config hash.

---

## Part D — Open questions (block full documentation)

1. Base year, value basis and country count behind the $15.7B 2A result.
2. Whether the Publisher was active in the $15.7B run.
3. IMARC report year, scope (does it include pomace?) and value basis.
4. Whether the 2B run was anchored to 2A, and in which countries scaling occurred.
5. Source and label of the 291k t vinegar anchor; numeric import/export volumes.
6. Meaning of the 70% ketchup factor; mayonnaise volume used.
7. Sources for 1.2 Mt ketchup, 378k t cakes and 39.5% eggless share.
8. Exact FX rate and vinegar price used for ₹12.5 crore → US$1.5M.
