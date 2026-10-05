"""Usage/Application-based bottom-up demand-side market sizing.

Third, independent methodology alongside `me_engine.generation` (Desk
Research -- cites or estimates from already-published report/web material)
and `me_engine.supply` (bottom-up from named companies' revenue). This
package instead constructs the market size FROM UNDERLYING DEMAND:

    Market -> Geography -> Usage/Application -> Demand Driver -> Quantity
    -> Price -> Usage-level Value -> Country Total -> Global Total

Every number carries a provenance tag (observed / derived / assumption) so
the final artifact is auditable line-by-line, not just a total that happens
to match a published figure. The existing Desk Research total is used only
as an informational reference/benchmark during validation -- it is never
used to override or backfill this pipeline's own bottom-up total.

Own state, own nodes, own output artifact -- no cross-import of `generation`
or `supply` business logic. Reuses only generic infrastructure: `GenLLM`,
`GenConfig`, the `ddgs`-based web evidence utility, the local sentence
embedding utility, and the history log.

See demand_usage_pipeline.py (repo root) for the entrypoint.
"""
