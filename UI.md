# UI.md — Pipeline Demo screen (third tab)

This document is the durable reference for the "Pipeline Demo" tab in
`frontend/src/app/page.tsx` — the third tab, replacing what used to be
"Research Audit". Read this before editing anything under
`frontend/src/lib/pipelineDemo/` or `frontend/src/components/demo/`.

## 1. Purpose

The app has three tabs:

1. **Desk Research** — real pipeline, untouched by this work.
2. **Demand-Side (Usage-Based)** — real pipeline wizard, untouched by this work.
3. **Pipeline Demo** (this doc's subject) — a **single-viewport, sequential,
   click-through walkthrough** ("wizard") of how the two sizing
   methodologies work, built for a non-technical audience (a CEO or market
   researcher watching someone click through it live). It opens directly
   into **Crude Oil — Usage-Based Demand Sizing** — there is no market
   picker. It is explicitly **not** a scrolling audit/dev-dashboard: the
   main frame (header, step rail, current-step panel, footer) fits one
   viewport with no page-height growth; anything long (a prompt, a full
   search-attempt chain, a calculation) lives behind a "View details"
   button that opens a right-side drawer, never by growing the page.

This is a **different layer** from the real audit instrumentation that
already exists and still works:

- `src/me_engine/audit/` — the real, tested backend audit-event emitter
  (stage_start/stage_end/llm_call/search_query/source_attempt/
  fact_validation/derivation/magnitude_check/taxonomy_generated/
  scope_override events).
- `/api/audit/*` — the real backend routes that expose those events
  (run list, run detail, events, timeline, lineage).
- `frontend/src/components/audit/*` (`AuditDashboard.tsx`,
  `RunPicker.tsx`, `ExecutionTrace.tsx`, `Evidence.tsx`,
  `ValidationPanel.tsx`, `NumberLineage.tsx`, `TaxonomyExplorer.tsx`,
  `Comparison.tsx`, `PipelineTimeline.tsx`, `RawEvents.tsx`,
  `useAuditEvents.ts`, `shared.tsx`, `index.tsx`) — the real-data UI built
  against those routes in an earlier session. **These files are NOT
  touched by this work and are NOT rendered anywhere today.** They remain
  available to reuse (see §5) once "Step-by-Step Run" mode is wired to a real
  running pipeline instead of the current simulated playback.

Today, the third tab renders `PipelineDemo` (`frontend/src/components/demo/PipelineDemo.tsx`),
which is now a wizard shell, not a scrolling list.

## 2. The wizard interaction model

```
HEADER      market name + approach switch (Usage-Based Demand / Desk
            Research) + researcher-mode toggle + mode toggle
            (● Replaying Completed Run / ☑ Step-by-Step Run)
STEP RAIL   compact numbered nodes with connecting lines, current step
            highlighted, horizontally scrollable, every node directly
            clickable to jump (StepRail.tsx) — in Step-by-Step Run mode,
            node coloring instead reflects each step's per-step run status
            (locked/ready/running/completed/failed)
STEP PANEL  two columns for the CURRENT step only:
              left  = "INPUT FROM PREVIOUS STEP" callout (if this step
                      genuinely carries a value forward from the one
                      before it) / what went in / what it did / why /
                      what came out (clickable to open a number-lineage
                      drawer, when traceable) / "PASSED TO NEXT STEP"
                      callout (if this step's output feeds the next one) /
                      provenance badge / human-decision card (if any)
              right = "View details" buttons that open a drawer for each
                      evidence artifact present (LLM call, search queries
                      & sources, conversion, calculation, validation
                      checklist) — never expands the panel itself
            (StepPanel.tsx + DetailDrawer.tsx)
FOOTER      ← Previous | "Step X of N" + progress bar | right-hand button
            (WizardFooter.tsx) — always visible, fixed position, only the
            step panel's content changes on Prev/Next. In Replay mode the
            right-hand button is always "Next Step →" (unchanged). In
            Step-by-Step Run mode its label/action reflects the CURRENT
            step's run status: "ready" → "▶ Run Step" (runs in place),
            "running" → "Running…" (disabled), "completed" → "Next Step →"
            (navigates), "locked" → "🔒 Locked" (disabled).
```

Clicking a rail node or a footer button changes ONLY the current step
panel — rail and footer never move. Clicking a "View details" button opens
`DetailDrawer.tsx`, a fixed right-side overlay; it never navigates away from
the wizard and never grows the main page's height.

**Two top-level modes** (`PipelineDemo.tsx`'s `mode: "replay" | "stepwise"`):

- **① "Replaying Completed Run"** (default). Every step's result already
  exists. Clicking any step in the rail, in any order, immediately shows its
  full content — never a "running" simulation, never re-computed. Unchanged
  from before.
- **② "Step-by-Step Run"** (replaces the old fixed-interval auto-advance
  "Live Run" entirely — that mode no longer exists). Each step carries its
  own `RunStepStatus` (`"locked" | "ready" | "running" | "completed" |
  "failed"`, `types.ts`), owned as wizard component state (`runStatuses`),
  never stored on the dataset. Entering this mode (or switching approach
  while in it) resets: step 0 → `"ready"`, every other step → `"locked"`.
  - **Locked**: clicking a locked step shows a "🔒 Locked" placeholder —
    never its real content.
  - **Ready**: shows a brief description (from `step.action`) and a
    centered "▶ Run This Step" button.
  - **Running**: clicking Run transitions to `"running"` and plays a short
    progressive reveal (~1.8s) of 2-3 phase lines derived from what the step
    actually contains (e.g. a step with `search_queries` shows
    "Searching sources… → Source found… → Extracting value…"; a step with
    an `llm_call` shows "Calling the LLM… → Parsing response…") — not the
    same phases for every step. It then becomes `"completed"`, renders the
    same full content Replay mode shows, and marks the NEXT step `"ready"`
    (the wizard never auto-advances the current index — the user clicks
    Next/the rail themselves).
  - **Completed**: clicking it again just shows its content, like Replay
    mode — never re-runs.
  - **Failed**: implemented for completeness (a red "🔴 Step failed" +
    "Retry" placeholder that returns the step to `"ready"`) but nothing in
    the demo actually triggers it — there is no real failure path since
    this is simulated.
  Real live wiring (polling `/api/audit/*` instead of a client-side
  simulated reveal) remains an explicit follow-up, not attempted here.

## 2b. Input→output chaining between steps (both modes)

Every `PipelineStep` can optionally carry two short strings that make the
pipeline's data flow explicit, independent of which mode is active:

- `carried_input?: string` — "this exact value came from the previous
  step", e.g. the Pricing step's `carried_input` is `"432,000,000 metric
  tons/year (verified driver quantity, from Evidence/Derivation step)"`.
  Rendered by `StepPanel.tsx` as an amber-left-border callout labeled
  "INPUT FROM PREVIOUS STEP", above "What went in". Only populated where a
  real chain genuinely exists — not every step has one.
- `feeds_into?: string` — "→ this step's output becomes the input to:
  &lt;next step&gt;". Rendered as a callout below "What came out", labeled
  "→ PASSED TO NEXT STEP"; clicking it jumps to the next step via a
  `nextStepId` + `onJumpToStepId` prop threaded from `PipelineDemo.tsx`
  through `StepPanel.tsx`.

Both datasets populate this for their real chains: in
`crudeOilUsageBased.ts`, Search Query & Retrieval → LLM Extraction → Validation
→ Derivation/Conversion → Pricing (the concrete 432,000,000 metric ton NAPA
example) → Calculation → Final Market Size; in `crudeOilDemandSide.ts`,
structure → market_size → geo → shares → asp → reward → finalize → brain →
final_estimate.

## 2c. Clickable numbers → lineage drawer

`StepPanel.tsx`'s "What came out" block becomes a clickable button whenever
the step has genuinely traceable numeric output (`step.calculation`,
`step.conversion`, or `carried_input`/`feeds_into` chain data) and opens
`NumberLineageTrace.tsx` via `onTraceNumber(stepId?)` — generalized from the
old final-step-only wiring to accept an optional starting step id.
`NumberLineageTrace` accepts an optional `startStepId` prop that determines
which node in the (always-full) backward chain starts highlighted, instead
of always defaulting to the final estimate.

## 3. The `PipelineStep` data model (`frontend/src/lib/pipelineDemo/types.ts`)

`PipelineStep` is the one shape every step in every approach dataset must
conform to — a plain TypeScript interface, zero JSX, zero component imports.

| Field | Type | Meaning |
|---|---|---|
| `step_id` | `string` | Stable identifier (used for rail jump / drawer routing / quality-flag deep links). |
| `step_name` | `string` | Human-readable step name shown on the rail node and panel header. |
| `status` | `"done" \| "active" \| "pending"` | Legacy visual status; both datasets today mark every step `"done"` (a completed run being replayed) — the wizard's own `index` state drives "current". |
| `data_tag` | `"real" \| "illustrative"?` | Which tier this step's content belongs to. Rendered as a small badge, never a blanket page label. |
| `data_tag_note` | `string?` | Specific note on what's real/illustrative/missing for this step — never generic "not captured" filler. |
| `human_decision` | `{summary, justification}?` | A recorded HUMAN (not automated) decision — e.g. the real Crude Oil taxonomy scope override. Rendered by `HumanDecisionCard.tsx` with explicit "not automated" framing. |
| `input` / `action` / `reason` | `string?` | 1–3 line "what went in / what it did / why". |
| `output` | `string \| string[]?` | Visual output — a headline number/string, or a chip list. |
| `llm_call` | `LLMCall?` | Behind a "View details" drawer — never inline. |
| `search_queries` | `SearchAttempt[]?` | One drawer button per attempt; each `SourceResult` inside can carry `candidatesTried` (multiple URLs tried under one logical source, mirroring real `retrieval_attempts[...].candidates_tried`). |
| `sources` | `SourceResult[]?` | Sources shown outside a search attempt. |
| `conversion` | `Conversion?` | Behind a drawer — unit/price-basis bridge (e.g. vehicle-km → barrels). |
| `calculation` | `Calculation?` | Behind a drawer — inputs → formula → output. |
| `validation` | `ValidationCheck[]?` | Behind a drawer — ✓/✕ checklist with ACCEPTED/REJECTED verdict. |
| `evidence_quality` | `EvidenceQualityBreakdown?` | Only present on the final-estimate step. |
| `provenance` / `provenance_note` | | One of `verified_observed \| derived \| source_found_unverified \| assumption`, shown inline (not behind a drawer — it's a compact badge). |
| `next_step_reason` | `string?` | Short inline "why next" banner (`WhyNextCard.tsx`) — short enough to stay inline. |
| `researcher_note` | `{level, note}?` | Shown only when Researcher mode is on, as a compact flag in the right column. |
| `carried_input` | `string?` | "This exact value came from the previous step" — amber callout above "What went in". Only where a real chain exists (see §2b). |
| `feeds_into` | `string?` | "→ becomes the input to: &lt;next step&gt;" — callout below "What came out", clickable to jump forward (see §2b). |

Nested types unchanged from before (`LLMCall`, `SearchAttempt`, `SourceResult`,
`SourceCheck`, `ValidationCheck`, `Calculation`, `Conversion`,
`EvidenceQualityBreakdown`), plus two new ones:

- `QualityFlag { code, severity: "info"|"warning"|"critical", message, step_id }`
  — one real `quality_flags[]` entry from `02_sizing.json`, plus which step
  it deep-links back to. Consumed by `QualityFlagsPanel.tsx` on the
  dedicated `review_researcher` step.
- `HumanDecision { summary, justification }` — see above.

## 4. Where the datasets live

Everything is under `frontend/src/lib/pipelineDemo/`:

- `types.ts` — the model above.
- `crudeOilUsageBased.ts` — exports `crudeOilUsageBasedSteps: PipelineStep[]`
  (14 steps: Input/Setup → Market Understanding → Taxonomy → Taxonomy
  Approval (human override) → Geography → Evidence Requirement → Search
  Query & Retrieval Attempts → LLM Extraction (failure case) → Validation →
  Derivation/Conversion → Pricing → Calculation → Final Market Size →
  Review as Market Researcher) and `crudeOilUsageBasedQualityFlags:
  QualityFlag[]` — **every fact in this file is real**, read directly from
  `output/demand_usage/crude-oil/00_setup_approved.json`,
  `01_taxonomy_approved.json`, and `02_sizing.json`. The only
  `data_tag: "illustrative"` content in this file is the literal LLM
  prompt/response text on the `llm_extraction` step (the failure itself,
  its error message, and its downstream numbers are real; the prompt text
  sent to the LLM was never captured for this historical run).
- `crudeOilDemandSide.ts` — exports `crudeOilDemandSideSteps: PipelineStep[]`
  (9 steps mapped onto the real LangGraph node sequence: `structure →
  market_size → geo → shares → asp → reward` (refine loop back to
  `market_size` while `reward < reward_threshold`) `→ finalize → brain →
  final_estimate`). The **step structure and mechanics** are real, read
  from `src/me_engine/generation/graph.py` / `agents.py` / `config.py`. The
  **numbers** (global anchor, CAGR, shares, ASP, reward scores) are
  `data_tag: "illustrative"` because no completed Desk Research run exists
  for Crude Oil yet — every illustrative step says so explicitly.
- `index.ts` — barrel export, plus:
  - `APPROACH_META: Record<Approach, ApproachMeta>` — labels ("Usage-Based
    Demand" / "Desk Research") and real-graph strings shown in the header.
  - `getApproachSteps(approach: Approach): PipelineStep[]` — the single
    function the wizard calls to get step data. **This is the seam to
    replace for real data** (see §6).

### Adding a step to an existing approach

Add a new `PipelineStep` object to the relevant array at the position it
belongs. Nothing else needs to change — `StepRail`/`WizardFooter` render
`steps.length` and index automatically, and `StepPanel` renders whatever
fields are present.

### Adding a new market

1. Create `frontend/src/lib/pipelineDemo/<market>UsageBased.ts` and/or
   `<market>DemandSide.ts`, each exporting a `PipelineStep[]`.
2. Extend `getApproachSteps` (or add a market parameter) in `index.ts`.
3. Add a market switcher to the wizard header if more than one market
   should be selectable (today it's hardcoded to Crude Oil, opened
   directly, per the interaction-model spec).

## 5. Component inventory (`frontend/src/components/demo/`)

| File | Renders |
|---|---|
| `PipelineDemo.tsx` | The wizard shell: header (market/approach/researcher-mode/mode toggle), `StepRail`, the current `StepPanel` (or `QualityFlagsPanel` on the dedicated review step) — or, in Step-by-Step Run mode, a locked/ready/running/failed placeholder instead — `WizardFooter`, and the single `DetailDrawer` instance. Owns `index` (current step), `mode` (`"replay" \| "stepwise"`), and `runStatuses` (per-step `RunStepStatus`, only meaningful in `"stepwise"` mode), and drives the per-step progressive-reveal simulation. |
| `StepRail.tsx` | Horizontally scrollable, numbered step nodes with connecting lines; current step highlighted; every node clickable to jump; optionally marks flagged steps with `!` in Researcher mode. |
| `StepPanel.tsx` | Two-column current-step content: left = input/action/reason/output/provenance/human-decision; right = "View details" buttons, one per present evidence artifact, each opening `DetailDrawer` with the corresponding card. Never grows page height. |
| `DetailDrawer.tsx` | Generic fixed right-side overlay drawer used for every "View details" click — full-height, its own scroll, backdrop click to close. |
| `HumanDecisionCard.tsx` | Explicit "Human Decision — not automated" card with a collapsed-by-default "View full recorded justification" expander — used for the real Crude Oil scope-override text. |
| `QualityFlagsPanel.tsx` | "Review as Market Researcher" — every real `quality_flags` entry verbatim, ⚠/✓/ℹ styled by severity, each clickable to jump the wizard back to the step it concerns. |
| `badges.tsx` | `DemoDataTag`, `ProvenanceBadge`, `StatusDot`, `ResearcherFlag` — unchanged from before. |
| `LlmCallCard.tsx` / `SearchQueryCard.tsx` / `SourceCard.tsx` / `ConversionCard.tsx` / `CalculationCard.tsx` / `ValidationChecklist.tsx` / `WhyNextCard.tsx` / `NumberLineageTrace.tsx` | Unchanged card components, now rendered **inside `DetailDrawer`** (or inline for the short `WhyNextCard`/`ProvenanceBadge`) rather than inline in a long scrolling list. `NumberLineageTrace` is opened from the Final Market Size step's "Trace this number" button. |
| `WizardFooter.tsx` | Fixed Prev/Next buttons + "Step X of N" + progress bar. |
| `StepCard.tsx` / `StepDetailPanel.tsx` / `ReviewAsResearcherMode.tsx` / `FinalEstimateSummary.tsx` / `ApproachComparison.tsx` | Left over from the previous scrolling-list layout. **Not deleted, not imported by the wizard** — kept in case a future iteration wants their content, but they are not part of today's rendered UI. |

## 6. Replacement path — swapping demo data for real pipeline data

The **only** function a future engineer needs to reimplement is
`getApproachSteps` in `frontend/src/lib/pipelineDemo/index.ts` (or a parallel
`getApproachStepsFromRun(runId, approach)`), producing the same
`PipelineStep[]` shape from real `/api/audit/*` data. No wizard component
needs to change, because every component only depends on the `PipelineStep`
shape, not on where it came from. This is unchanged from the previous
version of this document; see the field-by-field mapping table that used to
live here — it still applies:

| `PipelineStep` field | Real source |
|---|---|
| `step_id` / `step_name` | `AuditEvent.stage` (from `stage_start`/`stage_end` events) — one `PipelineStep` per stage. |
| `status` | Derived from `AuditTimeline.timeline[].status` or from whether a `stage_end` event exists yet. |
| `input` / `output` | `AuditEvent.input_data` / `AuditEvent.output_data`. |
| `action` / `reason` | Synthesize from `event_type` + `stage`, or add a `reasoning` field to the emitter. |
| `llm_call` | `AuditEvent` where `event_type === "llm_call"`. |
| `search_queries` / `sources` | `AuditEvent` where `event_type === "search_query"` / `"source_attempt"`. |
| `SourceResult.decision` / `checks` / `rejectionReason` | `AuditEvent` where `event_type === "fact_validation"` — `validation.rejection_code` maps directly. |
| `conversion` / `calculation` | `AuditEvent` where `event_type === "derivation"`. |
| `validation` (ValidationCheck[]) | `AuditEvent` where `event_type === "magnitude_check"`. |
| `evidence_quality` | Compute client-side or server-side from `verification_status` counts. |
| `provenance` | `AuditEvent` payloads' `verification_status` field — already 1:1. |
| `next_step_reason` | Synthesize from the next stage's `stage_start.input_data`, or add explicitly to `stage_end` events. |
| `QualityFlag[]` (new) | `AuditEvent` where `event_type === "taxonomy_generated"` / `"scope_override"`, plus the run's `quality_flags` array already produced by `src/me_engine/demand_usage/quality.py`. |
| `HumanDecision` (new) | `AuditEvent` where `event_type === "scope_override"` — `scope_override.justification`/`scope_override.summary` map directly. |
| Step-by-Step Run mode | Poll `AuditRunDetail.progress`/`lock` (from `progress.py`'s `02_sizing.progress.json`) to drive real per-step `RunStepStatus` transitions, instead of the current client-side simulated progressive reveal — this is the concrete follow-up once this interaction model is approved. |

The existing `components/audit/*` files remain the right starting point for
that live aggregation logic (`useAuditEvents.ts` already fetches and shapes
`/api/audit/*` data) — they were not deleted specifically so this swap can
reuse them rather than starting from scratch.

## 7. Real vs. illustrative — what to trust today

**Real, from the actual recorded Crude Oil Usage-Based run**
(`output/demand_usage/crude-oil/{00_setup_approved,01_taxonomy_approved,02_sizing}.json`):

- The scope statement, geography (14 countries), base year, units.
- All 5 usage buckets, their scope-validation flags, and the real
  scope-override justification text (verbatim) — a human decision, not
  automated.
- The Transportation Fuels / United States 5-attempt retrieval chain
  (4 real rejection codes, then an `llm_claim` fallback) and the Asphalt /
  Canada NAPA `wrong_metric_family:production_vs_capacity` rejection.
- The Transportation Fuels pricing `llm_call_failure` (a genuine failed
  LLM call, not a modeling assumption) and its silent $1/unit floor.
- The United States / Asphalt & Road Construction build-up: 432,000,000
  metric tons (NAPA, 2021) × $450/ton (assumption, uncited) =
  $194,400.0M — the "Trace this number" spine.
- The final total ($453,577.7M) and all 12 real `quality_flags` entries,
  quoted verbatim in the Review as Market Researcher step.

**Illustrative, mocked for this prototype** (each one says so on its own
card/step, not as a blanket disclaimer):

- Every number in `crudeOilDemandSide.ts` (global anchor, CAGR, regional/
  country shares, ASP, reward scores) — no completed Desk Research run
  exists for Crude Oil.
- The literal LLM prompt/response text on the Usage-Based
  `llm_extraction` step — the failure and its error message are real; the
  prompt text sent was not captured historically.
- Step-by-Step Run mode's progressive-reveal phase lines and timing (it
  simulates running the same real/illustrative dataset above one step at a
  time; it does not run a real pipeline).

**Real and already working, unaffected by this change**:

- The underlying `/api/audit/*` backend routes and `src/me_engine/audit/`
  instrumentation.
- `src/me_engine/demand_usage/fact_validator.py`'s deterministic 5-check
  validation order and rejection-code vocabulary.
- `src/me_engine/demand_usage/derivation.py` / `quality.py` / `progress.py`.
- The two functional tabs, "Desk Research" and "Demand-Side (Usage-Based)",
  in `frontend/src/app/page.tsx` — completely untouched by this work.
- `frontend/src/components/audit/*` — still present, still functional
  against real `/api/audit/*` data, still not rendered anywhere by default.
