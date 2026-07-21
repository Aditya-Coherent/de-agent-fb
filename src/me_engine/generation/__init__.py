"""LangGraph multi-agent synthetic Input-Sheet generator.

This package builds a market Input Sheet from *evidence only* — the market name,
the Neon report DB (structure + summary), and live web search — without ever
reading numeric values from a human-authored input sheet. Every number is decided
by an agent and justified by a "brain" agent that attaches artifacts.

See generate_synthetic_input.py (repo root) for the entrypoint.
"""
