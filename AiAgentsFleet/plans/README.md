# CMI whitepaper accuracy plans

Goal: every number in a generated CMI whitepaper is defensible and every sentence
is justifiable, so a report can go to a client without hand-editing.

Background: on 2026-10-15 all 5 reports in `AiAgentsFleet/output/<slug>/` were
reviewed and all 5 were rated "needs major fixes". The concrete defects, with
quotes, are in [FINDINGS-2026-10-15.md](FINDINGS-2026-10-15.md). Each plan below
fixes one class of defect **in the pipeline** (not in the documents), so the fix
applies to every future market.

## Single source of numbers

All numbers come from one source: the AiAgentsFleet desk research. There is no
blending, averaging or cross-checking with other approaches (uc runs, demand-side
models). Accuracy comes from:
- no random jitter;
- no junk sources;
- every number checked against the evidence it came from;
- the agent re-asked when an answer is incoherent;
- unconfirmed facts left out.

## How to use these plans

Give each plan file to a separate Claude Code terminal, for example:

> Implement `AiAgentsFleet/plans/PLAN-01-inputs-no-jitter-source-quality.md`.
> Read `AiAgentsFleet/plans/README.md` and `FINDINGS-2026-10-15.md` first.

Rules for every implementer:
- Work in your own git worktree or branch (`plan-01`, `plan-02` …). Several plans
  touch the same files (`groups.py`, `model.py`, `build.py`), so separate
  branches avoid clobbering. Merge in the order below.
- Use `.venv313` (the `.venv` is broken).
- Do not hand-edit anything in `AiAgentsFleet/output/`. Fix the code, then regenerate.
- Never tune a number toward a known answer. Reference figures are for scoring
  only, and must never be fed into a run as an input.
- Each plan has an "Acceptance" section. A plan is done only when its acceptance
  checks pass and `python -m pytest AiAgentsFleet/tests` is green.
- Keep `cmi_report/prose.py` `PROSE_VERSION` in mind. Bump it if you change
  prompts or guards, so cached prose is regenerated.

## Plans and merge order

| # | Plan | Main files | Can run in parallel with |
|---|---|---|---|
| 01 | [Inputs: remove jitter, filter junk sources](PLAN-01-inputs-no-jitter-source-quality.md) | `field_agent.py`, `cmi_report/run_data.py`, `randomizer.py`, `run_fleet.py` | 02-08 |
| 02 | [Headline backed by its own desk research](PLAN-02-headline-sourced.md) | `field_groups.py` (`market_size`), `consistency.py` (market size, scenarios), new `cmi_report/verify_size.py` | 01, 03-08 |
| 03 | [Correct country numbers: no rescaling, re-ask on bad splits](PLAN-03-geography-realism.md) | `cmi_report/model.py` (`_country_parts`), `groups.py` (geography/countries), new `cmi_report/geo_check.py` | 01, 02, 04-08 |
| 04 | [Market scope and segment coherence](PLAN-04-scope-and-segments.md) | `market_definition.py`, `groups.py` (segments), `model.py` (segments) | 01-03, 05-08 |
| 05 | [Vendors and competitive landscape](PLAN-05-vendors.md) | `groups.py` (vendors), `model.py` (vendor shares), `build.py` (vendor chapter), `charts.py` | 01-04, 06-08 |
| 06 | [Regulation and fact verification](PLAN-06-regulation-fact-verification.md) | `groups.py` (regulation), new `cmi_report/verify_facts.py`, `build.py` (regulation rows) | 01-05, 07, 08 |
| 07 | [Prose logic: ties, rankings, recommendations, claims](PLAN-07-prose-logic.md) | `prose.py`, `guard.py`, `register.py`, `charts.py` (process) | 01-06, 08 |
| 08 | [Client-ready document and a blocking release gate](PLAN-08-client-ready-and-release-gate.md) | `build.py` (appendix), `run_cmi_report.py` (qa), `factcheck.py`, `audit.py` | 01-07 |
| 10 | [Regenerate the 5 reports and sign off](PLAN-10-regenerate-and-signoff.md) | none (runs the pipeline) | last, after all others are merged |

Suggested merge order: 01 → 02 → 03 → 04 → 05 → 06 → 07 → 08 → 10. (There is no PLAN-09.)
Merge conflicts in `groups.py` / `model.py` / `build.py` should be small, because
each plan edits different functions or dict entries. Resolve them by keeping both
sides.

## What "done" looks like (from PLAN-10)

For each of the 5 markets, the regenerated report:
1. Has no randomised numbers, so the same evidence gives the same report.
2. States a headline that is backed by a quote found in the desk research, or is
   labelled "CMI estimate" with no published-comparison wording.
3. Has no "[to verify]" markers, no internal pipeline log, and no empty headings.
4. Has no vendor shares that aren't sourced, no wrong HQ or parent, and no
   regulator names that aren't confirmed.
5. Passes the blocking release gate (`audit.py --gate`), which includes a
   plausibility review.
6. Fixes every defect listed for that market in FINDINGS-2026-10-15.md, or
   records why it is not a defect.
