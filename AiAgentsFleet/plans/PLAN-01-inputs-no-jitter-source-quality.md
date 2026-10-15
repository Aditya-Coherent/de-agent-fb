# PLAN-01: Inputs. Remove the random jitter and filter junk sources

Read first: `plans/README.md`, `plans/FINDINGS-2026-10-15.md` (X1, X2, X5; F10).

## Problem
1. **Jitter.** `randomizer.randomize_json` multiplies every numeric leaf by
   1 ± U(5%, 10%). It runs in two places:
   - `field_agent.run_field_group` for all 13 fleet groups, including `market_size`.
   - `cmi_report/run_data.run_group` for groups with `"randomize": True`, a
     tuple, or `_PARTIAL_RANDOMIZE`.

   Because the base value and the CAGR are both jittered, the 2033 value drifts
   up to ~12%. Rankings and ties then come from noise (a 0.1 pp "fastest"),
   and the historical series becomes non-monotonic (Vitamin C 2023 > 2024).
2. **Junk sources.** Evidence comes from domains such as yandex, vkvideo,
   youtube, linkedin, hepsiburada, astrohelper.ru and `*.so` link farms. Their
   snippets were used to size markets.

## Changes

### 1. Jitter off by default
- In `randomizer.py`, add a module-level switch `ENABLED = False`, read from env
  `AIFLEET_RANDOMIZE=1`. `randomize_json` returns a deep copy unchanged when
  the switch is off.
- Keep the function so the old behaviour can be turned on explicitly, but the
  CMI report path must **never** enable it:
  - In `cmi_report/run_cmi_report.py` and `cmi_report/run_batch.py`, force it
    off. Raise if the env var is set and `--allow-randomize` was not passed.
- Update the log strings that say "then randomized every value by +/-5-10%":
  - `field_agent.py:79-81`
  - `run_fleet.py:86`, the metadata `"randomization"` key, which must now say
    `"none"` when off.
- In `cmi_report/run_data.py`, keep the `randomize` keys in `groups.py` (other
  plans edit that file) but route them through the same switch.
- README: rewrite the "Randomization rule" section to say it is off for reports,
  and why.

### 2. Keep the history honest
- `consistency._reconcile_market_size` rescales history so it "leads smoothly"
  into the base.
  - Also enforce a non-decreasing history; if not, log a warning and keep the raw values.
  - Flag any year-on-year jump > 2× the CAGR as a validation warning.
  - Do not print history in the report unless it passes.
- Check whether any chart or table prints 2020-2024 values (`charts.py`,
  `build.py`). If one does, gate it on the history passing.

### 3. Source-quality filter (shared by both search paths)
- Create `AiAgentsFleet/sources.py` with:
  - `BLOCKED_DOMAINS`: a set of video, social, marketplace, Q&A, link-farm and
    horoscope sites. At minimum: youtube.com, vkvideo.ru, vk.com, yandex.*,
    linkedin.com, facebook.com, instagram.com, x.com, twitter.com, tiktok.com,
    pinterest.*, reddit.com, quora.com, hepsiburada.com, amazon.*, ebay.*,
    aliexpress.*, astrohelper.ru, scribd.com.
  - A pattern for throwaway TLD link farms (`*.so`, `*.tk`, `*.xyz` unless allow-listed).
  - `TRUSTED_HINTS`: government (.gov, .gov.*, europa.eu, fao.org, who.int,
    worldbank.org, oecd.org, wto.org, un.org), company IR and press-release
    sites, trade associations, recognised research publishers (Precedence,
    Grand View, MarketsandMarkets, Mordor, Fortune Business Insights, IMARC,
    Allied, Verified, Market.us, Statista), and news outlets (Reuters,
    Bloomberg, FT, trade press).
  - `filter_sources(sourced) -> (kept, dropped)`: drops blocked domains and
    returns the reasons.
  - `quality(sourced) -> {"n": .., "distinct_domains": .., "trusted": .., "score": 0-1}`.
- Call `filter_sources` in `field_agent.run_field_group` (after
  `gather_with_sources`) and in `cmi_report/run_data.search` (before the
  on-topic filter).
- Store the quality block in each group's `_sources` as
  `{"num_sources", "domains", "dropped_domains", "quality"}`.
- If a numeric group (`market_size`, `regional`, `countries_*`, `segments_*`,
  `competitive_landscape`, `vendors`) ends up with < 3 kept snippets or 0
  trusted domains, set `_sources.low_evidence = true`. PLAN-02 and PLAN-08 use
  this flag.

## Acceptance
- New tests in `AiAgentsFleet/tests/`:
  - `test_randomizer.py`: off by default, so the output equals the input. With
    `AIFLEET_RANDOMIZE=1`, values change.
  - `test_sources.py`: blocked domains are dropped, trusted ones are counted,
    and `*.so` is blocked.
  - A test that `run_cmi_report` refuses to run with randomization on unless
    `--allow-randomize` is passed.
- Re-running `run_fleet.py` twice on the same market gives byte-identical
  `fields` (ignoring timestamps) with fixed search results (offline test). Live
  search varies from run to run, and we accept that.
- For any market, `whitepaper_fields.json` → `fields.market_size` equals
  `fields_raw.market_size` apart from reconciliation changes listed in
  `consistency_changes`.

## Do not
- Do not edit `consistency.py` beyond `_reconcile_market_size`; PLAN-02 owns
  the headline logic.
- Do not delete `randomizer.py`; other scripts may import it.
