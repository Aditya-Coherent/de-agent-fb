"""Supply-side market-size estimator.

Independent second methodology, next to `me_engine.generation` (the demand
side): given a list of known companies/distributors in a market, estimate
each one's market-specific revenue (LLM + web evidence, since real market
share is rarely public), gross up for the unlisted long tail, and produce one
total market-value figure comparable to the demand-side pipeline's output.
Used by `compare_demand_supply.py` for a rough (MRE-level) triangulation
check between the two independent estimates.
"""
