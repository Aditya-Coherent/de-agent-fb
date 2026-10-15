"""AiAgentsFleet -- a small, standalone agent fleet that fills every
numeric field the whitepaper template (`vitamin_c_market_whitepaper_v2.docx`)
requires, for ANY market name, by querying live web search and having an LLM
synthesize a concise "AI-Overview-style" structured answer per field group.
Snippets from blocked sources are dropped first (sources.py). Numbers are kept
as the agent returned them; the old +/-5-10% jitter (randomizer.py) is off
by default and never used for CMI reports.

See README.md in this folder for usage, the exact field list, and an
important caveat about what "Google AI Overview" means here.
"""
