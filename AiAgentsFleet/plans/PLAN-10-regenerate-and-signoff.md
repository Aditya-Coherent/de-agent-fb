# PLAN-10: Regenerate the 5 reports and sign off

Run this **last**, after PLAN-01 to PLAN-08 are merged into `main`.

Read first: `plans/README.md`, `plans/FINDINGS-2026-10-15.md`.

## Steps
1. `git pull` and confirm all 8 plans are merged. Run `python -m pytest AiAgentsFleet/tests` (it must be green).
2. Back up the current outputs:
   `AiAgentsFleet/output/<slug>/` → `AiAgentsFleet/output/_archive_2026-10-15/<slug>/`.
   Do not delete them. They are the "before" for comparison.
3. Regenerate the fleet data and reports. The prompts changed, so do not reuse
   the cached `whitepaper_fields.json` or `cmi_fields.json`.
   - Run `run_fleet.py` and `cmi_report/run_data.py` fresh for each market.
   - The LLM cache may stay warm.
   ```
   .venv313/Scripts/python AiAgentsFleet/run_fleet.py --market "<Market Name>" --workers 1
   .venv313/Scripts/python AiAgentsFleet/cmi_report/run_cmi_report.py --market-slug <slug>
   .venv313/Scripts/python AiAgentsFleet/cmi_report/audit.py --market-slug <slug> --gate
   ```
   Slugs:
   - `agricultural-biostimulants-market`
   - `functional-poultry-feed-market`
   - `generative-ai-in-agriculture-market`
   - `global-vitamin-c-market`
   - `super-intensive-shrimp-farming-market`

   Use `--workers 1` for live runs (ddgs throttles).
4. For each market, go through its section of `FINDINGS-2026-10-15.md` item by
   item. Write `plans/SIGNOFF-<slug>.md` with a table:
   finding ID | status (fixed / not applicable any more / still present) | evidence (quote or file:line).
   Also list the X1–X18 cross-cutting items.
5. Run an independent fresh review: a new subagent with no access to FINDINGS,
   prompted like the 2026-10-15 review. Add its new findings to the sign-off
   file. New "high" items mean the report is not ready. Record which plan the
   fix belongs to.
6. Write `plans/SIGNOFF-SUMMARY.md`: per market, show the gate result, the
   headline before → after, whether the headline is sourced (and from where), the open issues, and ready / not
   ready.

## Ready-to-ship criteria (all must hold)
- `audit.py --gate` returns `ready: true`.
- No FINDINGS item is "still present" unless it is explained as not a defect.
- The independent review has no "high" items.
- The headline is backed by a cited quote from the desk research, or is
  labelled "CMI estimate" with no published-comparison wording.
- The client .docx has 0 "[to verify]" markers, no A.4–A.6, and no internal
  pipeline text.

## Do not
- Do not hand-edit generated documents to pass the gate. If something fails,
  open an issue against the plan that owns it, fix the code, and regenerate.
