"""Supply-side market-size estimator.

Independent methodology, next to `me_engine.generation` (Desk Research /
secondary research) and `me_engine.demand_usage` (Demand-Side / bottom-up
usage-based): given a list of known companies/distributors in a market,
estimate each one's market-specific revenue (LLM + web evidence, since real
market share is rarely public), gross up for the unlisted long tail, and
produce one total market-value figure comparable to the other pipelines'
output. Used by `compare_demand_supply.py` for a rough (MRE-level)
triangulation check against Desk Research's total.
"""
