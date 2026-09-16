"""India-only, usage-based demand-side market-sizing methodology.

A parallel sibling of `me_engine.demand_usage` (imported from, never
copy-pasted) that adds one more hierarchy level and locks geography to India
only:

    Market -> Major Use Case -> India -> Actual Use Case -> driver -> quantity
        -> price -> value
        -> Actual Use Case totals roll up to Major Use Case total
        -> Major Use Case totals roll up to India Market Total

See each module's own docstring for the specific reuse points. Nothing here
ever modifies `me_engine.demand_usage`'s own behavior -- this package only
imports from it.
"""
