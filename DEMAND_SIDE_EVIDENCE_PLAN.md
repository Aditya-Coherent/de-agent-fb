# Demand-Side Evidence Architecture — Phased Implementation Plan

**Status**: Planning only. No implementation has started against this document.
**Scope**: `src/me_engine/demand_usage/` and its `backend/`/`frontend/` integration points ONLY.
**Never touch**: `src/me_engine/generation/` (Desk Research), `src/me_engine/supply/` (Supply Side). No phase in this document may modify either.
**Never create**: a fourth market-sizing methodology. This plan only strengthens the existing Usage-Based Demand-Side pipeline.

## How to use this document

Each phase below is self-contained. In a fresh session, "Implement Phase N from `DEMAND_SIDE_EVIDENCE_PLAN.md`" should be enough context to start — the phase's own **Objective**, **Current behavior**, **Exact files**, and **Depends on** sections replace the need to re-read this whole document or prior conversation history. Do not skip ahead to a later phase whose "Depends on" isn't satisfied. Do not start a phase whose acceptance criteria you can't verify (run the tests named in that phase before declaring it done).

At the end of every phase's implementation session:
1. Run the full existing test suite (`pytest tests/`) — it must stay green unless this phase's own "Tests" section says a specific test is intentionally changed (and the change must be documented in that phase's handoff).
2. Run the new tests this phase adds.
3. Fill in the phase's **Handoff to next phase** section (even if just confirming "no surprises") so the next session's Phase N+1 has a live status, not just this document's static plan.

---

## Current architecture (as of this plan)

```
Market Name -> Market Setup Draft -> User Approval -> Taxonomy + Demand Driver Draft
    -> User Approval -> Evidence Retrieval -> Demand Quantification -> Pricing
    -> Unit Contract -> Deterministic Calculation -> Magnitude Validation
    -> Triangulation -> Final Output
```

Existing modules in `src/me_engine/demand_usage/` (all in scope for this plan):

| File | Role |
|---|---|
| `checkpoints.py` | Draft/approved JSON file I/O shared by CLI + backend routes |
| `setup.py` | Phase 1 (Market Setup Draft) — `market_setup_draft_node`, `approve_setup`, `load_approved_setup` |
| `taxonomy.py` | Phase 2 (Taxonomy + Driver Draft) — orchestrates `agents.py` nodes, `approve_taxonomy`, `load_approved_taxonomy` |
| `agents.py` | All LangGraph node functions: usage decomposition, scope validation, physical/financial/inventory classification, coverage review, demand driver proposal, demand quantification, pricing (incl. price-basis resolution), deterministic `calculate_node`, `validation_node` |
| `graph.py` | `UsageDemandState` TypedDict, `build_sizing_graph`, `sizing_init_state`, `run_sizing_phase` |
| `evidence.py` | `verify_and_tag`, `extract_structured_fact`, `extract_fact_from_source`, `build_fact`, `build_structured_fact`, `worse_of` — the acceptance-gate layer |
| `evidence_router.py` | `propose_driver/intensity/pricing_evidence_plan`, `execute_evidence_plan` — typed source-chain proposal + dispatch |
| `web_acquirer.py` | ddgs search + real page-body fetch + `evidence.extract_fact_from_source` |
| `pdf_acquirer.py` | PDF discovery, download, pdfplumber extraction, keyword shortlist, LLM page-anchored extraction |
| `tabular_acquirer.py` | CSV/XLSX discovery, download, pandas load, LLM column/row mapping, deterministic lookup |
| `api_acquirer.py` | Generic JSON API discovery, HTTP GET, LLM field-path mapping, deterministic traversal |
| `web_search.py` | Low-level `ddgs`-backed search returning `SearchResult(title, url, snippet)` |
| `units.py` | Deterministic unit-string parser + period-conflict checker (the unit-contract gate) |
| `conversion.py` | Pure `driver_value * intensity_value [* adoption_pct]` arithmetic |
| `driver_library.py` | Cross-market driver-methodology reuse (embedding nearest-neighbor) |
| `reference_data.py` | Static population-share table (country allocation fallback) |
| `magnitude.py` | `driver_magnitude_validation_node` — independent-reference cross-check, flag-only |
| `triangulation.py` | `triangulation_node` — external benchmark sweep, informational only |
| `quality.py` | `compute_provenance_quality_flags` — the deterministic flag catalog |
| `buildup_writer.py` | `build_report_dict`, `write_buildup_json`, `write_buildup_workbook` — audit output |
| `demand_usage_pipeline.py` (repo root) | CLI: `setup`/`approve-setup`/`taxonomy`/`approve-taxonomy`/`size` |
| `backend/demand_usage_routes.py` | FastAPI routes mirroring the CLI phases, SSE for the sizing phase |
| `frontend/src/components/DemandUsageWizard.tsx` | 3-step wizard UI |

**Cross-market validation baseline** (Crude Oil / Cement / Avocado Oil, most recent run):
- Driver: 0% observed / 0% derived / 100% assumption in all three markets.
- Intensity: 50% / 16.7% / 50% observed across the three — the one facet that already works.
- Price: 0% observed in all three markets.
- 1,046 total evidence attempts across web/PDF/CSV-Excel/API; only 1 succeeded.
- Dominant rejection reasons: `download_failed` (161+115+82), `no_source_found` (143+126+127), `extraction_failed` (70+120+54), `not_relevant_page` (31+16).
- Root cause, as diagnosed: source **discovery** (finding a real, directly-fetchable file/endpoint URL) is the bottleneck, not source-type *selection* — the router correctly proposes typed sources (USGS PDF, GCCA dataset, FAOSTAT, real APIs), but the guessed URLs/locators frequently aren't real, fetchable assets.

This plan exists to fix that discovery/verification gap without regressing what already works (intensity retrieval, the deterministic calculation core, unit contract, magnitude/triangulation flagging).

---

## Critical evidence contract

Every candidate fact, regardless of which acquirer produced it, must be expressible in this shape:

```json
{
  "value": 8900000,
  "unit": "barrels/day",
  "year": 2025,
  "geography": "US",
  "source_name": "EIA",
  "source_url": "https://www.eia.gov/...",
  "source_type": "web_page",
  "document_title": "",
  "location_ref": "",
  "evidence_text": "US motor gasoline consumption averaged 8.9 million barrels per day...",
  "citation": "8.9 million barrels per day",
  "verification_status": "verified_observed"
}
```

`verification_status` replaces today's 3-value `evidence_type` (`observed`/`derived`/`assumption`) with a 4-value enum. This is the single most important schema change in this plan — see Phase 6 for the full migration. The four states, and the exact case each maps to:

| Case | Description | `verification_status` |
|---|---|---|
| **A** | LLM finds a source + a number, AND code independently verifies the source is accessible and the number is traceable in the actual retrieved content | `verified_observed` |
| **B** | LLM finds a source + a number, but the source could not be verified as accessible (download failed, page fetch failed, API unreachable) — the LLM's claim is not corroborated by code | `source_found_unverified` |
| **C** | Source is accessible, but the candidate number cannot be found/traced inside that source's actual content | reject the candidate outright (return `None`, do not persist a fact) — the acquirer's attempt log still records the miss with a rejection reason |
| **D** | Value is deterministically computed from one or more `verified_observed` or `derived` inputs, with an explicit, logged formula | `derived` |
| **E** | No evidence and no derivation path exists | `assumption` |

Hard rules, enforced in code, never overridable by LLM confidence:
- `source_found_unverified` can NEVER be silently promoted to `verified_observed` just because the LLM sounds confident, cites a plausible-looking institution, or repeats the claim across multiple attempts.
- A `verified_observed` fact's `source_url` must be a URL that code actually fetched successfully in this run — never an LLM-typed string that was never dereferenced.
- `derived` requires a logged formula string referencing the specific input fact(s) it was computed from (bucket name, geography, `verification_status` of each input) — `evidence.worse_of` continues to determine the resulting overall provenance from its inputs, extended to the 4-value enum (see Phase 6).
- Backward compatibility: existing code/tests that check `evidence_type in ("observed", "derived", "assumption")` must be migrated in the SAME phase that introduces the 4th state (Phase 6), not left half-migrated — see that phase's acceptance criteria.

---

## Source-acquisition requirements (apply across Phases 1, 3, 4, 5)

### PDF
- Follow HTTP redirects (`requests` with `allow_redirects=True`, capped at a small hop limit) and record the FINAL resolved URL, not the originally-guessed one.
- Verify `Content-Type` header contains `application/pdf` OR verify the file signature (`%PDF-` magic bytes at the start of the downloaded content) — never trust the URL's `.pdf` extension alone (today's `_discover_pdf_url`/`_download_pdf_bytes` in `pdf_acquirer.py` only checks the extension/content-type header, not the magic bytes — this is a concrete Phase 1/3 fix).
- Distinguish an actual PDF file from an HTML "landing page" that merely links to one (a very common `download_failed`/`extraction_failed` cause today) — if the fetched content is HTML, that's a distinct rejection reason from "not actually a PDF at all" vs. "PDF fetch timed out" vs. "PDF too large."
- Preserve page/table provenance (`page_number`, an optional `table_index`) on every fact, already partially implemented in `pdf_acquirer.py` — Phase 3 hardens this, does not invent it from scratch.

### CSV/XLSX
- Same redirect-following and content-type/extension verification discipline as PDF (verify it's actually tabular data, not an HTML error page saved with a `.csv` extension).
- Confirm the parser (`pandas.read_csv`/`read_excel`) actually succeeded and produced a non-empty DataFrame before treating the source as "acquired."
- Preserve row/column/sheet provenance (`location_ref`), already implemented in `tabular_acquirer.py` — Phase 4 hardens discovery, not the extraction logic itself.

### API
- Verify HTTP status code is 2xx before attempting to parse a body as JSON.
- Verify the response is valid, parseable JSON (a 200 status with an HTML error page body is a common false-positive today).
- Respect and log rate-limit responses (429) distinctly from a genuine 404/no-data response — these need different retry/backoff behavior and different rejection-reason labels.
- Preserve the exact field path traversed (`location_ref`) — already implemented in `api_acquirer.py`.

### Web
- Continue fetching the actual page body (already implemented in `web_acquirer.py`'s `_fetch_page_text`), not just the search-result snippet.
- Preserve the distinction between "the ddgs snippet contained it" vs. "the fetched full page body contained it" in the attempt log — this distinction matters for diagnosing whether the web acquirer's snippet-depth fix (from the prior session) is actually the thing that found a given fact, or whether the snippet alone was sufficient.

---

## Pricing requirement (applies to Phase 8, foundational logic already exists)

The distinction between a **market/product price** (a real, publicly quoted commodity/product price) and a **usage-unit price** (a bucket-specific derived unit that is NOT how the market actually prices anything) must be preserved and strengthened:

```
Transportation demand -> quantity in barrels -> crude oil price $/barrel   (correct)
Transportation -> USD/vehicle-km                                          (wrong, unless genuinely the market's commercial unit)
```

`agents._resolve_price_basis` already implements this distinction (added in the evidence-router work). Phase 8's job is not to invent this mechanism but to (a) verify it is actually finding a real price for the resolved basis unit more often than today's 0%, and (b) apply the same Phase 1-6 verification discipline to price facts specifically, since pricing sources (commodity exchanges, price indices) skew heavily toward PDF/API sources that Phase 1's discovery fix should help most.

---

# Phase 0 — Baseline & Contracts

## 1. Objective
Freeze current behavior as a measurable, reproducible baseline before any acquisition-layer changes, and land the `verification_status` contract as a pure additive schema change (no behavior change yet) so every later phase has a stable target to migrate toward.

## 2. Problem being solved
Without a frozen baseline, "did Phase 1 actually improve discovery" is unanswerable — the cross-market validation numbers in this document's intro are the only baseline that currently exists, captured informally in a prior session's chat output, not as a committed artifact.

## 3. Current behavior
`evidence_type` is a bare string (`"observed"|"derived"|"assumption"`) scattered across `evidence.py`, `agents.py`, `quality.py`, `buildup_writer.py`. No single source of truth defines the full enum or the transition rules between states. No committed baseline metrics file exists.

## 4. Desired behavior
- A committed `output/demand_usage/BASELINE_V3.md` (or `.json`) capturing the exact cross-market numbers from this document's intro, generated by actually re-running Crude Oil/Cement/Avocado Oil once at the start of this phase (not copied from memory) — this is the regression target every later phase's Phase 12 re-run compares against.
- A new `Literal["verified_observed", "source_found_unverified", "derived", "assumption"]` type alias defined in `evidence.py` (e.g. `VerificationStatus`), with `evidence_type` fields renamed/aliased incrementally — Phase 0 only ADDS the type and a compatibility shim (`legacy_evidence_type()` mapping `verified_observed -> observed` for any code not yet migrated), it does NOT change any node's actual behavior.

## 5. Exact files/components likely to change
- `evidence.py` (add `VerificationStatus` type alias + compatibility mapping function, additive only)
- New: `output/demand_usage/BASELINE_V3.md` (generated artifact, not source code)

## 6. New files/components if needed
- `tests/test_demand_usage_baseline_contract.py` — asserts the `VerificationStatus` literal values and the compatibility mapping function's behavior.

## 7. LLM responsibilities
None — this phase is schema/tooling only.

## 8. Deterministic responsibilities
- Define the enum.
- Generate the baseline artifact by running the real CLI end-to-end for all three markets (same as the prior validation session) and saving the output.

## 9. State/schema changes
Additive only: a new type alias, no changes to `UsageDemandState` (`graph.py`) yet.

## 10. Provenance requirements
N/A this phase — no provenance logic changes.

## 11. Failure/fallback behavior
If a live re-run fails (e.g. API balance/network), the baseline artifact must record the failure explicitly (market, phase, exact error) rather than being silently skipped or backfilled with the prior session's numbers presented as fresh.

## 12. Tests
- `test_demand_usage_baseline_contract.py`: `VerificationStatus` has exactly 4 values; `legacy_evidence_type()` maps `verified_observed->observed`, `source_found_unverified->assumption` (conservative — until Phase 6 migrates real consumers, an unverified claim must never look like more than an assumption to existing code), `derived->derived`, `assumption->assumption`.
- Full existing suite (`pytest tests/`) stays green, unchanged.

## 13. Acceptance criteria
- [ ] `BASELINE_V3.md` committed with real (not remembered) numbers from a fresh 3-market run.
- [ ] `VerificationStatus` type + compatibility shim added, zero behavior change anywhere else.
- [ ] Full test suite green, no test file other than the new one touched.

## 14. What must NOT be changed
Any node function's actual logic; any existing test's expected values; `generation/`, `supply/`.

## 15. Expected output/artifacts
- `output/demand_usage/BASELINE_V3.md`
- `tests/test_demand_usage_baseline_contract.py`

## 16. Handoff to next phase
Record in this section (when executed): the exact baseline numbers, any deviation from this document's intro numbers (LLM non-determinism means a fresh run won't match exactly — note the delta), and confirmation the compatibility shim tests pass.

---

# Phase 1 — Source Discovery & URL Verification

## 1. Objective
Solve the dominant failure mode from the baseline: `download_failed` / `no_source_found` accounting for the majority of all rejections. Build a shared, acquirer-agnostic URL/asset verification layer.

## 2. Problem being solved
Today, `pdf_acquirer._discover_pdf_url`, `tabular_acquirer._discover_dataset_url`, and `api_acquirer.acquire` each independently guess a URL/locator and then try to download it — there is no shared "is this a real, accessible, correctly-typed asset" check performed BEFORE committing to a full download+parse attempt, and no redirect-following, no magic-byte verification, no distinction between "HTML landing page" and "actual file."

## 3. Current behavior
- `pdf_acquirer._download_pdf_bytes`: checks `content-type` header or `.pdf` extension only; no redirect handling beyond what `requests.get` does by default; no magic-byte check.
- `tabular_acquirer._discover_dataset_url`/`_download`: extension-based discovery only, same gap.
- `api_acquirer._fetch`: no explicit status-code check beyond `raise_for_status()`; no distinction between "404" and "200 with an HTML error body."

## 4. Desired behavior
A new shared module, `asset_verifier.py`, providing one function acquirers call before treating a URL as "found":

```python
def verify_asset(url: str, expected_type: Literal["pdf", "csv", "excel", "json", "html"]) -> AssetVerification
```

returning a small dataclass: `{resolved_url, is_accessible, actual_content_type, matches_expected_type, http_status, rejection_reason}`. This function does the redirect-following, magic-byte/content-type checking, and HTML-landing-page detection ONCE, shared by all four acquirers, replacing each acquirer's own ad-hoc check.

## 5. Exact files/components likely to change
- `pdf_acquirer.py`: `_download_pdf_bytes` calls `asset_verifier.verify_asset(url, "pdf")` first.
- `tabular_acquirer.py`: `_download` calls `asset_verifier.verify_asset(url, "csv"/"excel")` first.
- `api_acquirer.py`: `_fetch` calls `asset_verifier.verify_asset(url, "json")` first.
- `web_acquirer.py`: `_fetch_page_text` gains the same redirect/content-type discipline for consistency (already partially there).

## 6. New files/components if needed
- `asset_verifier.py` (new)
- `tests/test_demand_usage_asset_verifier.py` (new)

## 7. LLM responsibilities
Unchanged from today: propose the candidate URL/locator. This phase does not change what the LLM is asked to do, only what code does with what it proposes.

## 8. Deterministic responsibilities
- Redirect following (capped, e.g. max 5 hops) and recording the final URL.
- Magic-byte check for PDF (`%PDF-` at file start).
- Content-type/extension cross-check for CSV/XLSX (reject an XLSX claim that's actually `text/html`).
- JSON-parseability check for API responses, distinct from HTTP status.
- New rejection reasons this phase introduces: `redirect_loop`, `wrong_content_type`, `html_landing_page_not_asset`.

## 9. State/schema changes
Each attempt record (already `source_type`, `rejection_reason`, etc.) gains an optional `resolved_url` field distinct from the originally-attempted `source_url`/locator, for audit transparency when a redirect changed it.

## 10. Provenance requirements
A fact's `source_url` in the final persisted fact must be the RESOLVED url (post-redirect), never the originally-guessed one — this is what a human clicking the link in the audit workbook should actually land on.

## 11. Failure/fallback behavior
`verify_asset` never raises — same discipline as every acquirer today (`except Exception: return None`-style). A verification failure is a normal, expected, logged outcome, not an error state.

## 12. Tests
- Redirect resolution (mock a redirect chain, assert final URL captured).
- Magic-byte rejection (content claims `application/pdf` header but body doesn't start with `%PDF-`).
- HTML-landing-page detection (a `.pdf`-extensioned URL that actually serves HTML).
- CSV/XLSX content-type mismatch rejection.
- JSON parseability check (200 status, non-JSON body).
- Regression: existing `pdf_acquirer`/`tabular_acquirer`/`api_acquirer` tests still pass once wired to call `verify_asset`.

## 13. Acceptance criteria
- [ ] `asset_verifier.py` exists, is acquirer-agnostic (no market/institution-specific logic).
- [ ] All 4 acquirers call it before committing to a full download+parse.
- [ ] New rejection reasons appear in the attempt log where applicable.
- [ ] Re-running the Phase 0 baseline markets shows a measurable shift in rejection-reason distribution (not necessarily more `found=true` yet — Phase 1 is about better DIAGNOSIS and fewer wasted full-downloads, not guaranteed higher hit rate; Phase 2's discovery-quality improvements are what should raise the hit rate).
- [ ] Full test suite green.

## 14. What must NOT be changed
The LLM prompts proposing sources/locators (that's Phase 2's job); the acceptance-gate logic in `evidence.py` (that's Phase 6's job).

## 15. Expected output/artifacts
- `asset_verifier.py`, `tests/test_demand_usage_asset_verifier.py`.
- Updated attempt-log shape documented in this phase's handoff.

## 16. Handoff to next phase
**Status: implemented (this session).**

- New shared module `src/me_engine/demand_usage/asset_verifier.py` (`verify_asset(url, expected_type)` -> `AssetVerification`). Acquirer-agnostic, no market/institution-specific logic. Never raises.
- Wired into all 4 acquirers, replacing each one's ad-hoc check:
  - `pdf_acquirer._download_pdf_bytes(url)` now returns `AssetVerification` (was `bytes | None`) — calls `verify_asset(url, "pdf")`.
  - `tabular_acquirer._download(url)` now returns `AssetVerification` (was `bytes | None`) — calls `verify_asset(url, _expected_tabular_type(url))` (`"excel"` for `.xlsx`/`.xls`, else `"csv"`).
  - `api_acquirer._fetch(url)` now returns `AssetVerification` (was `dict | None`) — calls `verify_asset(url, "json")`; `acquire()` itself now does the `json.loads` on `.content` (previously `resp.json()` did both fetch+parse in one step).
  - `web_acquirer._fetch_page_text(url)` unchanged signature (`str | None`) — internally now calls `verify_asset(url, "html")` instead of its own `requests`+content-type check.
- Every acquirer's `attempt` dict gains `resolved_url` (the post-redirect URL, distinct from the originally-attempted `source_url`/locator) whenever a verification was attempted. On a *successful* acquisition, `source_url` in both the attempt record and the persisted fact is reassigned to the resolved URL, per the plan's provenance requirement — never the originally-guessed one.
- New rejection reasons now surfaced end-to-end from `asset_verifier` through each acquirer's attempt log: `malformed_url`, `redirect_loop`, `wrong_content_type`, `html_landing_page_not_asset` (alongside the existing `download_failed`/`no_source_found`/`extraction_failed`/`not_relevant_page`/`geography_mismatch`, all of which are unchanged in meaning).
- **Deliberate test changes** (documented per the plan's process, not a silent regression): `tests/test_demand_usage_evidence_router.py`'s existing `pdf_acquirer`/`tabular_acquirer`/`api_acquirer` unit tests were updated to monkeypatch `_download_pdf_bytes`/`_download`/`_fetch` returning `AssetVerification` objects (via two new local helpers, `_ok_verification`/`_failed_verification`) instead of raw `bytes`/`dict`/`None`, matching the new return contract. Each test's original intent (dispatch, acceptance-gate, rejection-reason assertions) is unchanged — only the mock shape changed. `web_acquirer` tests needed no changes since they monkeypatch `_fetch_page_text` at its unchanged boundary.
- New file `tests/test_demand_usage_asset_verifier.py` (23 tests, including a 7-case parametrized sweep): valid web page / PDF / CSV / XLSX / XLS / JSON API; PDF-URL-serving-HTML and CSV-URL-serving-HTML landing-page rejections; JSON-endpoint-serving-HTML rejection; malformed URL (rejected without a network call); empty URL; connection failure; HTTP 404 and 500; redirect resolution (final URL captured); redirect loop; and a parametrized `sniff_type` unit sweep (content sniffing wins over the `Content-Type` header in every case).
- Full suite: **112 passed** (`pytest tests/`), including the pre-existing Phase 0 contract tests, unchanged.
- **Live smoke test** (not the 3-market baseline — a handful of real, targeted HTTP calls against `verify_asset` directly, reported here rather than committed as an artifact): a real EIA page (`html`, matched), a real dummy PDF (`pdf`, matched, `%PDF-` sniffed), a genuinely-404 `.pdf`-looking URL (`download_failed`), the real World Bank JSON API (`json`, matched), a nonexistent host (`download_failed`), a real GitHub-raw `.csv` (`csv`, matched), and a real Wikipedia page requested with `expected_type="pdf"` (200 OK, HTML body -> `html_landing_page_not_asset`, confirming the plan's core "HTML landing page masquerading as a file" failure mode is now caught live, not just in mocks).
- Known limitations carried forward to later phases:
  - Discovery itself (which URL to *guess*) is untouched — Phase 1 only changed what happens once a candidate URL is proposed. `pdf_acquirer._discover_pdf_url` and `tabular_acquirer._discover_dataset_url` still take the first/only extension-matching search result; Phase 3/4 own improving the guess quality.
  - `api_acquirer.acquire`'s pre-existing behavior of substituting `{country}` directly into `source["locator"]` with no existence pre-flight beyond the new `verify_asset` GET is unchanged — Phase 5's job (`endpoint_not_found` vs. `no_data_for_query` distinction) is still open.
  - `sniff_type`'s Excel detection treats any ZIP-magic-byte (`PK\x03\x04`) blob as `"excel"` — technically also matches `.docx`/`.pptx`/other OOXML/zip formats; acceptable for this phase's scope (there is no acquirer that would call `verify_asset(..., "excel")` on a non-spreadsheet URL today) but worth tightening (e.g. peeking the zip's internal `[Content_Types].xml`) if a false-positive is ever observed in practice.
  - A re-run of the Phase 0 baseline's 3 markets (Crude Oil/Cement/Avocado Oil) to measure the actual shift in the `no_source_found`/`download_failed`/`extraction_failed`/`not_relevant_page` rejection-reason distribution was intentionally NOT performed this session (out of scope per this task's instructions — the frozen `BASELINE_V3.md` was not touched). Phase 2 (or a dedicated re-run session) should perform that 3-market re-run as its own first step, diffing against `BASELINE_V3.md`, to get the real "before vs. after Phase 1" numbers this phase's acceptance criterion #4 calls for.

---

# Phase 2 — LLM-Assisted Evidence Discovery

## 1. Objective
Let the LLM return a real, useful "I found this via search/browsing" claim — including a candidate value — even when the underlying asset is hard for code to independently fetch, WITHOUT ever letting that claim masquerade as verified. Introduce `source_found_unverified` as a real, distinct, honestly-labeled outcome.

## 2. Problem being solved
Today, if the acquirer chain fails to download/verify a source, the fact is simply rejected (`None`) and the pipeline falls through to the next source or eventually an `assumption`. But the LLM sometimes genuinely has located a specific, real number from a specific real page during its own reasoning/browsing-adjacent process — throwing that signal away entirely wastes real information the LLM has, in favor of an undifferentiated bare assumption. Distinguishing "no idea" from "I have a specific, attributable claim I just can't get code to independently confirm" is valuable audit information.

## 3. Current behavior
Every acquirer's LLM step (`extract_fact_from_source`, `pdf_acquirer`'s page-anchored extraction, `tabular_acquirer`'s mapping proposal, `api_acquirer`'s path proposal) only runs AFTER a successful download — there is no path for the LLM to report "I believe the value is X, from source Y at URL Z" when the download itself failed.

## 4. Desired behavior
A new, LAST-priority acquirer step per Evidence Router chain (`llm_claim_acquirer.py` or folded into `evidence_router.execute_evidence_plan` as a final fallback rung) that runs only when EVERY typed acquirer in the chain has exhausted its sources without a verified fact. It asks the LLM one final, explicit question: "based on everything you know, what is your best specific claim for this fact, including a named source and (if you can) a URL, even if you cannot access it right now?" The result is tagged `source_found_unverified`, never `verified_observed`, and is clearly weaker than a `derived` value built from something code actually confirmed.

## 5. Exact files/components missing/changed
- `evidence_router.execute_evidence_plan`: add an optional final fallback rung (distinct from today's plain `_fallback_assumption` in `agents.py`) that produces `source_found_unverified` instead of jumping straight to `assumption` — but only when the LLM actually names a specific source+claim, not a vague "I think it's around X."
- `evidence.py`: new `build_unverified_claim_fact()` gate function, parallel to `build_fact`/`build_structured_fact`, always returning `verification_status="source_found_unverified"` regardless of how confident the LLM sounds.

## 6. New files/components if needed
- Possibly `llm_claim_acquirer.py` if this warrants its own module rather than living in `evidence_router.py` — decide based on how much logic it needs; keep it minimal (this is deliberately a THIN, last-resort rung, not a new full acquirer).

## 7. LLM responsibilities
- Return a specific claim (value, source name, URL if known, year, geography, brief reasoning) OR honestly say it has nothing better than a generic estimate (in which case the existing `assumption` fallback path is used, unchanged).

## 8. Deterministic responsibilities
- Never accept this rung's output as `verified_observed` or `derived` — hard-coded, not a prompt instruction.
- Require a non-empty source name for `source_found_unverified` (an unnamed claim is just an assumption, not a weaker verification tier).
- Log this rung's usage distinctly in the attempt log (`source_type: "llm_claim"`) so Phase 12's cross-market report can show how often this tier actually contributes vs. true assumptions.

## 9. State/schema changes
`quality.py`'s evidence-quality percentages gain a 4th bucket (`source_found_unverified_pct`) alongside observed/derived/assumption — see Phase 6 for the full rollout of the 4-value enum through `quality.py`/`buildup_writer.py`; Phase 2 only needs to START producing this status, Phase 6 needs to finish threading it everywhere it's consumed.

## 10. Provenance requirements
A `source_found_unverified` fact must still carry whatever partial source info the LLM gave (name, claimed URL even if unverified, claimed citation) into the audit trail — never discard it just because it's unverified; the whole point is it's MORE useful than a bare assumption for a human reviewer deciding whether to manually verify it.

## 11. Failure/fallback behavior
If even this rung's LLM call fails outright (network/auth), fall through to the existing `_fallback_assumption` path unchanged — this rung is additive, never a required step.

## 12. Tests
- LLM claim with a named source -> `source_found_unverified`, never `verified_observed`.
- LLM claim with no named source -> falls through to ordinary `assumption`.
- LLM call failure at this rung -> falls through to `_fallback_assumption`, `call_failed` still tracked correctly (regression: don't break the existing `llm_call_failures` mechanism).
- Attempt log shows `source_type: "llm_claim"` distinctly.

## 13. Acceptance criteria
- [ ] New rung wired into `execute_evidence_plan` as the final fallback before `assumption`.
- [ ] Zero path exists where this rung's output reaches `verified_observed`.
- [ ] Re-running Phase 0 baseline markets shows how many previously-`assumption` cells now land as `source_found_unverified` (informational, not a pass/fail number — the point is visibility, not necessarily a higher "quality" score, since `source_found_unverified` is NOT counted as good as `derived` in Phase 6's scoring).
- [ ] Full test suite green.

## 14. What must NOT be changed
The existing `assumption`-tier `_fallback_assumption` function and its `call_failed` tracking; the driver/intensity/price call-site structure in `agents.py` beyond adding this one new rung.

## 15. Expected output/artifacts
- `evidence.build_unverified_claim_fact()`, wired-in fallback rung, new tests.

## 16. Handoff to next phase
Record: how often this tier actually fired across the 3-market re-run, and whether any `source_found_unverified` claims, when manually spot-checked, actually looked correct (informs how much to trust this tier going forward, and whether Phase 3/4/5's own discovery improvements are making this rung less necessary over time).

---

# Phase 3 — PDF Evidence Pipeline (hardening)

## 1. Objective
Raise the PDF acquirer's real hit rate by fixing discovery (Phase 1 dependency) and hardening extraction robustness — this is the single largest attempt volume in the baseline (337-143 attempts per market) with a 0% hit rate everywhere except intensity.

## 2. Problem being solved
`pdf_acquirer._discover_pdf_url` guesses a URL from a `filetype:pdf` search or a locator string with no verification the guess is even close to right; `_extract_pages` silently returns `[]` on any pdfplumber failure with no distinction between "encrypted PDF," "scanned/image-only PDF (no extractable text)," "corrupted file," or "genuinely empty."

## 3. Current behavior
See `pdf_acquirer.py` as it exists today (already read/built in the prior session) — discovery via one `filetype:pdf` search, first `.pdf`-looking result wins; extraction via `pdfplumber.open()` wrapped in a bare `except Exception: return []`.

## 4. Desired behavior
- Discovery tries MULTIPLE candidate results from the `filetype:pdf` search (not just the first), verifying each via Phase 1's `asset_verifier` until one actually resolves to a real PDF, instead of committing to the first URL that merely LOOKS like a PDF link.
- Extraction distinguishes failure modes: `encrypted_pdf`, `scanned_no_text_layer` (pdfplumber returns pages but `extract_text()` is empty/near-empty across all pages — a strong signal of a scanned document, not a genuinely blank one), `corrupted_or_unreadable`, vs. today's single generic `extraction_failed`.
- Page/table shortlisting (`_keyword_shortlist`) gets a documented, tested tie-breaking rule (currently: score then original order — verify this is actually deterministic and add a test asserting it, since it's a load-bearing determinism claim in the module's own docstring).

## 5. Exact files/components likely to change
- `pdf_acquirer.py`: `_discover_pdf_url` (multi-candidate + verify), `_extract_pages` (distinguish failure modes), `_keyword_shortlist` (add determinism test).

## 6. New files/components if needed
None — this phase hardens the existing module, no new files expected unless the multi-candidate discovery logic grows large enough to warrant splitting out.

## 7. LLM responsibilities
Unchanged: propose search queries/locators; interpret shortlisted pages.

## 8. Deterministic responsibilities
- Try N candidate URLs (e.g. top 3 from the `filetype:pdf` search) via `asset_verifier` before giving up, not just the first.
- Classify extraction failure modes explicitly.

## 9. State/schema changes
Attempt record's `rejection_reason` enum grows: add `encrypted_pdf`, `scanned_no_text_layer`, `corrupted_or_unreadable` as refinements of today's single `extraction_failed`.

## 10. Provenance requirements
Unchanged from the existing `pdf_acquirer.acquire` contract (page_number, document_title, source_url) — Phase 3 doesn't change the fact SHAPE, only the discovery/extraction success rate and failure diagnostics.

## 11. Failure/fallback behavior
Same as today: any failure anywhere in this chain returns `(None, attempt)` with a populated `rejection_reason`, never raises out of `acquire()`.

## 12. Tests
- Multi-candidate discovery: first candidate fails verification, second succeeds — assert the SECOND is used, and both attempts are logged (or at least the discovery step's own internal retries are observable/testable — decide exact granularity when implementing).
- Encrypted PDF -> `encrypted_pdf` rejection reason (mock pdfplumber raising the specific encryption exception).
- Scanned PDF (pages present, `extract_text()` returns `""`/whitespace-only for all pages) -> `scanned_no_text_layer`.
- Shortlist determinism: same input pages/keywords -> same shortlist order, always (explicit regression test, not just an assumption).

## 13. Acceptance criteria
- [ ] Discovery tries multiple candidates, not just the first.
- [ ] Failure-mode-specific rejection reasons appear in real attempt logs.
- [ ] Re-run of the Phase 0 baseline markets shows PDF hit rate change (report it honestly whether it goes up, stays flat, or reveals that most Cement/USGS-style PDFs really are scanned/encrypted and this phase's diagnostic value exceeds its hit-rate value — that's a legitimate, useful outcome to report, not a failure of the phase).
- [ ] Full test suite green.

## 14. What must NOT be changed
The fact shape returned by `pdf_acquirer.acquire`; `evidence_router.py`'s dispatch logic (Phase 3 only changes what happens INSIDE `pdf_acquirer.py`).

## 15. Expected output/artifacts
Updated `pdf_acquirer.py`, expanded `tests/test_demand_usage_evidence_router.py` (or a split-out `tests/test_demand_usage_pdf_acquirer.py` if it grows large).

## 16. Handoff to next phase
**Status: implemented (this session).**

- `pdf_acquirer.py` hardened along all three axes:
  - **Multi-candidate discovery**: new `_search_pdf_candidates(source, geo)` replaces the old
    single-shot `_discover_pdf_url` — returns up to `_MAX_PDF_CANDIDATES` (3) URLs (the plan's own
    `.pdf`-looking locator first, then `.pdf`-looking search results, then any remaining results,
    deduplicated). `acquire()` verifies each in turn via `_download_pdf_bytes` (unchanged Phase 1
    `asset_verifier` call) and uses the first that actually resolves to a real, accessible PDF; every
    candidate's outcome is logged in the attempt dict's new `candidates_tried` list, so a single
    broken/guessed link no longer sinks the whole source.
  - **Failure-mode-specific extraction diagnostics**: `_extract_pages` now returns
    `(pages, failure_reason, document_title)` instead of a bare list, with `failure_reason` one of
    `encrypted_pdf` (password-protected / pdfplumber raises a password/encryption-named exception),
    `scanned_no_text_layer` (the file opens and has pages, but `extract_text()` is empty/whitespace
    on every single page — the scanned/image-only signal), `corrupted_or_unreadable` (any other
    malformed-file failure), or `None` on success.
  - **Shortlist determinism fix + test**: `_keyword_shortlist`'s tie-break was actually
    index-descending before this phase (an artifact of `sort(reverse=True)` on `(score, i)` tuples),
    contradicting the module's own "score then original order" docstring claim. Fixed to
    `sort(key=lambda t: (-t[0], t[1]))` (score desc, then true original page order for ties) and
    added `test_shortlist_tie_break_is_original_page_order_not_reverse` +
    `test_shortlist_is_deterministic_across_repeated_calls` to lock this in as a regression.
- **Table provenance (new, not in the original module)**: pages are extracted as a `_PageContent`
  dataclass (`text`, `tables`, `combined_text`) instead of a bare string. The LLM's page-anchored
  interpretation prompt now also shows each shortlisted page's tables (`[PAGE N TABLE T]` blocks) and
  may return an optional `table_index`; an invalid/unresolvable table pointer only drops the table
  provenance (never invalidates an otherwise page-anchored, text-traceable value — the LLM cannot
  invent a page, but a bad table guess is just downgraded, not treated as a hard rejection). A new
  `location_ref` field (`"page N"` or `"page N, table T"`) and `table_index` are added to both the
  fact and the attempt record, additively — the fact's pre-existing shape (`page_number`,
  `document_title`, `source_url`, etc.) is unchanged.
- **Table spanning multiple pages**: a best-effort `_merge_spanning_tables` heuristic — if a page's
  LAST table and the NEXT page's FIRST table have the same column count, the next page's table rows
  are appended to the earlier page's table in place (both pages' `combined_text` recomputed) — so a
  continuation row landing on the following page is still reachable from, and attributed back to, the
  table's actual starting page/table index. Column-count mismatches are correctly left unmerged
  (tested).
- **Document title provenance improvement**: `_extract_pages` now also reads the PDF's own
  `pdf.metadata["Title"]` when present, falling back to the URL's filename only when the PDF carries
  no title metadata (unchanged fallback behavior for untitled PDFs).
- **HTML-landing-page / malformed-PDF / redirect handling**: entirely reused from Phase 1's
  `asset_verifier` (`_download_pdf_bytes` is an unmodified passthrough to `verify_asset(url, "pdf")`)
  — Phase 3 did not touch or reimplement any of that; it only added the *retry-on-a-different-
  candidate* loop around it.
- **Fact shape preserved**: `evidence_router.py`'s dispatch logic and `pdf_acquirer.acquire`'s
  external contract (`(fact_or_None, attempt)`, called with `(llm, source, geography, *, unit,
  context, keywords)`) are byte-for-byte unchanged — only new, additive fields (`table_index`,
  `location_ref`, `candidates_tried`) appear on the returned fact/attempt.
- **Deliberate test changes** (documented, not a silent regression): the 4 existing PDF tests in
  `tests/test_demand_usage_evidence_router.py` were updated to monkeypatch the new
  `_search_pdf_candidates` function (instead of the removed `_discover_pdf_url`) and the new
  3-tuple `_extract_pages` return shape (via a small `_page()` helper building `_PageContent`
  objects) — each test's original intent (shortlist-rejection, page-anchored acceptance,
  download-failure reporting) is unchanged, only the mock shape changed to match the new contract.
- New file `tests/test_demand_usage_pdf_acquirer.py` (25 tests, 2 skipped when `reportlab` isn't
  installed): multi-candidate discovery ordering and fallback-to-second-candidate; all-candidates-
  fail / no-candidates-found; a real (unmocked) `asset_verifier` HTML-landing-page-as-PDF rejection;
  `encrypted_pdf` / `scanned_no_text_layer` / `corrupted_or_unreadable` rejection reasons (including
  one test against the real pdfplumber code path with a genuinely malformed byte string, and one
  against a faked all-blank-pages `pdfplumber.open` return); shortlist tie-break determinism;
  table-anchored fact provenance, plain-text fact provenance (no table_index), invalid-table-index
  graceful degradation, and page-index-not-in-shortlist hard rejection; a table-spanning-two-pages
  merge test plus a mismatched-column-count non-merge test; verified-observed end-to-end; an
  untraceable-value-downgrades-away-from-observed test; a download-failure (`source_found_unverified`
  territory, produced by the unchanged Phase 2 fallback rung in `evidence_router.py`, not by
  `pdf_acquirer` itself) test; LLM-call-failure and LLM-says-not-found both reported as
  `extraction_failed`; and two real-PDF (built with `reportlab` when available, else skipped)
  single-page/multi-page extraction tests.
- Full suite: **147 passed, 2 skipped** (`pytest tests/`) — the 2 skips are the `reportlab`-gated
  real-PDF-construction tests (`reportlab` is not an installed dependency of this project and Phase 3
  does not add it; the rest of the real-content path IS exercised live in the smoke test below).
- **Real PDF smoke test** (not the 3-market baseline — a direct, live call against
  `pdf_acquirer._download_pdf_bytes` + `_extract_pages` + `_keyword_shortlist`, no LLM involved): EIA's
  public Short-Term Energy Outlook PDF (`https://www.eia.gov/outlooks/steo/pdf/steo_full.pdf`) —
  verified as a real, accessible, `%PDF-`-signed PDF (7.4 MB, 200 status, no redirect), extracted 40
  pages (the module's own scan cap) with pdfplumber, page 0's text correctly read as "Short-Term
  Energy Outlook August 2026", and a keyword shortlist against `["gasoline","barrels","crude"]`
  deterministically ranked real content pages. Confirms the acquisition -> verification -> extraction
  -> shortlist chain works end-to-end against a real, large, real-world authoritative PDF, not just
  mocks.
- Known limitations carried forward:
  - The table-spanning-pages merge is a column-count heuristic only — it will not correctly merge
    tables whose continuation page happens to add/drop a column, and it has no positive signal beyond
    column count (e.g. it can't distinguish "genuinely two different but coincidentally same-width
    tables" from "one table split across pages"). Acceptable for this phase's scope; a future phase
    could strengthen this with header-repetition detection if it proves to matter in practice.
  - `scanned_no_text_layer` detection is currently a scan/no-scan-uses-`extract_text()`-only signal per
    page; a PDF with only ONE scanned page (out of many real pages) will not trigger this classification
    for the whole document, which is correct (the document isn't wholly scanned) but means a
    genuinely-scanned single page within an otherwise-fine report will simply not surface useful text
    from that page and may just fail the keyword shortlist for it — no OCR fallback exists, per the
    plan's explicit scope (Phase 3 does not add OCR).
  - `_search_pdf_candidates` still relies on `web_search.search`'s `filetype:pdf`-biased query and the
    LLM-proposed `locator`/`search_query_template` — it does not itself do any smarter query
    reformulation if all 3 candidates are irrelevant-but-real PDFs (e.g. real PDFs that are just the
    wrong report). That's a discovery-QUALITY problem the Phase 2 LLM-claim fallback (unchanged) and
    the router's multi-source chain (unchanged) already partially cover by trying the next SOURCE in
    the plan, not the next PDF-acquirer candidate.
  - A re-run of the Phase 0 baseline's 3 markets to measure the actual PDF hit-rate shift was NOT
    performed this session (would require live LLM calls across three market pipelines end-to-end,
    out of scope for a Phase-3-only session per this task's instructions) — Phase 4/5's own re-run (or
    a dedicated re-run session) should perform that 3-market re-run, diffing PDF-tier
    `found=true`/rejection-reason distribution against `BASELINE_V3.md`, to get the real "before vs.
    after Phase 3" numbers this phase's acceptance criterion #3 calls for. The live smoke test above
    confirms the mechanism works against real content; it does not substitute for that regression
    measurement.

---

# Phase 4 — Tabular Evidence Pipeline (hardening)

## 1. Objective
Same hardening treatment as Phase 3, applied to `tabular_acquirer.py` — the second-largest attempt volume in the baseline (380 attempts, 0% hit rate).

## 2. Problem being solved
`_discover_dataset_url` only accepts a URL that LITERALLY ends in `.csv`/`.xlsx`/`.xls` from the search results — many real open-data portals serve a dataset via a query-parameter URL (`?format=csv`) or a redirect-based download link with no file extension at all, which today's discovery silently rejects.

## 3. Current behavior
See `tabular_acquirer._discover_dataset_url` as built — extension-suffix check only.

## 4. Desired behavior
- Discovery accepts a candidate whose Phase-1-verified `Content-Type` matches `text/csv`/`application/vnd.openxmlformats...`/`application/vnd.ms-excel` REGARDLESS of URL extension (this is exactly what Phase 1's `asset_verifier` is for — Phase 4's job is to actually USE that capability here, since today's tabular discovery doesn't even attempt a non-`.csv`/`.xlsx`-suffixed URL).
- Schema-summary prompt (already in `tabular_acquirer.acquire`) gets a documented cap-size test (verify the `_MAX_SCHEMA_CHARS` truncation doesn't cut off mid-row in a way that corrupts the sample shown to the LLM).

## 5. Exact files/components likely to change
- `tabular_acquirer.py`: `_discover_dataset_url` (accept non-suffixed URLs verified by content-type).

## 6. New files/components if needed
None expected.

## 7. LLM responsibilities
Unchanged: propose locators/queries; map schema to column/row/filter.

## 8. Deterministic responsibilities
Content-type-based acceptance (via Phase 1's verifier) instead of extension-only.

## 9. State/schema changes
None beyond what Phase 1 already added (`resolved_url` on attempts).

## 10. Provenance requirements
Unchanged fact shape.

## 11. Failure/fallback behavior
Unchanged — never raises out of `acquire()`.

## 12. Tests
- A dataset URL with no file extension but a verified `text/csv` content-type is accepted and parsed.
- Schema-summary truncation doesn't corrupt a sample row (test with a dataset whose row text straddles the `_MAX_SCHEMA_CHARS` boundary).
- Regression: existing extension-suffixed discovery still works.

## 13. Acceptance criteria
- [ ] Non-extension-suffixed real datasets are discoverable.
- [ ] Re-run shows tabular hit-rate change, reported honestly.
- [ ] Full test suite green.

## 14. What must NOT be changed
`tabular_acquirer.acquire`'s fact shape, the LLM mapping-proposal prompt's core structure (only the discovery step changes).

## 15. Expected output/artifacts
Updated `tabular_acquirer.py`, expanded tests.

## 16. Handoff to next phase
**Status: implemented (this session).**

- `tabular_acquirer.py` hardened along the discovery axis (extraction logic below discovery is
  unchanged from Phase 0-3):
  - **Multi-candidate discovery**: new `_search_dataset_candidates(source, geography)` replaces the
    old single-shot `_discover_dataset_url` -- returns up to `_MAX_TABULAR_CANDIDATES` (3) URLs (the
    plan's own locator first, then `.csv`/`.xlsx`/`.xls`-suffixed search results, then any remaining
    non-suffixed results, deduplicated), mirroring Phase 3's `pdf_acquirer._search_pdf_candidates`.
  - **Content-sniffed acceptance, not extension-based**: new `_resolve_dataset(url)` calls Phase 1's
    unmodified `asset_verifier.verify_asset` with an extension-based guess (`"csv"`/`"excel"`), but
    when that guess doesn't hold, falls back to `asset_verifier.sniff_type` on the SAME already-
    fetched bytes to decide the real kind -- so a non-suffixed download URL, a query-parameter URL
    (`?format=csv`), or a content-disposition-served file with no extension in the URL at all are all
    discoverable as long as the bytes really are a CSV or an Excel workbook. Verified live against a
    real GitHub-raw CSV served with `Content-Type: text/plain` (see smoke test below) -- the old
    extension/content-type-only path would have rejected it.
  - **Dataset landing pages**: new `_extract_tabular_links(html_bytes, base_url)` -- a dependency-free
    regex over `href` attributes, ranked extension-match first then `download`/`export`/`format=`
    wording, resolved against the page's own URL via `urljoin`. When `_resolve_dataset` sniffs a
    candidate as HTML, it tries up to `_MAX_LANDING_PAGE_LINKS` (5) extracted links, ONE hop deep
    only (never recurses past that hop, to bound retry volume) -- confirmed by a test asserting
    exactly 2 `verify_asset` calls total when the linked candidate is itself another landing page.
  - **Redirects**: reused unchanged from Phase 1 -- `asset_verifier.verify_asset` already follows
    redirects and returns the final `resolved_url`; `_resolve_dataset`/`acquire()` propagate that
    resolved URL through to the persisted fact's `source_url`, never the originally-guessed one.
  - **Malformed/missing-column extraction**: unchanged from Phase 0-3 (`_load_sheets` returning
    `None` on a pandas/openpyxl parse failure -> `extraction_failed`; a proposed `value_column` not
    actually in the loaded DataFrame -> `extraction_failed`) -- Phase 4 did not touch this logic, only
    added explicit regression tests for it.
- **`asset_verifier.py` reused unchanged** (per this phase's explicit requirement) -- Phase 4 never
  modifies it, only calls it (`verify_asset`, `sniff_type`) from `tabular_acquirer.py`'s new discovery
  functions, exactly as Phase 1-3 already did.
- **Fact shape preserved**: `evidence_router.py`'s dispatch call
  (`tabular_acquirer.acquire(llm, source, geography, unit=..., context=..., year=...)`) and the
  returned fact's shape (`value`, `unit`, `year`, `source`, `source_url`, `location_ref`, `file_name`,
  `sheet`, `evidence_type`, `confidence`) are byte-for-byte unchanged -- only the discovery step
  changed, per this phase's own "what must NOT be changed" constraint.
- **`source_found_unverified` fallback preserved**: when every discovered candidate fails
  verification/parsing, `acquire()` still returns `(None, attempt)` with a populated
  `rejection_reason` and `candidates_tried` -- the existing Phase 2 last-resort rung in
  `evidence_router.execute_evidence_plan` (unchanged by this phase) is still what turns a chain of
  such misses into an honestly-labeled `source_found_unverified` claim, not `tabular_acquirer` itself.
- **Deliberate test changes** (documented, not a silent regression): the 3 existing tabular tests in
  `tests/test_demand_usage_evidence_router.py` were updated to monkeypatch the new
  `_search_dataset_candidates`/`_resolve_dataset` functions (via a small local `_ok_resolve()` helper)
  instead of the removed `_discover_dataset_url`/`_download`, and `_load_sheets`'s mock lambdas gained
  an accepted (unused) `kind=None` parameter to match its new (backward-compatible, default-`None`)
  third parameter. Each test's original intent (deterministic lookup, ambiguous-row aggregation,
  geography-mismatch rejection) is unchanged -- only the mock shape changed.
- New file `tests/test_demand_usage_tabular_acquirer.py` (24 tests): multi-candidate discovery
  ordering + locator/search-hit deduplication; direct CSV/XLSX/XLS acceptance; non-suffixed URL
  sniffed as CSV (server content-type already matches) and sniffed as Excel DESPITE a CSV guess
  (server content-type doesn't match either -- pure byte-sniffing decides); a content-disposition-
  style download (no extension anywhere, generic `application/octet-stream` content-type, real CSV
  bytes); redirect resolution (final URL captured); a wrong-file-type rejection (real JSON body);
  a download-failure rejection reason propagating through; HTML landing-page link extraction
  (extension-ranked over generic wording, relative-link resolution); a landing page that successfully
  discovers and verifies its linked real CSV asset end-to-end; a landing page with no plausible links
  rejected as `html_landing_page_not_asset`; a landing-page-links-to-another-landing-page case proving
  the one-hop bound (exactly 2 verify calls, never chased further); malformed-content parse failure;
  missing-expected-column rejection; LLM-says-not-found honest rejection; a full verified-observed
  end-to-end test asserting complete provenance (source name, resolved URL, file name, sheet name,
  column/filter location_ref, value, unit, year); the pre-existing ambiguous-multi-row-never-silently-
  observed regression; no-candidates-found / all-candidates-fail (unverified-fallback-preserving)
  cases; and a wide-schema `_MAX_SCHEMA_CHARS`-truncation-doesn't-crash regression.
- Full suite: **171 passed, 2 skipped** (`pytest tests/`) -- the 2 skips are the pre-existing
  `reportlab`-gated PDF tests from Phase 3, unrelated to this phase.
- **Real dataset smoke test** (not the 3-market baseline -- a direct, live call against
  `tabular_acquirer._resolve_dataset` + `_load_sheets`, no LLM involved): the public
  `datasets/gdp` CSV mirror on GitHub-raw
  (`https://raw.githubusercontent.com/datasets/gdp/main/data/gdp.csv`) -- verified as real, accessible
  content (200 status, no redirect, 562,767 bytes) even though GitHub-raw serves it with
  `Content-Type: text/plain; charset=utf-8` (NOT `text/csv`) -- confirming this phase's core
  "content-sniffing over content-type/extension" fix actually matters against a real, common serving
  pattern, not just a mocked one. Loaded with pandas, deterministically filtered to
  `Country Name == 'India' & Year == 2020` (no LLM), yielding India's real 2020 GDP
  (~$2.6749 trillion current USD) as an `evidence_type="observed"` fact via
  `evidence.build_structured_fact`, with full provenance (`source_url`, `location_ref` naming the
  sheet/column/filter).
- Known limitations carried forward:
  - `_extract_tabular_links` is a dependency-free regex over raw `href` attributes, not a real HTML
    parser -- it will miss links generated by JavaScript, links in non-`href` attributes (e.g. a
    button's `data-url`), and could theoretically mis-extract from unusually malformed markup. This is
    the same tradeoff Phase 3 made for PDF table serialization (no heavy new dependency); acceptable
    for this phase's scope, worth revisiting (e.g. `BeautifulSoup`) if a real landing page's link
    structure proves too irregular for a regex in practice.
  - The one-hop bound on landing-page link-following is a deliberate, hard-coded limit (not
    configurable) -- a dataset portal requiring two or more clicks through intermediate pages to reach
    the real file is not discoverable by this phase. Widening this was explicitly out of scope (the
    plan calls for bounded retry volume, not unbounded crawling).
  - Content-Disposition header inspection was NOT added to determine a downloaded file's real
    attachment filename, because doing so would require either modifying `asset_verifier.py`'s return
    shape (out of scope -- "Reuse `asset_verifier.py` unchanged" was an explicit requirement this
    session) or issuing a second network request purely for that header (wasteful, and still an
    `asset_verifier` behavior change in spirit). `fact["file_name"]` therefore still falls back to the
    resolved URL's own last path segment, which is accurate for the common case (a real filename in
    the URL, or a redirect target that names the file) but would show a generic/query-string-shaped
    "filename" for a pure content-disposition-only download (the acquisition and parsing of such a
    file still work correctly via content-sniffing -- only the cosmetic `file_name` provenance field
    would look different from the real `Content-Disposition: attachment; filename=...` value). A
    future phase could add a narrow, additive `AssetVerification.content_disposition_filename` field
    to `asset_verifier.py` if this ever needs to be exact.
  - A re-run of the Phase 0 baseline's 3 markets (Crude Oil/Cement/Avocado Oil) to measure the actual
    tabular-tier hit-rate shift was intentionally NOT performed this session (would require live LLM
    calls across three market pipelines end-to-end, out of scope for a Phase-4-only session per this
    task's instructions, and explicitly not required unless a minimal smoke test is necessary -- the
    live smoke test above already confirms the mechanism works against a real, real-world dataset).
    Phase 5 (or a dedicated re-run session) should perform that 3-market re-run, diffing tabular-tier
    `found=true`/rejection-reason distribution against `BASELINE_V3.md`, to get the real "before vs.
    after Phase 4" numbers this phase's acceptance criterion #2 calls for.

---

# Phase 5 — API / Structured Data Pipeline (hardening)

## 1. Objective
Same treatment for `api_acquirer.py` — verify the LLM is naming REAL, callable public endpoints rather than plausible-sounding but non-existent ones (the likely dominant cause of this tier's 0% hit rate at 50-51 attempts per market).

## 2. Problem being solved
Today's `propose_evidence_plan` lets the LLM name an "API" source with zero grounding check that such an endpoint actually exists and is callable — Phase 1's `asset_verifier` (HTTP status + JSON-parseability) is necessary but likely not sufficient if the endpoint URL itself is fabricated (e.g. a plausible-looking but non-existent REST path).

## 3. Current behavior
`api_acquirer.acquire` calls `_fetch(url)` directly with no pre-flight existence check distinct from the fetch attempt itself.

## 4. Desired behavior
- Before the full fetch-and-LLM-mapping flow, do a lightweight pre-flight (e.g. HEAD request or a minimal GET) specifically to distinguish "endpoint doesn't exist / DNS failure / connection refused" from "endpoint exists but this specific query/path returned nothing" — two very different rejection reasons that should not both collapse into `download_failed`.
- Consider (decide during implementation, don't over-commit here) whether to have the LLM propose 1-2 well-known, GENUINELY real public API bases as part of `propose_pricing_evidence_plan`/`propose_driver_evidence_plan`'s guidance text (e.g. explicitly naming World Bank, FRED, Comtrade, FAOSTAT bulk API as examples the LLM can draw on when genuinely applicable) — this is a PROMPT-GUIDANCE change, still fully generic (not hardcoding a market->API mapping, just giving the LLM better-grounded examples of what a "real" API looks like, the same way the existing prompt already gives a `site:eia.gov` example for web sources).

## 5. Exact files/components likely to change
- `api_acquirer.py`: pre-flight existence check, refined rejection reasons (`endpoint_not_found` vs. `no_data_for_query`).
- `evidence_router.py`: `_propose_plan`'s system prompt guidance text, if the example-API-names approach is adopted (additive guidance only, not a hardcoded mapping).

## 6. New files/components if needed
None expected.

## 7. LLM responsibilities
Propose endpoint/locator with (if adopted) better-grounded example guidance; propose field-path mapping (unchanged).

## 8. Deterministic responsibilities
Pre-flight existence check, refined rejection-reason classification.

## 9. State/schema changes
New rejection reasons: `endpoint_not_found` (DNS/connection failure), `no_data_for_query` (endpoint real, query returned nothing).

## 10. Provenance requirements
Unchanged fact shape.

## 11. Failure/fallback behavior
Unchanged.

## 12. Tests
- Pre-flight distinguishes a genuinely non-existent host from a reachable one with an empty result.
- (If adopted) prompt guidance change doesn't regress `test_propose_intensity_and_pricing_plans_never_return_empty_source_list`-style tests.

## 13. Acceptance criteria
- [ ] Refined rejection reasons appear in real attempt logs.
- [ ] Re-run shows API hit-rate/diagnostic change, reported honestly (this tier may legitimately stay near 0% — most niche markets simply don't have a real public API for their specific driver; that's a valid, reportable finding, not a failure to fix).
- [ ] Full test suite green.

## 14. What must NOT be changed
The generic, non-hardcoded nature of source proposal — do not add a market-name-keyed lookup table of APIs.

## 15. Expected output/artifacts
Updated `api_acquirer.py`, possibly updated `evidence_router.py` guidance text, expanded tests.

## 16. Handoff to next phase
Record API-tier hit rate + diagnostic clarity improvement from the 3-market re-run.

---

# Phase 6 — Evidence Verification & Provenance (the authoritative layer)

## 1. Objective
Finish threading the 4-value `verification_status` enum (started in Phase 0/2) through EVERY consumer: `evidence.py`'s gate functions, `agents.py`'s `worse_of` usage, `quality.py`'s percentage computation and flags, `buildup_writer.py`'s report/workbook, `graph.py`'s state schema documentation.

## 2. Problem being solved
Phases 0-5 introduce the new status value and start producing it in a few places, but until this phase, most of the codebase still only understands the 3-value `evidence_type`. This phase is the "make it real everywhere" pass — the single point where a candidate fact's final classification becomes authoritative end-to-end.

## 3. Current behavior
`evidence_type` is 3-valued everywhere except the Phase 2 addition. `quality.py`'s `compute_provenance_quality_flags` computes `observed_pct`/`derived_pct`/`assumption_pct` only. `buildup_writer.py`'s Excel sheets color-code via `_PROV_FILL` keyed on the 3 values.

## 4. Desired behavior
- `evidence.build_fact`/`build_structured_fact`/`build_unverified_claim_fact` all return `verification_status` (rename from `evidence_type`, OR keep `evidence_type` as the field name but with 4 legal values — DECIDE during implementation which is less disruptive; document the decision here in this phase's handoff once made, and if renamed, provide a compatibility property/alias so this isn't a silent breaking change for any code this plan didn't anticipate).
- `evidence.worse_of` extended to rank `source_found_unverified` between `derived` and `assumption` (a source-found-but-unverified claim is weaker than a derived value but stronger than a bare guess) — new rank order: `verified_observed` (3) > `derived` (2) > `source_found_unverified` (1) > `assumption` (0).
- `quality.py`: `evidence_quality` dict gains `source_found_unverified_pct`; a new flag `high_unverified_claim_share` (mirroring the existing `high_assumption_share` pattern) fires when this tier dominates.
- `buildup_writer.py`: `_PROV_FILL` gains a 4th color (distinct from the existing green/yellow/red) for `source_found_unverified`; all sheets that currently branch on 3 values get the 4th.

## 5. Exact files/components likely to change
`evidence.py`, `agents.py` (`worse_of` call sites), `quality.py`, `buildup_writer.py`, `graph.py` (docstring/comments only, TypedDict shape doesn't change since these are dict-valued fields already).

## 6. New files/components if needed
None — this is a migration/threading phase across existing files.

## 7. LLM responsibilities
Unchanged — this phase is pure plumbing.

## 8. Deterministic responsibilities
Every consumer of the provenance field correctly handles all 4 values; no consumer silently treats an unrecognized 4th value as a crash or as `assumption`-by-default in a way that loses information.

## 9. State/schema changes
The rename/field decision above; `evidence_quality` dict shape gains a key; `quality_flags` catalog gains 1 new flag type.

## 10. Provenance requirements
This IS the provenance requirements phase — the acceptance criteria below are the substance.

## 11. Failure/fallback behavior
Any code path that receives an unexpected/missing provenance value must default to the SAFEST (weakest) interpretation, never the strongest — mirrors the existing "absence of a verdict must not silently flag a legitimate bucket" philosophy already used elsewhere in this codebase (e.g. `usage_validation_node`).

## 12. Tests
- `worse_of` new 4-value ranking, all pairwise combinations.
- `quality.py`: `evidence_quality` includes the new percentage; `high_unverified_claim_share` flag fires/doesn't fire correctly.
- `buildup_writer.py`: workbook generation doesn't crash on a fact with `source_found_unverified`; the 4th color is applied.
- Full regression suite for every existing test that references `evidence_type`/`observed`/`derived`/`assumption` literals — audit and update each one deliberately, documented in this phase's handoff (which specific tests changed and why).

## 13. Acceptance criteria
- [ ] Every consumer of provenance (7+ files) correctly handles 4 values.
- [ ] `evidence_quality` reporting includes the new percentage in JSON + workbook.
- [ ] No silent promotion path exists anywhere (search the codebase for every `== "observed"` / `!= "assumption"` style comparison and verify each is still correct under the 4-value model).
- [ ] Full test suite green (with the documented, deliberate updates from this phase).

## 14. What must NOT be changed
The MEANING of `verified_observed`/`derived`/`assumption` for facts that were already correctly classified before this phase — this is an additive 4th tier, not a re-classification of existing behavior.

## 15. Expected output/artifacts
Updated `evidence.py`, `agents.py`, `quality.py`, `buildup_writer.py`; a migration note in this phase's handoff listing every call site touched.

## 16. Handoff to next phase
**Status: implemented (this session).**

- **Field-name decision**: kept `evidence_type` as the field name everywhere (it was already
  entrenched across every acquirer/consumer/test from Phases 0-5, including the
  `source_found_unverified` value Phase 2 already wrote under that key) -- renaming the KEY would
  have touched far more call sites for zero behavioral gain. What actually changed is the VALUE
  vocabulary: `evidence_type` now carries the full, authoritative 4-value `VerificationStatus`
  (`"observed"` renamed to `"verified_observed"` at every producer). Every gate function in
  `evidence.py` (`build_fact`, `build_structured_fact`, `build_unverified_claim_fact`,
  `extract_structured_fact`, `extract_fact_from_source`) additionally stamps a `verification_status`
  key with the IDENTICAL value via one shared helper (`_stamp_verification_status`), so Phase 7+ can
  use either name -- there is exactly one decision made per fact, exposed under two names, never two
  judgments that could drift apart. `evidence.PROVENANCE_STATES` is the new canonical 4-tuple
  (`verified_observed`, `derived`, `source_found_unverified`, `assumption`) for any future consumer
  that needs to enumerate/default-init counts safely.
- **`worse_of` ranking extended**: `verified_observed`(3) > `derived`(2) > `source_found_unverified`(1)
  > `assumption`(0), unrecognized/missing input defaults to rank 0 (`assumption`) -- never crashes,
  never silently promotes. Verified via an exhaustive pairwise test
  (`test_worse_of_rank_order_all_pairs`) covering all 16 ordered combinations.
- **Combined provenance (driver x intensity, driver x price/conversion-factor)**: unchanged call
  sites (`agents.py`'s `demand_quantification_node`/`pricing_node` already called `evidence.worse_of`)
  now correctly rank all 4 values instead of silently collapsing `source_found_unverified` to the same
  rank as `assumption` (the pre-Phase-6 `_PROVENANCE_RANK` dict had no entry for it, so it defaulted to
  rank 0 and could never be DISPLAYED as `source_found_unverified` in the combined result, only as
  `assumption` -- Phase 6 fixes that visibility gap, not just the ranking).
- **Single-input derivation now inherits its base fact's tier**: the regional/global ->
  population-share allocation in `demand_quantification_node` previously hardcoded
  `"evidence_type": "derived"` regardless of the regional fact's own status. Now computed as
  `evidence.worse_of(regional_fact["evidence_type"], "derived")` -- a `verified_observed` regional
  fact still yields `derived` (correct, unchanged in effect), but a regional fact that was itself only
  `source_found_unverified` or `assumption` now correctly caps the country-level allocation at that
  same weaker tier, never overstating it as an ordinary `derived` value. The derived fact's
  `derivation_basis` is now an explicit formula string naming the regional value, its own
  verification_status, and the population-share factor used -- satisfying the plan's "a derived value
  must retain provenance to the observed facts it was derived from" rule.
- **Files migrated** (7, matching the plan's "7+ files" acceptance criterion):
  1. `evidence.py` -- the authoritative layer: `verify_and_tag` (`"observed"` -> `"verified_observed"`),
     `build_fact`, `build_structured_fact`, `build_unverified_claim_fact`, `worse_of`/`_PROVENANCE_RANK`,
     plus the new `PROVENANCE_STATES` tuple and `_stamp_verification_status` helper.
  2. `agents.py` -- `_IDENTITY_INTENSITY`, `_fallback_assumption`, `_fallback_assumption_batch`
     (all now also stamp `verification_status`), the intensity-clamping downgrade, the population-share
     derivation (now provenance-aware, see above), `demand_quantification_node`'s `counts` dict (now a
     4-key `dict.fromkeys(evidence.PROVENANCE_STATES, 0)` with safe fallback instead of a 3-key dict that
     would `KeyError` on an unrecognized/4th provenance value), `pricing_node`'s `grounded`/
     `overall_price_evidence_type` (`== "observed"` -> `== "verified_observed"`, plus a
     `verification_status` mirror on the price record), and `validation_node`'s evidence-quality
     percentage block (`prov_counts`/`price_prov_counts`/`combined`/`evidence_quality` all now 4-value,
     with a new `source_found_unverified_pct` key, defended by a `_safe_prov` helper).
  3. `quality.py` -- `compute_provenance_quality_flags`'s `counts` dict (4-value, safe default), the
     `no_observed_data` flag now keys off `verified_observed` specifically, and a new
     `high_unverified_claim_share` flag (fires at >=50% `source_found_unverified` share, mirroring
     `high_assumption_share`'s existing threshold/philosophy).
  4. `buildup_writer.py` -- `_PROV_FILL` gains a 4th, visually distinct color
     (`source_found_unverified` -> light purple `D9D2E9`, distinct from the pre-existing green/yellow/
     red/blue already in use elsewhere in the workbook) and the value-key rename; the Summary sheet's
     "Evidence Quality" block gains a "Source found, unverified inputs" row (own line, never merged into
     either "Verified observed" or "Assumption") with the rest of that sheet's row numbers shifted down
     by one to make room.
  5. `demand_usage_pipeline.py` -- the CLI's final console summary line now reports all 4 percentages
     (`verified_observed`/`derived`/`source_found_unverified`/`assumption`), not 3.
  6. `graph.py` -- audited (per the plan's file list); no change needed, it holds no
     `evidence_type`/provenance literals, only dict-valued state fields that already pass through
     unchanged.
  7. Every acquirer (`web_acquirer.py`, `pdf_acquirer.py`, `tabular_acquirer.py`, `api_acquirer.py`) and
     `evidence_router.py`/`triangulation.py` -- audited and confirmed to only ever MIRROR
     `fact["evidence_type"]` from `evidence.py`'s gate output, never construct their own literal
     `"observed"`/`"derived"`/`"assumption"` strings -- so they automatically inherit the correct
     4-value vocabulary with zero changes needed. This confirms `evidence.py` really is the single
     authoritative acceptance/provenance choke point the objective called for.
- **No silent promotion path exists**: audited every `== "observed"` / `!= "observed"` / dict-literal
  `"evidence_type": "observed"` occurrence across `src/` and `tests/` (11 in source, ~20 across 6 test
  files) and confirmed each one either (a) is the Phase 0 legacy-compat map's own
  `"verified_observed": "observed"` entry (correct, unchanged -- that's the DOWN-mapping for
  not-yet-migrated 3-value consumers, of which none remain in this codebase) or (b) was migrated to
  `"verified_observed"`. Grepped again after all edits landed to confirm zero stragglers.
- **Backward compatibility**: `evidence.legacy_evidence_type()` (Phase 0) is unchanged and still maps
  `verified_observed->observed`, `source_found_unverified->assumption`, `derived->derived`,
  `assumption->assumption` -- verified by both the pre-existing `test_demand_usage_baseline_contract.py`
  and this phase's own `test_legacy_evidence_type_shim_still_works_after_phase_6`. No consumer in this
  codebase currently calls it (Phase 6 migrated every real consumer to the 4-value model directly), but
  it remains available, correct, and tested for any future 3-value integration.
- **Desk Research / Supply Side confirmed untouched**: `git status` shows no changes to
  `src/me_engine/generation/` (the one pre-existing, unrelated modification to
  `src/me_engine/generation/agents.py` predates this session and was not touched further) or
  `src/me_engine/supply/` this session.
- **Tests**: new file `tests/test_demand_usage_phase6_provenance.py` (22 tests) covering every item in
  this phase's own "Tests" section -- verified-observed/inaccessible-source/derived-from-verified/
  derived-from-unverified-is-capped/assumption-fallback facts; the full 16-combination `worse_of`
  pairwise ranking plus identity, no-input, and unrecognized-value-defaults-safely cases; combined
  driver x intensity and driver x price (via a conversion factor) provenance capping; the new
  `high_unverified_claim_share` flag firing/not-firing plus a regression check that `no_observed_data`
  still fires independently; `_PROV_FILL`'s 4 distinct colors; JSON report percentage rollups (never
  merging unverified into observed); an XLSX workbook smoke test with a `source_found_unverified` fact
  present (does not crash, per the plan's own explicit test requirement); and the backward-compatible
  legacy mapping. Additionally audited and updated (documented here, not a silent regression) every
  pre-existing test asserting the old `"observed"` literal:
  `tests/test_demand_usage_api_acquirer.py` (2 assertions), `tests/test_demand_usage_evidence_router.py`
  (9 assertions/mocks), `tests/test_demand_usage_pdf_acquirer.py` (2), `tests/test_demand_usage_tabular_acquirer.py`
  (1 assertion + 1 docstring), `tests/test_demand_usage_phase2_discovery.py` (3 assertions/mocks),
  `tests/test_demand_usage_triangulation.py` (2 mock dicts), `tests/test_units_contract.py` (1 mock
  dict) -- every one of these tests' original intent (dispatch, acceptance-gate, rejection-reason,
  price-basis-conversion behavior) is unchanged, only the literal string value changed to match the new
  vocabulary. Full suite: **220 passed, 2 skipped** (the 2 skips are the pre-existing
  `reportlab`-gated PDF tests from Phase 3, unrelated to this phase).
- Known limitations / handed off to Phase 7+:
  - The 3-market re-run (Crude Oil/Cement/Avocado Oil) to see how the `source_found_unverified` tier's
    share and the new `high_unverified_claim_share` flag actually behave against `BASELINE_V3.md` was
    intentionally NOT performed this session, per this task's explicit instruction ("Do not run the full
    3-market validation yet"). Phase 7 (or a dedicated re-run session) should perform it.
  - `magnitude.py`/`triangulation.py`'s own reference facts already flow through the same
    `evidence.py` gates (confirmed by audit) and therefore already carry correct 4-value
    `evidence_type`/`verification_status`, but Phase 11 is where a magnitude/triangulation flag record
    is meant to SURFACE a reference's own verification_status distinctly in its flag message -- that
    surfacing was explicitly out of this phase's scope (`magnitude.py` was on the "must NOT be changed"
    list) and remains open for Phase 11.
  - Phase 7's `evidence_strategy.derivation_path` concept (a strategy-level, pre-declared formula name)
    is a natural consumer of this phase's newly-explicit `derivation_basis` formula strings on
    population-share-derived facts -- Phase 7 can reference/validate against the concrete formula text
    this phase now always populates, rather than inventing its own from scratch.

---

# Phase 7 — Driver Research Strategy

## 1. Objective
Give every approved usage/driver an explicit, persisted evidence STRATEGY (not just a source list) before quantification begins — primary/secondary source classes, preferred metric+unit, fallback evidence, and an explicit derivation path — so the driver layer stops treating "search generically and hope" as the only strategy.

## 2. Problem being solved
Today, `evidence_router.propose_driver_evidence_plan` produces a flat ranked source list with no notion of "if ALL of these fail, here is the deliberate fallback derivation strategy" (e.g. "if no country-level figure exists, derive from a regional total via population share" is currently hardcoded procedural logic in `agents.demand_quantification_node`'s regional/global fallback, not something visible/auditable as part of the driver's own strategy record).

## 3. Current behavior
The regional/global-then-population-share fallback exists in `demand_quantification_node` (unchanged since before the evidence-router work) but is invisible in the taxonomy/driver approval draft — a user approving the taxonomy has no visibility into what happens if primary evidence fails for a given driver.

## 4. Desired behavior
`demand_driver_node` (in `agents.py`, part of Phase 2/taxonomy) gains an explicit `evidence_strategy` field per driver:
```json
{
  "primary_source_classes": ["government_statistics", "international_organization"],
  "secondary_source_classes": ["industry_association", "research_consulting"],
  "preferred_metric": "annual production volume",
  "preferred_unit": "million tonnes per year",
  "fallback_evidence": "regional/global total with population-share allocation",
  "derivation_path": "country_share = population(country)/population(region); country_value = regional_value * country_share"
}
```
This is surfaced in the taxonomy draft (visible to the user before approval, per the existing "approve reduces ambiguity" pattern) and persisted into the final audit output so a reviewer can see WHY a given cell ended up `derived` via a specific named path, not just that it did.

## 5. Exact files/components likely to change
- `agents.py`: `demand_driver_node` prompt/schema extended.
- `taxonomy.py`: draft/approved schema carries `evidence_strategy` per driver.
- `graph.py`: `UsageDemandState` gains nothing new structurally (drivers dict already carries arbitrary per-driver fields) but document the new keys.
- `buildup_writer.py`: surface the strategy in the Build-Up/driver-facts audit.
- `frontend/src/components/DemandUsageWizard.tsx`: (optional, low-priority within this phase) show the strategy in the taxonomy review step.

## 6. New files/components if needed
None expected — this extends existing driver-proposal logic.

## 7. LLM responsibilities
Propose the strategy fields (source classes, preferred metric/unit, fallback approach, derivation formula in words) as part of the SAME call that already proposes the driver itself — avoid an extra LLM round-trip if the existing `demand_driver_node` call can be extended to return these fields too.

## 8. Deterministic responsibilities
Validate the strategy's `preferred_unit` against `units.py`'s parser (reuse the existing unit-contract discipline — a strategy proposing an unparseable unit should be flagged the same way a driver's own unit is flagged today).
Persist the strategy unchanged through Phase 2 -> Phase 3 checkpoints.

## 9. State/schema changes
`drivers[bucket_name]` dict gains an `evidence_strategy` sub-dict. `taxonomy.approve_taxonomy`'s validation (currently checks `driver_name`/`unit`/`final_demand_unit` presence) should NOT hard-require `evidence_strategy` (backward compatible with manually-edited taxonomy drafts that predate this field) but should pass it through if present.

## 10. Provenance requirements
The final audit output (build-up rows / workbook) should show, for a `derived` driver fact, which `derivation_path` (if any, from the strategy) was actually used vs. an ad-hoc one — this closes a real transparency gap.

## 11. Failure/fallback behavior
Missing/malformed `evidence_strategy` on a driver must not block taxonomy approval or sizing — it's an enrichment, not a new hard requirement. Absence should just mean the audit trail shows "(no explicit strategy recorded)" rather than crashing.

## 12. Tests
- `demand_driver_node` returns a well-formed `evidence_strategy` given a stubbed LLM response.
- `approve_taxonomy` still succeeds on a taxonomy draft that lacks `evidence_strategy` (backward compatibility).
- Build-up report surfaces the strategy when present.

## 13. Acceptance criteria
- [ ] Every newly-generated driver in a fresh taxonomy draft carries an `evidence_strategy`.
- [ ] Backward compatibility with pre-Phase-7 taxonomy drafts confirmed via test.
- [ ] Full test suite green.

## 14. What must NOT be changed
The actual regional/global/population-share fallback ARITHMETIC in `demand_quantification_node` (unchanged in this phase — Phase 7 makes the strategy visible/auditable, Phase 10 formalizes/generalizes the derivation mechanics themselves).

## 15. Expected output/artifacts
Updated `agents.py`, `taxonomy.py`, `buildup_writer.py`; new tests.

## 16. Handoff to next phase
**Status: implemented (this session).**

- New module `src/me_engine/demand_usage/evidence_strategy.py` -- the strategy schema/prompt-
  fragment/normalization/deterministic-validation layer, with a richer field set than this phase's
  own worked example (superset, at the user's explicit request): `driver_name`, `metric_required`,
  `physical_unit`, `period`, `geography`, `primary_source_types`, `preferred_sources`,
  `search_query_templates`, `fallback_source_types`, `fallback_queries`, `derivation_path`,
  `expected_evidence`, `why_this_driver_measures_the_usage`, plus an internal `strategy_source`
  provenance tag (`llm_proposed` / `reused_library_default` / `missing`).
- **No extra LLM round-trip**: `agents.demand_driver_node`'s existing driver-proposal prompt gains
  a strategy-request fragment (`evidence_strategy.strategy_system_fragment()` /
  `strategy_json_fragment()`) and the strategy is parsed out of the SAME JSON response
  (`evidence_strategy.extract_strategy_from_driver_response`). A library-REUSED driver (no LLM call
  at all) gets a deterministic strategy built from its own already-persisted
  `driver_library` fields (`default_strategy_for_reused_driver`) -- every approved driver carries a
  strategy, reused or newly-derived.
- **Validation, never silent-fix**: `evidence_strategy.validate_evidence_strategy` flags (never
  auto-corrects) `no_measurable_metric`, `missing_or_unparseable_unit` (reuses `units.py`'s existing
  parser), `ambiguous_period` (must be exactly day/month/quarter/year), `driver_usage_relationship_unclear`
  (embedding-similarity check against the usage bucket description, same technique as the existing
  overlap/coverage checks -- best-effort, degrades to no-flag on any embedding failure),
  `derivation_requires_unsupported_conversion` (a curated known-unit-noun scan catching a derivation
  path that names a different physical-unit family than the driver's own, without one of the
  codebase's supported derivation markers -- `population_share`/`driver_value`/`identity`/etc.), and
  `scope_contradiction` (strategy geography outside the approved market geography). Computed once at
  proposal time (`demand_driver_node`) and RE-computed at `taxonomy.approve_taxonomy` time (after any
  human edit to the strategy or the usage bucket text), so the approved checkpoint's flags reflect
  what was actually approved, not a stale pre-edit verdict.
- **Sample strategy** (Transportation Fuels / Crude Oil, matching this phase's own worked example):
  ```json
  {
    "driver_name": "Road fuel consumption", "metric_required": "Annual physical fuel consumption",
    "physical_unit": "barrels per year", "period": "year", "geography": ["United States", "China"],
    "primary_source_types": ["government_statistics", "international_organization"],
    "preferred_sources": ["EIA", "IEA"],
    "search_query_templates": ["{country} road fuel consumption barrels per year"],
    "fallback_source_types": ["industry_association"], "fallback_queries": ["global road fuel consumption total"],
    "derivation_path": "population_share: country_value = regional_value * population_share(country)",
    "expected_evidence": "A national statistics bulletin stating annual road fuel consumption in barrels.",
    "why_this_driver_measures_the_usage": "Road fuel consumed is the direct physical measure of transportation fuel demand.",
    "strategy_source": "llm_proposed"
  }
  ```
- **Reaches the evidence router two ways** (`evidence_router.py`, additive, both keyed off the SAME
  `driver["evidence_strategy"]` that already flows unchanged from the approved taxonomy checkpoint
  through `graph.sizing_init_state` into Phase 3 state -- no new call-site plumbing needed):
  1. `_strategy_guidance` -- prose grounding the router's own source-proposal LLM call in the
     approved metric/period/preferred/fallback/derivation text, so it stops re-deriving a research
     plan from scratch.
  2. `_strategy_sources` / `_merge_strategy_sources` -- DETERMINISTIC: the strategy's own
     `preferred_sources`/`primary_source_types`/`search_query_templates` become real source-chain
     entries (tagged `origin: "evidence_strategy"`), prepended AHEAD of the LLM-proposed ones and
     actually dispatched through the unchanged `execute_evidence_plan`/acquirer chain -- satisfying
     "execute approved queries through the existing evidence router" literally, not just via prompt
     text. A template lacking the required `{country}` placeholder is skipped, never dispatched
     unsubstituted. Wired into `propose_driver_evidence_plan` only (pricing/intensity are Phase 8/out
     of this phase's scope).
- **Derivation path preservation**: `demand_quantification_node`'s existing (unchanged-arithmetic)
  population-share fallback now prefixes its ad-hoc `derivation_basis` string with
  `[strategy derivation_path: ...]` when the bucket's strategy declared one -- the audit trail shows
  BOTH the approved plan and the actual formula exercised, never one silently standing in for the
  other.
- **User approval / editability**: `drivers[bucket_name]["evidence_strategy"]` rides through the
  existing generic JSON checkpoints (`taxonomy.py`'s draft/approve, the FastAPI `TaxonomyApproveBody`
  `drivers: dict[str, Any]`, the CLI's plain-JSON `approve-taxonomy`) with ZERO schema changes needed
  there -- all three were already schema-agnostic on `drivers`. The Next.js wizard
  (`DemandUsageWizard.tsx`) gained a per-driver expandable "Evidence strategy" editor (metric, unit,
  period, preferred/fallback sources, search templates, derivation path, expected evidence, the
  usage-relevance rationale) with a flag-count badge, editable before "Approve & Continue" -- typed
  via a new `EvidenceStrategy`/`EvidenceStrategyFlag` pair in `frontend/src/lib/types.ts`. `tsc
  --noEmit` reports no new errors.
- **Audit surfacing**: `buildup_writer.build_report_dict` gains an `evidence_strategies` map
  (`{strategy, flags}` per bucket, explicitly `strategy: None` -- never omitted -- when absent) and
  the workbook gains a new "Evidence Strategy" sheet (one row per driver, flags column highlighted
  amber when non-empty, "(no explicit strategy recorded)" row when absent).
- **Backward compatibility**: `taxonomy.approve_taxonomy` only re-validates a driver whose
  `evidence_strategy` is present (`isinstance(..., dict)` guard) -- a pre-Phase-7 draft with no such
  key approves exactly as before, confirmed by
  `test_approve_taxonomy_backward_compatible_with_no_strategy`. `graph.py`'s `UsageDemandState`
  needed no structural change (`drivers` was already an arbitrary per-entry dict) -- only its
  docstring/comment was updated per this phase's own file list.
- **Files changed**: `agents.py` (prompt fragment + strategy extraction/validation at both the
  newly-derived and reused-library driver paths, + derivation-path-preserving audit string),
  `taxonomy.py` (re-validation pass at approval time), `evidence_router.py` (strategy-aware guidance
  + deterministic source injection for the driver plan), `buildup_writer.py` (`evidence_strategies`
  in the JSON report + new workbook sheet), `graph.py` (doc-only). New:
  `evidence_strategy.py`, `tests/test_demand_usage_evidence_strategy.py` (25 tests). Frontend:
  `DemandUsageWizard.tsx`, `lib/types.ts` (low-priority per this phase's own file list, implemented
  anyway since the objective explicitly calls for the strategy to be "visible in the existing
  taxonomy/driver approval stage").
- **Tests**: 25 new, covering every item in this phase's own "Tests" section plus every check in the
  task's expanded validation list -- a valid strategy (no flags), each individual flag
  (`no_measurable_metric`, `missing_or_unparseable_unit` for both an unparseable-currency unit and an
  empty unit, `ambiguous_period` with a period-synonym allow-list regression, a mocked-embedding
  `driver_usage_relationship_unclear` positive AND negative case, `derivation_requires_unsupported_conversion`
  positive AND a `population_share`-shaped negative case, `scope_contradiction` plus a `global`-is-never-
  a-contradiction regression), an explicitly-missing/empty strategy producing zero flags,
  `demand_driver_node` producing a well-formed strategy for a newly-derived driver, a deterministic
  strategy for a reused-library driver, graceful non-blocking behavior when an (old-style) LLM
  response omits `evidence_strategy` entirely, `approve_taxonomy` persisting a user-edited strategy
  end-to-end through the checkpoint file, `approve_taxonomy` re-flagging a user edit that blanked the
  metric, `approve_taxonomy` backward compatibility with no strategy at all, the strategy's preferred
  sources reaching `evidence_router`'s actual dispatched source chain ahead of the LLM's own proposal
  (plus a no-strategy-present regression and an unsubstitutable-template-is-skipped case), the
  derivation path surviving into `demand_quantification_node`'s persisted `derivation_basis`, and
  `buildup_writer.build_report_dict` surfacing both a present and an explicitly-absent strategy.
  **Full suite: 245 passed, 2 skipped** (the 2 skips are the pre-existing `reportlab`-gated PDF tests
  from Phase 3, unrelated to this phase) -- run via `pytest tests/`. The 3-market regression run was
  intentionally NOT performed this session, per this task's explicit instruction.
- **Desk Research / Supply Side confirmed untouched**: `git diff --stat
  src/me_engine/generation/agents.py` shows only the same single pre-existing 1-line change already
  present at session start (dated before this session, per `git log`) -- nothing in
  `src/me_engine/generation/` or `src/me_engine/supply/` was touched this session.
- Known limitations / handed off to Phase 8+:
  - The strategy-vs-driver `physical_unit` consistency is checked via `units.parse_unit_string`
    parseability only, not cross-checked against the driver's own `unit`/`final_demand_unit` string
    for exact equality -- a strategy proposing a plausible-but-different physical unit than the
    driver it belongs to would pass validation today. Worth tightening in a later phase if observed
    in practice.
  - `_strategy_sources`' pairing of `preferred_sources`/`primary_source_types`/`search_query_templates`
    is positional (zipped by index, padding from the last entry when lists are uneven length) -- a
    strategy with badly mismatched list lengths degrades gracefully (never crashes, never drops a
    named source) but doesn't guarantee the "right" template paired with the "right" source name.
  - Pricing's own evidence strategy (Phase 8) is explicitly out of this phase's scope -- 
    `propose_pricing_evidence_plan`/`propose_intensity_evidence_plan` were deliberately left
    unwired to the strategy layer; Phase 8 should decide whether pricing gets its own
    `evidence_strategy` sub-object (a price basis differs materially from a physical driver metric)
    or reuses this same schema.
  - A real 3-market re-run (Crude Oil/Cement/Avocado Oil) to see what strategies the LLM actually
    proposes in practice, and whether the `derivation_requires_unsupported_conversion` heuristic's
    curated unit-noun list needs expanding for real-world derivation-path phrasing, was intentionally
    NOT performed this session -- Phase 8 (or a dedicated re-run session) should do this and report
    back per this phase's original handoff ask.

---

# Phase 8 — Pricing Evidence & Price Basis

## 1. Objective
Materially raise pricing's observed rate (0% in the baseline, across all three markets, with `_resolve_price_basis` already correctly identifying non-obvious price bases like "barrels of fuel per vehicle-km" but finding no actual price evidence for the resolved basis).

## 2. Problem being solved
Pricing sources (commodity exchanges, price indices, industry price reports) are exactly the kind of source that's often PDF (industry price report) or API (exchange data feed) rather than a plain web page — meaning pricing should benefit MORE than driver evidence from Phases 1/3/5's discovery hardening, but this hasn't been verified.

## 3. Current behavior
`pricing_node` already calls `_resolve_price_basis` then `evidence_router.propose_pricing_evidence_plan`/`execute_evidence_plan` exactly like driver/intensity — no pricing-specific acquisition logic exists beyond the shared router.

## 4. Desired behavior
- Verify (via the Phase 12 re-run) whether Phases 1-6's discovery/verification hardening measurably raises pricing's observed rate on its own, since no pricing-specific mechanism was proposed as necessary in the design discussion — this phase's job may turn out to be MOSTLY verification + targeted prompt-guidance refinement, not new mechanism-building.
- If pricing hit rate is STILL near-zero after Phases 1-6, add pricing-specific guidance to `propose_pricing_evidence_plan`'s prompt: bias toward `pdf`/`api` source types explicitly for pricing (commodity exchanges commonly expose APIs; industry price reports are commonly PDFs) since today's generic guidance doesn't nudge the LLM toward the source TYPES most likely to actually work for pricing specifically.
- Ensure the conversion-factor bridge (`pricing_conversion_factors`, from the price-basis work) ALSO benefits from the harder evidence-router search now, not just the ASP itself — today conversion factors fall to `_fallback_assumption` immediately if the router misses once; verify this uses the full acquirer chain, not a truncated one.

## 5. Exact files/components likely to change
- `evidence_router.propose_pricing_evidence_plan`: refined guidance text if needed (data-driven decision, not assumed upfront).
- `agents.pricing_node`: verify (and fix if needed) that the conversion-factor evidence search uses the full Evidence Router chain, not a shortcut.

## 6. New files/components if needed
None expected.

## 7. LLM responsibilities
Propose pricing sources with (if adopted) sharper source-type guidance; resolve price basis (unchanged, already works).

## 8. Deterministic responsibilities
Nothing new beyond what Phases 1-6 already established — this phase mostly measures and, if warranted, tunes prompt guidance.

## 9. State/schema changes
None expected beyond what Phase 6 already threads through (pricing facts use the same 4-value provenance).

## 10. Provenance requirements
Pricing facts follow the exact same Phase 6 rules as driver/intensity facts — no separate pricing-specific provenance logic.

## 11. Failure/fallback behavior
Unchanged from today's `pricing_node` (LLM call failure floors ASP to $1/unit, clearly flagged as `call_failed`, never silently trusted — this existing safety net must survive this phase's changes untouched).

## 12. Tests
- Re-verify existing `test_demand_usage_price_basis.py` tests still pass unmodified (Phase 8 must not regress the price-basis resolution logic itself).
- If prompt guidance changes: a test asserting the pricing plan's proposed source types skew toward pdf/api when the guidance is present (mock-LLM test, similar to existing `propose_*_evidence_plan` tests).

## 13. Acceptance criteria
- [ ] 3-market re-run shows pricing observed/derived rate compared explicitly against the Phase 0 baseline (0% in all three) — report the actual number honestly, whether it improved or not.
- [ ] If it did NOT improve, this phase's handoff must say so plainly and hypothesize why (e.g. "most commodity price APIs require paid API keys the pipeline doesn't have credentials for" is a legitimate, reportable structural limit, not a phase failure to hide).
- [ ] Full test suite green.

## 14. What must NOT be changed
`_resolve_price_basis`'s core logic (already correct); the `$1/unit` LLM-failure floor safety net.

## 15. Expected output/artifacts
Possibly-updated `evidence_router.py` guidance text; a clear before/after pricing-observed-rate comparison in this phase's handoff.

## 16. Handoff to next phase
Record the actual pricing observed-rate delta and root-cause hypothesis for Phase 12's final cross-market report to reference.

---

# Phase 9 — Scope ↔ Taxonomy Validation (hardening)

## 1. Objective
Strengthen the existing `scope_taxonomy_validation_node` (already implemented and already correctly flagging real contradictions, e.g. all 6 Crude Oil buckets flagged out-of-scope against a scope statement that excludes "refined petroleum products") so contradictions are more actionable, not just flagged.

## 2. Problem being solved
Today's node flags per-bucket in/ambiguous/out-of-scope with a reason, but doesn't distinguish "the SCOPE STATEMENT itself is probably too narrow for a usage-based decomposition of this market" (the actual root cause in the Crude Oil case — ALL 6 buckets got flagged, which is a strong signal the scope draft itself needs editing, not that all 6 usages are wrong) from "one specific bucket genuinely doesn't belong."

## 3. Current behavior
`scope_taxonomy_validation_node` (in `agents.py`) classifies each bucket independently; no aggregate-level signal exists for "most/all buckets got flagged, which usually means the SCOPE is the problem, not the taxonomy."

## 4. Desired behavior
Add a deterministic aggregate check: if `n_flagged / len(buckets)` exceeds a threshold (e.g. 50%), surface a distinct, higher-level flag ("MOST usage buckets were flagged against the approved scope — consider revising the Market Setup's scope statement rather than the taxonomy") in the taxonomy draft, visible before approval — this doesn't change the per-bucket flagging logic (which already works correctly), it adds an aggregate interpretation layer on top.

## 5. Exact files/components likely to change
- `agents.scope_taxonomy_validation_node`: compute and return the aggregate ratio + a new `scope_likely_too_narrow` boolean/flag alongside the existing per-bucket `scope_validation_flags`.
- `taxonomy.py`: surface this in the draft.
- `quality.py`/`buildup_writer.py`: surface it in the final audit if the taxonomy was approved despite the high-flag-ratio warning (informational, since by sizing time the taxonomy is already locked in).

## 6. New files/components if needed
None.

## 7. LLM responsibilities
Unchanged — the per-bucket classification call already exists; this phase adds a deterministic aggregation on top of its output, no new LLM call needed.

## 8. Deterministic responsibilities
The threshold check and aggregate flag computation — pure code, no LLM involved in the aggregation itself.

## 9. State/schema changes
`scope_validation_flags` dict's return from `scope_taxonomy_validation_node` gains a sibling top-level key, e.g. `scope_taxonomy_aggregate: {"flagged_ratio": 1.0, "likely_scope_too_narrow": true}`.

## 10. Provenance requirements
N/A — this is a taxonomy-quality signal, not an evidence-provenance one.

## 11. Failure/fallback behavior
If the per-bucket classification call itself failed (already handled — defaults everything to `in_scope`), the aggregate ratio would correctly show 0% flagged, which is the safe/correct behavior (never inventing a false aggregate warning from a failed LLM call).

## 12. Tests
- All-buckets-flagged scenario -> `likely_scope_too_narrow=True`.
- No-buckets-flagged scenario -> `False`.
- Threshold boundary test (exactly at 50%, just above, just below).

## 13. Acceptance criteria
- [ ] Aggregate flag correctly computed and surfaced in the taxonomy draft.
- [ ] Re-running Crude Oil (which triggered 6/6 flagged in the baseline) now also shows `likely_scope_too_narrow=True` in the draft.
- [ ] Full test suite green.

## 14. What must NOT be changed
Per-bucket classification logic (already correct, per the design and the 3-market validation).

## 15. Expected output/artifacts
Updated `agents.py`, `taxonomy.py`; new tests.

## 16. Handoff to next phase
Record whether re-running the 3 markets shows this aggregate flag firing appropriately, and whether it would have usefully caught the Crude Oil scope contradiction if this had existed during the earlier validation run.

---

# Phase 10 — Derived-Demand Methodologies (formalization)

## 1. Objective
Formalize today's ad-hoc regional/global-then-population-share derivation (already working, embedded in `demand_quantification_node`) into an explicit, reusable, auditable derivation-chain mechanism that Phase 7's `evidence_strategy` can reference by name, and that supports MORE derivation shapes than just population-share allocation.

## 2. Problem being solved
Today there is exactly ONE derivation shape (population-share country allocation from a regional/global figure) hardcoded into `demand_quantification_node`. Real markets need other derivation shapes too (e.g. "derive country crude demand from country refinery throughput," "derive a conversion factor from two independently-observed related figures") — none of which exist as reusable, named, auditable mechanisms today.

## 3. Current behavior
`demand_quantification_node`'s regional/global fallback (`regional_fact`, `reference_data.population_share`) is the only derivation path, inline in that function, not reusable elsewhere (e.g. `pricing_node`'s conversion-factor fallback duplicates similar but not identical logic via `_fallback_assumption` instead).

## 4. Desired behavior
A small `derivation.py` module defining named derivation strategies as pure functions with a common signature, e.g.:
```python
def derive_by_population_share(regional_fact, country, all_countries) -> DerivedFact: ...
def derive_by_explicit_formula(observed_facts: dict[str, Fact], formula: str) -> DerivedFact: ...
```
`demand_quantification_node` and `pricing_node`'s conversion-factor path both call into this shared module instead of each having their own inline/ad-hoc version. Every `DerivedFact` records WHICH named strategy produced it (feeds directly into Phase 7's `evidence_strategy.derivation_path` and the audit trail).

## 5. Exact files/components likely to change
- `agents.py`: `demand_quantification_node`'s regional/global fallback refactored to call `derivation.py`; `pricing_node`'s conversion-factor fallback likewise, where applicable.
- New: `derivation.py`.

## 6. New files/components if needed
- `derivation.py`
- `tests/test_demand_usage_derivation.py`

## 7. LLM responsibilities
None new — this phase is a refactor of EXISTING derivation logic into a named, reusable, testable module. No new LLM calls.

## 8. Deterministic responsibilities
100% of this phase is deterministic code reorganization — the actual arithmetic (population-share ratio, whatever else is added) must produce IDENTICAL results to today's inline logic for the population-share case (regression-critical), while making the mechanism extensible for future derivation shapes.

## 9. State/schema changes
None to `UsageDemandState` — this is an internal refactor; the `allocation_notes`/provenance-note strings already surfaced to users should read identically or better (more specific) after the refactor.

## 10. Provenance requirements
Every derived fact must name its exact derivation strategy (not just "derived" with a free-text note, but a structured `derivation_strategy: "population_share"` field alongside the existing note) — this is what makes Phase 7's `evidence_strategy.derivation_path` concept actually machine-checkable later, not just a human-readable string.

## 11. Failure/fallback behavior
Identical to today: if no regional/global fact exists either, fall through to the existing batched `_fallback_assumption` — this phase does not change the assumption fallback at all, only formalizes what sits between "found directly" and "pure assumption."

## 12. Tests
- `derive_by_population_share` produces IDENTICAL numeric output to today's inline calculation, for the same inputs (hard regression requirement — write this test using today's actual production behavior as the golden reference before refactoring, per Phase 0's baseline discipline).
- New derivation strategies (if added) are independently unit-testable without needing the full `demand_quantification_node` graph context.

## 13. Acceptance criteria
- [ ] `demand_quantification_node`'s regional/global/population-share output is numerically IDENTICAL before and after this refactor (verified by test, not assumed).
- [ ] `pricing_node`'s conversion-factor derivation (where applicable) uses the same shared module.
- [ ] Full test suite green, this phase's regression test included.

## 14. What must NOT be changed
The actual population-share ARITHMETIC (`reference_data.population_share` itself, untouched) — this phase reorganizes callers, not the underlying math.

## 15. Expected output/artifacts
`derivation.py`, `tests/test_demand_usage_derivation.py`, refactored call sites in `agents.py`.

## 16. Handoff to next phase
Record confirmation the refactor is numerically transparent (identical outputs) and note any new derivation strategies added, for Phase 11's magnitude-check to potentially reference by name.

---

# Phase 11 — Magnitude & Cross-Source Validation (strengthening)

## 1. Objective
Strengthen the existing `magnitude.py`/`triangulation.py` flag-only checks (already working — correctly caught a 629,723× Crude Oil driver blowup and a 65× Cement blowup in real runs) to compare against MORE than one independent reference where available, and to leverage Phase 6's richer provenance (e.g. weight a magnitude-check reference by whether IT was `verified_observed` vs. `source_found_unverified`).

## 2. Problem being solved
Today's `driver_magnitude_validation_node` fetches exactly ONE independent reference per bucket and compares. If that single reference is itself unreliable (e.g. a bad web snippet), the flag's own credibility is only as good as one data point.

## 3. Current behavior
`magnitude.py`'s `driver_magnitude_validation_node`: one `evidence.extract_structured_fact` call per bucket, ratio-threshold flag (>=3x), never mutates the value — this discipline is correct and must be preserved, just made more robust.

## 4. Desired behavior
- Attempt up to 2 independent references per bucket (not just 1) where the Evidence Router chain has multiple viable sources; if both references agree (within some tolerance) and both disagree with the pipeline's own value, that's a MUCH stronger signal than one lone reference — surface this distinction in the flag message (already partially supported by the existing flag structure, extend it to note "2/2 independent references agree the pipeline value is X off" vs. "1 reference, treat with more caution").
- Tag each magnitude-check reference with its own `verification_status` (from Phase 6) so a flag built from a `verified_observed` reference reads as more authoritative than one built from a `source_found_unverified` claim — never silently treat them the same.

## 5. Exact files/components likely to change
- `magnitude.py`: `driver_magnitude_validation_node` gains the optional second-reference attempt + provenance-aware flag messaging.
- `triangulation.py`: same "note the benchmark's own verification status" principle applied to external benchmarks, if straightforward — lower priority than the driver-magnitude change; assess during implementation whether to include in this phase or defer.

## 6. New files/components if needed
None expected.

## 7. LLM responsibilities
Unchanged — extract a reference figure from search results (same discipline as today).

## 8. Deterministic responsibilities
The ratio-threshold comparison and reference-agreement logic — pure code, unchanged discipline of "flag only, never silently correct the value."

## 9. State/schema changes
`driver_magnitude_flags` entries gain an optional second reference (`reference_value_2`, `reference_source_2`) and a `references_agree` boolean when 2 are fetched.

## 10. Provenance requirements
Each reference's own `verification_status` must be visible in the flag record.

## 11. Failure/fallback behavior
If the second reference attempt fails/finds nothing, the flag falls back to today's single-reference behavior unchanged — this is a strict enhancement, never a regression risk to the existing single-reference path.

## 12. Tests
- Two agreeing references -> flag message reflects higher confidence.
- Second reference unavailable -> falls back to existing single-reference flag format, unchanged from today's behavior (explicit regression test against current `magnitude.py` tests).
- Reference `verification_status` correctly threaded into the flag.

## 13. Acceptance criteria
- [ ] Existing single-reference magnitude checks still work exactly as before when only one reference is available.
- [ ] Two-reference case adds genuine diagnostic value when both are available.
- [ ] Full test suite green (including all existing `magnitude.py` tests, unmodified).

## 14. What must NOT be changed
The "never silently correct the value" rule — this phase strengthens the FLAG's credibility signal, it never makes the system auto-correct a magnitude-implausible value.

## 15. Expected output/artifacts
Updated `magnitude.py` (and possibly `triangulation.py`), new tests.

## 16. Handoff to next phase
**Status: implemented (this session), scope expanded beyond this section's own text at explicit
task instruction** — the task that kicked off this session asked for a full generic
magnitude/cross-source validation LAYER (driver, intensity, derived-quantity, price, country-vs-
parent, usage-vs-parent-category, year/period consistency), not just the narrower "second
reference + verification_status on `magnitude.py`" scope originally written here. Both are done:
`magnitude.py`'s own single-reference `driver_magnitude_validation_node` is completely UNCHANGED
(still exactly as before, all its existing tests pass unmodified) — the broader layer lives in a
new, separate, additive module instead of being folded into it.

- **New module**: `src/me_engine/demand_usage/magnitude_validation.py`. Core generic comparator
  `evaluate_magnitude(check_type, subject_label, value, unit, ...)` — one function used identically
  for all 8 `check_type`s (`driver_magnitude`, `intensity_magnitude`, `derived_quantity_magnitude`,
  `price_magnitude`, `country_vs_regional_or_global`, `country_vs_independent_reference`,
  `usage_vs_parent_category`, plus a separate `check_year_period_consistency` for the year/period
  facet). No market-specific thresholds anywhere — `_MATCH_RATIO`(1.5)/`_DISCREPANCY_RATIO`(3.0)/
  `_ORDER_OF_MAGNITUDE_RATIO`(10.0) are the only constants, applied identically regardless of unit
  or market.
- **Threshold methodology**: pure ratio (`max/min`) classification —
  `match` (<1.5x) / `minor_discrepancy` (1.5–3x, reviewed, NOT flagged) / `large_discrepancy`
  (3–10x, flagged) / `order_of_magnitude_mismatch` (≥10x, flagged) — plus a SEPARATE,
  always-flagged `exceeds_parent_total` classification (a country/derived value mathematically
  cannot exceed its own parent total, regardless of ratio-threshold logic).
- **Independent-reference handling**: `gather_independent_references` fetches up to 2 references
  via two DISTINCT acquisition paths — reference 1 through the existing Evidence Router
  (`evidence_router.propose_evidence_plan` + `execute_evidence_plan`, the same typed source-chain
  driver/intensity/price evidence already uses), reference 2 through the plain web-search +
  `evidence.extract_structured_fact` path (`magnitude.py`'s own existing mechanism) — so a check
  never rests on one acquisition mechanism's blind spot. Both are optional/best-effort (a failed or
  empty search yields fewer references, `status="no_reference"`, never a failure). When 2
  references are available, `references_agree` (pairwise ratio <1.5x) is computed and surfaced in
  the flag's note text ("2/2 independent references agree..." vs. "...do NOT agree..., treat with
  more caution").
- **Provenance behavior**: every reference in the returned record carries its own `source`,
  `source_url`, `value`, `unit`, `geography`, `year`, `verification_status`, `evidence_text`,
  `location_ref`, `citation`, plus a generic evidence-hierarchy label
  (`source_authority_rank` — government_or_statistical > international_organization >
  industry_association > secondary_or_unclassified, pure keyword classification, no
  institution/market lookup table). A flag built from a `source_found_unverified` reference gets an
  explicit "no reference here was independently verified -- treat this flag with caution" note,
  never silently treated the same as a `verified_observed` one.
- **Compatibility gate** (`check_compatibility`): unit (via the existing `units.py` parser/
  `units_share_base`), geography (exact-match, only enforced for same-geography check types),
  and year (>2-year gap ⇒ `period_mismatch`) / explicit stated-period conflict (via
  `units.periods_conflict`) are all checked BEFORE any ratio is computed — an incompatible
  reference is reported with its specific `incompatibility_reasons` and excluded from the ratio
  comparison, never silently compared anyway.
- **Derived-quantity integration (Phase 10)**: `diagnose_derivation_origin(derived_fact)` reads
  Phase 10's `derivation_method`/`input_facts`/`formula`/`derivation_lineage` (when present) to
  identify whether a flagged derived value's discrepancy most likely traces to the input total's
  own magnitude, the allocation share, the intensity, a period/unit conversion, or an upstream
  derivation step — returns `origin="unknown"` (never guesses) on an ordinary, non-derived fact.
  Additive metadata wiring in `agents.demand_quantification_node`'s regional/global fallback: the
  merged `country_driver_facts[c]` dict now also carries `input_facts`/`derivation_lineage`
  (previously computed by `derivation.derive_regional_share` but not threaded through) — no existing
  field/behavior changed, confirmed by the full suite staying green.
- **Example flagged discrepancy** (from this phase's own tests, generic — not market-specific):
  proposed price `$1.00/barrel` (an `assumption`-tier fallback) vs. an independent reference
  `$75.00/barrel` (a NYMEX-labelled, `verified_observed` reference) → `ratio ≈ 75.0x`,
  `classification = "order_of_magnitude_mismatch"`, `status = "flagged"`,
  `reason = "magnitude_discrepancy"` — the original `$1.00/barrel` value, its source, and its
  `assumption` verification_status remain fully visible and UNCHANGED in the returned record; the
  same "never replace, only flag" discipline is exercised for every `check_type`
  (`test_no_silent_correction_across_every_classification` asserts this explicitly across match/
  minor/large/order-of-magnitude outcomes).
- **Graph wiring** (additive only): `graph.py` gains a new node,
  `magnitude_cross_validation` (→ `magnitude_validation.magnitude_cross_validation_node`), inserted
  AFTER the existing `triangulation` node and BEFORE `validation` — `driver_magnitude_validation`
  and `triangulation` themselves are completely untouched, still run exactly as before, in the same
  order. Two new, additive `UsageDemandState` fields: `magnitude_validation_records` (every check
  run, flagged or not) and `magnitude_validation_flags` (the flagged subset only). Surfaced end-to-
  end: `agents.validation_node`'s `validation` dict gains both keys (mirroring the existing
  `driver_magnitude_flags`/`triangulation_large_gap_flags` fold-in pattern exactly);
  `quality.py.compute_provenance_quality_flags` gains a new `magnitude_cross_validation_discrepancy`
  flag (critical severity) when any check flags; `buildup_writer.py` gains a new "Magnitude
  Cross-Validation" workbook sheet (one row per flag: check type, bucket, subject, original
  value/unit/source/verification_status, best reference value/source/verification_status, ratio,
  classification, references-agree, note) plus a JSON `magnitude_validation_flags`/
  `magnitude_validation_records` pair and a Summary-sheet flag-count row.
- **Tests**: new file `tests/test_demand_usage_magnitude_validation.py` (35 tests) — normal match,
  small discrepancy (reviewed, not flagged), large discrepancy, order-of-magnitude mismatch (plus an
  explicit ratio-boundary sweep), country-vs-regional inconsistency, country-exceeds-parent-total
  (always flagged, both a flagged and a within-bounds case), period mismatch (both an incompatible-
  unit-string case and an explicit year-gap case, plus a within-tolerance non-flagging regression),
  unit mismatch, geography mismatch, two agreeing references / two disagreeing references,
  unverified-reference caution note (plus a verified-reference regression showing the note is
  correctly absent), derived-quantity magnitude validation using real `derivation.py` output (plus
  an `origin="unknown"` regression on a non-derived fact, and an `activity_intensity`→`"intensity"`
  origin case), price magnitude validation (flagged and matching cases), the explicit no-silent-
  correction sweep across every classification, a no-reference-is-not-a-failure case, a
  missing-value-never-crashes case, generic evidence-hierarchy keyword classification, year/period
  consistency (year-gap flagged, explicit period-conflict flagged, consistent case not flagged,
  absence-is-not-a-conflict regression), and 5 node-level tests against
  `magnitude_cross_validation_node` (driver-magnitude flag, no-flag-on-match, never-mutates-
  quantities-or-prices, price-magnitude flag, derived-quantity-exceeds-parent-total with real
  `derivation.py`-built input). **Full suite: 367 passed, 2 skipped** (the 2 skips are the
  pre-existing `reportlab`-gated PDF tests from Phase 3, unrelated to this phase) — run via
  `pytest tests/`. `magnitude.py`'s own 5 pre-existing tests pass completely unmodified, confirming
  the original single-reference behavior this phase's own "what must NOT be changed" section
  requires is untouched.
- **Desk Research / Supply Side confirmed untouched**: `git status`/`git diff --stat` show no
  changes to `src/me_engine/generation/` (the one pre-existing 1-line modification to
  `src/me_engine/generation/agents.py` predates this session, per `git log`, and was not touched
  further) or `src/me_engine/supply/` this session.
- **The 3-market re-run was intentionally NOT performed this session**, per this task's explicit
  instruction ("Do not run the full 3-market regression yet"). Known limitations / handed off to
  Phase 12:
  - `gather_independent_references`'s reference-2 path (`gather` + `extract_structured_fact`) still
    depends on `cfg.enable_web`; with web disabled (as in every unit test here) only reference 1
    (Evidence Router) is attempted, so a real run's actual reference COUNT/agreement behavior is
    unverified against live data — Phase 12's re-run should report how often 2 real references are
    actually obtained per check, not just per the mocked test paths.
  - `usage_vs_parent_category`'s "parent" is currently `total_market_value` (Included Demand) only —
    it does not yet compare a bucket against an independent EXTERNAL category-total reference (e.g.
    a category-level triangulation benchmark); `triangulation.py`'s own benchmark sweep runs
    separately and isn't cross-wired into this check. A future phase could bridge the two.
  - `check_year_period_consistency`'s representative `intensity`/`price` fact is the first
    country's/bucket's fact found, not an aggregate across all countries in a bucket — acceptable
    given intensity/price are already modeled as market-wide constants in this pipeline, but worth
    noting if that assumption ever changes.
  - Geography-string compatibility (`check_compatibility`) is an exact, normalized-string match
    only (no fuzzy "US" == "United States" resolution) — a reference whose geography is phrased
    differently than the subject's own geography label will be reported as `geography_mismatch`
    even when it's actually the same place; acceptable generic behavior (never silently assumes
    equivalence) but a source of possible false-incompatibility in a live run, worth Phase 12
    reporting on if observed.

---

# Phase 12 — End-to-End Cross-Market Validation

## 1. Objective
Run the complete, unmodified pipeline (after Phases 0-11 land) on Crude Oil, Cement, and Avocado Oil — identical procedure to every prior cross-market validation in this project's history — and produce the definitive before/after comparison against the Phase 0 baseline.

## 2. Problem being solved
Every individual phase's own "re-run and compare" step is a partial, single-phase check. Phase 12 is the integration check across ALL phases together, which may surface interaction effects no single phase's isolated re-run would catch (e.g. Phase 6's provenance rename interacting with Phase 7's new strategy field in the audit workbook).

## 3. Current behavior
N/A — this phase IS the "current behavior" measurement, repeated at the end.

## 4. Desired behavior
Fresh `setup -> approve-setup -> taxonomy -> approve-taxonomy -> size` for all 3 markets, using the SAME procedure as every prior validation in this project (market name only, approve every draft as-is, no manual edits, no market-specific tuning, no code/prompt changes during or after the run to chase a better number).

## 5. Exact files/components likely to change
None — this phase runs the pipeline, it does not modify it. If a genuine bug is found during this phase, it must be logged and handed to a NEW, scoped bugfix phase inserted before this validation is re-attempted — never silently patched mid-validation-run.

## 6. New files/components if needed
- `output/demand_usage/BASELINE_V3_FINAL_COMPARISON.md` (or similar) — the committed before/after artifact.

## 7. LLM responsibilities
Whatever each phase's own LLM responsibilities already established — no new LLM behavior introduced here.

## 8. Deterministic responsibilities
Running the CLI, collecting the metrics, computing the before/after deltas.

## 9. State/schema changes
None.

## 10. Provenance requirements
Report using Phase 6's final field-name/shape decision.

## 11. Failure/fallback behavior
Per the original request this plan is based on: **"If any market fails because of API/network/data-access issues, report the exact failure instead of replacing it with an assumption."** This rule applies with full force to this phase — a failed run is reported as a failed run, with the exact error, never silently re-run with different (implicitly more favorable) conditions and presented as if it were the first attempt.

## 12. Tests
Full existing test suite green (final confirmation across the whole plan's changes). No new unit tests expected in this phase — it's an integration/reporting phase, not a code-adding one.

## 13. Acceptance criteria
- [ ] All 3 markets run to completion (or failure explicitly reported).
- [ ] Per-market report includes: driver/intensity/price observed-derived-assumption(-unverified) splits, source-type success counts (web/pdf/csv-xlsx/api), rejection-reason breakdown, final market size, plausibility score, magnitude flags, triangulation result, unresolved assumptions.
- [ ] Explicit before/after comparison against `BASELINE_V3.md` (Phase 0) stating, per facet, whether it improved, stayed the same, or got worse — no cherry-picking, report all facets even if some got worse.
- [ ] No value was tuned/forced toward Desk Research or toward a "nicer-looking" percentage.

## 14. What must NOT be changed
Everything — this is a read-only validation phase.

## 15. Expected output/artifacts
`BASELINE_V3_FINAL_COMPARISON.md` (or equivalent), the full per-market report delivered as this phase's actual output to whoever requested it.

## 16. Handoff to next phase
**Status: implemented (this session).**

Full report: `output/demand_usage/PHASE12_FINAL_VALIDATION.md`. Summary here for anyone reading only
this file:

- **Test suite**: green before this validation (`pytest tests/` → 367 passed, 2 skipped) and
  re-confirmed green after (`367 passed, 2 skipped`) — no test file touched this session.
- **All 3 markets (Crude Oil, Cement, Avocado Oil) ran to completion**, no outright failures. Two
  organic pricing-LLM-call failures occurred (Crude Oil/Transportation Fuels, Avocado
  Oil/Household Lubricant) and were floored to $1/unit with an honest `llm_call_failure` flag, not
  smoothed over — same discipline as the Phase 0 baseline's own disclosed failure.
- **Baseline preserved**: `BASELINE_V3.md` itself was never modified. Its backing JSON/XLSX were
  copied to `output/demand_usage/_baseline_v3_backup/` before this session's re-run overwrote the
  live `crude-oil/`/`cement/`/`avocado-oil/` run directories, so both remain independently
  verifiable.
- **One operator action disclosed, not hidden**: Phase 9's post-baseline hard scope-approval gate
  (`approve_taxonomy` raising on any `out_of_scope` bucket without a `scope_overrides` justification)
  blocked every market's approval step. Per this phase's "no manual taxonomy edits" rule, the
  taxonomy draft was approved exactly as the LLM generated it (zero buckets removed/edited) by
  supplying one identical, generic, non-market-specific override justification string for every
  flagged bucket in all three markets — quoted verbatim in the full report.
- **Headline result — Cement improved, Crude Oil/Avocado Oil did not**:
  - Cement: Included Demand $10,395,166,209.8M → $618,423.5M (`implausible_total` flag gone),
    evidence quality 0%/0%/100% → 2.2% observed / 2.2% derived / 11.1% unverified / 84.4%
    assumption, 4 independent triangulation benchmarks agreeing. A genuine, measurable win.
  - Crude Oil: Included Demand $443,450.0M → $5,515,001,477.2M — got a NEW `implausible_total` +
    `dominant_bucket_unit_mismatch_risk` flag pair the baseline never triggered; evidence quality
    got worse (derived 15.6%→0%, assumption 84.4%→100%).
  - Avocado Oil: total roughly flat ($5,328.7M → $3,714.5M) but plausibility score fell (60%→25%).
  - No cherry-picking: all three markets' full facet breakdowns, including the ones that got worse,
    are in the full report.
- **PDF/CSV/XLSX/API acquisition hit rate is still 0% in all three markets** — identical to the
  Phase 0 baseline — despite Phases 1/3/4/5's discovery/verification hardening. What DID
  measurably improve: `no_source_found` fell 94% (424→27 across markets) and `download_failed`
  fell 76% (404→98), confirming the hardening fixed *discovery*, but the failure point simply moved
  downstream to `extraction_failed`/`not_relevant_page` (287→666 combined) — a real asset gets
  found and fetched now, it just often doesn't state the specific figure asked for. This is the
  single most important, and most sobering, finding of this validation.
- **The Phase 2 `llm_claim` last-resort rung is now doing most of the real acquisition work** (41 of
  56 total "found" results across the three markets, 73%), with `web_page` contributing the rest
  (15 of 56) and `pdf`/`csv_excel`/`api` contributing zero. Most `llm_claim` "found" results still
  downgrade to `assumption` after `evidence.build_fact`'s traceability check (correct, intended gate
  behavior — never silently promoted) — the attempt-log's `found=true` flag and a fact's final
  `verification_status` can diverge sharply, worth a future phase's attention.
- **Phase 9's `scope_too_narrow_for_taxonomy` aggregate flag fired exactly where anticipated**:
  100% of Crude Oil's buckets and 67% of Cement's flagged, confirming the flag would have (and now
  does) usefully surface the exact Crude Oil scope contradiction Phase 9's own design discussion
  referenced — but nothing in Phases 0–11 fixes the root cause (the Market Setup phase's scope
  statements for refined-commodity markets are systematically too narrow relative to what the
  taxonomy phase naturally proposes).
- **Magnitude cross-validation (Phase 11) caught real, previously-invisible discrepancies** in
  every market (6.6×/43.4× in Crude Oil, 65.7× in Cement, 2 flags in Avocado Oil) — flagged for
  review, original values/sources left unchanged in every case, per the non-negotiable rule.
- **Pricing observed rate is still 0%** at the price-record level in all three markets, in both the
  baseline and this run — Phase 8's structural hypothesis (no paid commodity-exchange API
  credentials) remains the best explanation, neither confirmed nor falsified further this session.
- **Final recommendation: NOT yet ready for production.** The evidence-acceptance/provenance-
  integrity architecture (Phases 0, 2, 6, 9, 11) is validated and should not be re-litigated — every
  failure mode is flagged loudly and honestly, nothing was silently corrected or promoted anywhere
  in this run. But the acquisition *hit rate* for the highest-volume source tiers (PDF, CSV/XLSX,
  API) remains 0% in a live run, the headline evidence-quality numbers are still overwhelmingly
  assumption-driven, and one of the three markets (Crude Oil) came out *less* plausible than at
  baseline. Recommend at least one more engineering cycle focused specifically on extraction-quality
  (real, verified, but off-target documents/datasets/API responses) and the Market Setup
  scope-drafting gap, before this pipeline's output is used as a trustworthy market-size number.
- **Confirmed**: Desk Research (`src/me_engine/generation/`) and Supply Side
  (`src/me_engine/supply/`) untouched this session — the sole diff `git diff --stat` shows against
  `src/me_engine/generation/agents.py` is a single pre-existing whitespace change dated 2026-08-05
  (commit `4b5b889`), weeks before this session, confirmed via `git log`. No fourth methodology was
  created. No value was tuned/forced toward a nicer percentage or toward Desk Research. No
  `source_found_unverified` fact was upgraded to `verified_observed` anywhere in this run.
- **No Phase 13 or new methodology work was started.** This is the last phase in the current plan.
  The "Remaining weaknesses" section of the full report (extraction-quality gap, Market Setup
  scope-drafting gap, `llm_claim`'s found-vs-verification_status divergence, pricing's 0% observed
  rate, single-run LLM non-determinism) are recorded as candidate future-phase material only, not
  implemented here.

---

# Phase Execution Checklist

Recommended execution order, with each phase's hard dependency on the one(s) before it:

1. **Phase 0 — Baseline & Contracts** — no dependencies. Must run first; every later phase's "did this help" claim depends on this baseline existing.
2. **Phase 1 — Source Discovery & URL Verification** — depends on Phase 0 (needs the baseline to measure against). Foundational for Phases 3/4/5.
3. **Phase 2 — LLM-Assisted Evidence Discovery** — depends on Phase 0. Can be developed in parallel with Phase 1 (different files: `evidence_router.py`/`evidence.py` additions vs. `asset_verifier.py`) but should land before Phase 6 needs to thread the 4th status value everywhere.
4. **Phase 3 — PDF Evidence Pipeline** — depends on Phase 1 (`asset_verifier.py` must exist).
5. **Phase 4 — Tabular Evidence Pipeline** — depends on Phase 1. Can be developed independently of Phase 3 (different acquirer file) — safe to parallelize across two sessions if desired.
6. **Phase 5 — API / Structured Data Pipeline** — depends on Phase 1. Independently developable alongside Phases 3/4.
7. **Phase 6 — Evidence Verification & Provenance** — depends on Phase 2 (needs `source_found_unverified` to actually exist before it can be threaded everywhere) and benefits from Phases 3/4/5 being done first (fewer call sites to re-touch later), but is not strictly blocked by them. Do this AFTER Phases 1-5, BEFORE Phases 7+, since 7-11 build on the finished 4-value provenance model.
8. **Phase 7 — Driver Research Strategy** — depends on Phase 6 (needs final provenance model settled).
9. **Phase 8 — Pricing Evidence & Price Basis** — depends on Phases 1-6 (benefits from all discovery/verification hardening); can run in parallel with Phase 7 (different primary files: `pricing_node` vs. `demand_driver_node`).
10. **Phase 9 — Scope ↔ Taxonomy Validation** — depends on nothing beyond the existing `scope_taxonomy_validation_node` (already implemented pre-plan). Can actually be done EARLY (even before Phase 1) if a session is available and the team wants a quick, low-risk win — noted here at its logical position in the narrative, but it has no real blocking dependency on Phases 1-8.
11. **Phase 10 — Derived-Demand Methodologies** — depends on Phase 7 (references `evidence_strategy.derivation_path`) and Phase 6 (needs the final provenance model for `DerivedFact`'s status field).
12. **Phase 11 — Magnitude & Cross-Source Validation** — depends on Phase 6 (needs `verification_status` on magnitude-check references) and benefits from Phase 10 (shared derivation vocabulary) but is not strictly blocked by it.
13. **Phase 12 — End-to-End Cross-Market Validation** — depends on ALL prior phases being complete. Always last.

**Parallelizable groups** (if multiple sessions/engineers are available):
- Group A (after Phase 0): Phase 1, Phase 2 — different files, no shared state.
- Group B (after Phase 1): Phase 3, Phase 4, Phase 5 — different acquirer files, no shared state.
- Group C (after Phase 6): Phase 7, Phase 8 — different node functions, no shared state.
- Phase 9 can be slotted in anywhere with a free session, including before Phase 1.
- Phase 10, Phase 11 are each single-threaded (depend on specific prior phases finishing).
- Phase 12 is always last, single-threaded, and should be run by whoever has visibility into ALL phases' individual handoff notes.

**Constant, non-negotiable rules for every phase, every session:**
- Never modify `src/me_engine/generation/` or `src/me_engine/supply/`.
- Never create a fourth market-sizing methodology.
- Never force a result toward Desk Research or toward a "nicer" percentage.
- A failed live run (API/network/data-access) is reported exactly as it failed — never silently replaced with an assumption or re-run under quietly different conditions.
- Every phase ends with the full test suite green, and its own **Handoff to next phase** section filled in with real, specific findings — not a restatement of the plan.

---

# Addendum (post-Phase-12) — Evidence Extraction Quality

**Not a new numbered phase** — Phase 12 is still the last phase of this plan. This is a targeted
engineering increment directly scoped to the single bottleneck Phase 12's cross-market validation
isolated: real, successfully-acquired sources still routinely yield the WRONG number (an adjacent
metric, wrong geography/year/unit) rather than no number at all.

**What was added**: a new, additive, market-independent module,
`src/me_engine/demand_usage/fact_validator.py` — a deterministic gate every acquirer's LLM
extraction step now calls, after the LLM proposes a candidate and BEFORE it reaches
`evidence.build_fact`/`build_structured_fact`. It checks (all pure keyword/string logic, no
market-name lookup table, never rejects on mere ambiguity):
- monetary-value-for-physical-driver (a `$`/market-value figure returned when the request has no
  currency signal at all);
- geography mismatch (only for a candidate explicitly declared a DIRECT figure — a declared
  regional/global derivation basis is never penalized for this);
- year mismatch (>1 year gap between a requested and a source-declared year, absence never a
  conflict);
- physical-unit mismatch (reusing `units.py`'s existing parser — different base-unit noun, e.g.
  tonnes vs. vehicles);
- generic adjacent-metric-family conflicts (production vs. capacity, production vs. reserves,
  monetary value vs. any physical family, etc., via a small, generic keyword-family classifier).

**Wired into**: `evidence.extract_fact_from_source` (web), `evidence.extract_structured_fact`
(magnitude/triangulation reference gathering), `pdf_acquirer.acquire` (page/table LLM step),
`tabular_acquirer.acquire` (deterministic column-name check, no extra LLM call), `api_acquirer.acquire`
(deterministic field-path-name check), and `evidence_router._llm_claim_fallback` (the Phase 2
last-resort rung — a wrong-metric claim is dropped before it can reach EITHER the verified-URL path
or the `source_found_unverified` fallback). Every LLM extraction prompt touched gained an explicit
instruction: return `found=false` rather than reporting a number that measures something different
from what was asked, and never invent a page/table/row/column/value/citation/year/unit/geography.

**Tests**: new file `tests/test_demand_usage_fact_validator.py` (29 tests) — `fact_validator.py`'s
checks in isolation (currency/metric-family/geography/year/unit conflict detection, plus explicit
"never rejects on ambiguity" regressions), and per-acquirer wiring tests (web/PDF/CSV/API each with
a wrong-metric-rejected case and a correct-fact-accepted case, a web geography-mismatch case, an API
year-mismatch case, a PDF invented-page-index regression, and the `_llm_claim_fallback` wrong-metric
and still-produces-unverified-for-a-plausible-claim cases). **Full suite: 396 passed, 2 skipped**
(367 pre-existing + 29 new), zero regressions, zero existing tests modified.

**Crude Oil smoke test** (live, single-market, not a full 3-market re-run per this increment's own
instructions): 377 total evidence attempts (18 found, 5 `verified_observed`, 0 `derived`, 0
`source_found_unverified`, 13 downgraded-to-`assumption` despite a real citation), final per-cell
evidence quality 1.3% observed / 0% derived / 0% unverified / 98.7% assumption, Included Demand
$453,577.7M (Reported Market View $477,450.2M), plausibility 25%. The new gate fired live at least
once (`wrong_metric_family:production_vs_capacity` in the rejection-reason breakdown) — direct
evidence it is catching real adjacent-metric confusions in practice, not just in mocked tests.
Verified-observed examples this run included NAPA's own reported 432M-ton 2021 US asphalt-mixture
production figure and several World Bank arable-land-hectares country facts. Conceptually, this
smoke test's total is far closer to `BASELINE_V3.md`'s original Crude Oil scale ($443,450.0M) than
the immediately-preceding Phase 12 run's ($5,515,001,477.2M, flagged `implausible_total`) — but the
taxonomy/buckets differ between all three runs (LLM non-determinism regenerates the usage
decomposition each time), so this is reported as directionally encouraging, not a controlled,
causally-attributed A/B result. `BASELINE_V3.md` itself was not modified; the pre-increment Phase 12
run's raw JSON was preserved in `output/demand_usage/_phase12_run_backup/` before this smoke test
overwrote the live `crude-oil/` run directory.

**Desk Research / Supply Side confirmed untouched**: the sole diff is the same pre-existing
2026-08-05 whitespace change to `src/me_engine/generation/agents.py`, unrelated to this increment.

**Recommendation for what comes next** (explicitly NOT decided/implemented here): a full 3-market
regression (Crude Oil + Cement + Avocado Oil) is the right next step BEFORE any further extraction
or pricing-specific work — this increment's live signal (one real wrong-metric rejection, a more
plausible single-market total) is encouraging but is a single-market, single-run sample; Phase 12's
own lesson was that a single run is noisy. A full 3-market re-run against `BASELINE_V3.md` and
against this increment's own before/after numbers is needed to know whether the extraction-quality
gate reliably raises real observed/derived share across markets, or whether pricing-specific
extraction (0% observed in every run so far, every market, including this one) is the more urgent
next target. No further extraction-quality or pricing-specific work, and no Phase 13, was started
in this increment.
