"""Demand-Side Approach 2C: product type x application x country -> global.

NOT IMPLEMENTED YET (placeholder, deliberately left out of the current scope).

Planned hierarchy:

    Country -> Application -> Product Type -> quantity x price -> value
        Product Types roll up to each Application (per country)
        Applications roll up to the Country total
        Countries roll up to Region -> Global
        Remaining segments (e.g. Distribution Channel) -> top-down split

How it would be built (mostly by reuse):
- Phases 1-2: 2B's setup + usage taxonomy (`demandUseCaseCountry_approach2B`),
  plus one new LLM node that lists the product types used inside each
  application (the same pattern as the India variant's
  `actual_use_case_agents.py`, which adds one level below a use case).
- Phase 3: 2B's sizing graph, run over (application, product type) leaves
  instead of applications -- it already sizes "a list of buckets x a list of
  countries" -- then a pure-arithmetic rollup, like
  `demand_usage_india/aggregation.py`.
- Phases 4-5: `me_engine.demand_topdown` with BOTH Application and Product
  Type passed as bottom-up dimensions.

Main cost: the number of sized leaves grows to applications x product types x
countries, so evidence calls grow by roughly the product-type count.
"""
