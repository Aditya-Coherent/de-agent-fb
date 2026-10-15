"""The canonical list of field GROUPS this fleet fills -- one agent runs per
group. Derived directly from reading every section and every embedded chart
of `vitamin_c_market_whitepaper_v2.docx` (the template whitepaper).

Each group is a plain dict:
    key           -- short id, used as the output JSON's top-level key
    section       -- which whitepaper section this feeds
    queries       -- search-query templates; "{market}" is substituted
    system        -- system prompt for the synthesis LLM call
    schema_hint   -- the exact JSON shape the LLM must return (as a string,
                     embedded verbatim in the user prompt)

No numbers live here -- this module is pure structure/prompts.
"""
from __future__ import annotations

FIELD_GROUPS: list[dict] = [
    {
        "key": "market_size",
        "section": "Sec 2 - Global Market Size Outlook",
        "queries": [
            "{market} market size 2025 USD billion",
            "{market} market size forecast 2035 CAGR",
            "{market} market size historical 2020 2021 2022 2023 2024",
            "{market} market size 2024",
            "{market} market size 2025",
            "{market} market worth billion",
            "{market} market valued at",
            "{market} Coherent Market Insights",
        ],
        "system": (
            "You summarize web search results the way Google's AI Overview does: a concise, "
            "confident, well-cited answer synthesized from multiple sources. Extract market-sizing "
            "numbers for the requested market.\n"
            "CITE YOUR HEADLINE: base_year_value_usd_mn and cagr_pct must be taken from, or directly "
            "derived from, the quotes you list in `basis`. Each basis.quote is copied EXACTLY from one "
            "WEB EVIDENCE line and states the value and its year. Derived means only: a 2024 or 2026 "
            "value carried to the base year with a CAGR a quote states, or a conversion to USD million. "
            "If several quotes disagree, use the one whose scope best matches the market definition and "
            "say why in `how_derived`. If a WEB EVIDENCE line from Coherent Market Insights "
            "(coherentmarketinsights.com) states a size for this market, use it as the headline basis. "
            "If no evidence line states a size for this market, return "
            "\"basis\": [] and give your own best estimate.\n"
            "For the other fields, if the evidence doesn't cover them, give your own best estimate using "
            "domain knowledge and reasonable extrapolation -- never leave a field null."
        ),
        "schema_hint": (
            '{"basis": [{"quote": <exact evidence text>, "publisher": <firm or site the quote is from>, '
            '"value_usd_mn": <number>, "year": <int>, "cagr_pct": <number|null>}, ...], '
            '"how_derived": <1 sentence: how the headline follows from basis>, '
            '"base_year": <int, e.g. 2025>, "base_year_value_usd_mn": <number>, '
            '"forecast_year": <int, e.g. 2035>, "forecast_value_usd_mn": <number>, '
            '"cagr_pct": <number>, '
            '"historical_usd_mn": {"<year>": <number>, ... 5 recent past years}, '
            '"forecast_series_usd_mn": {"<year>": <number>, ... every year from base_year to forecast_year}}'
        ),
    },
    {
        "key": "drivers_restraints",
        "section": "Sec 3 - Market Dynamics (Drivers & Restraints)",
        "queries": [
            "{market} market growth drivers",
            "{market} market restraints challenges",
        ],
        "system": "You summarize web search results into a structured drivers/restraints breakdown, Google AI-Overview style.",
        "schema_hint": (
            '{"drivers": [{"name": <string>, "cagr_contribution_bps": <number>}, ... 5-8 items], '
            '"restraints": [{"name": <string>, "cagr_drag_bps": <number>}, ... 4-7 items]}'
        ),
    },
    {
        "key": "porters_five_forces",
        "section": "Sec 4 - Porter's Five Forces",
        "queries": [
            "{market} industry attractiveness Porter's five forces analysis",
            "{market} supplier power buyer power competitive rivalry",
        ],
        "system": "You summarize web search results into a Porter's Five Forces scorecard, Google AI-Overview style.",
        "schema_hint": (
            '{"overall_score_out_of_5": <number>, "forces": {'
            '"supplier_power": {"score_1_5": <number>, "label": "<Low|Medium|High>"}, '
            '"buyer_power": {"score_1_5": <number>, "label": "<Low|Medium|High>"}, '
            '"competitive_rivalry": {"score_1_5": <number>, "label": "<Low|Medium|High>"}, '
            '"threat_of_new_entrants": {"score_1_5": <number>, "label": "<Low|Medium|High>"}, '
            '"threat_of_substitutes": {"score_1_5": <number>, "label": "<Low|Medium|High>"}}}'
        ),
    },
    {
        "key": "segmentation_grade_form",
        "section": "Sec 5 - Segmentation by Grade/Type and Product Form",
        "queries": [
            "{market} market segmentation by grade type share",
            "{market} market segmentation by product form share",
        ],
        "system": "You summarize web search results into base-year segmentation shares, Google AI-Overview style.",
        "schema_hint": (
            '{"by_grade_or_type": [{"name": <string>, "share_pct_of_market": <number>, '
            '"value_usd_mn": <number>}, ... 4-6 items summing to ~100%], '
            '"by_form": [{"name": <string>, "share_pct_of_market": <number>, '
            '"fastest_growing_cagr_pct": <number|null>}, ... 4-6 items summing to ~100%]}'
        ),
    },
    {
        "key": "end_use_industry",
        "section": "Sec 6 - End-Use Industry Analysis",
        "queries": [
            "{market} end use application share by industry",
            "{market} demand by application CAGR 2025 2030",
        ],
        "system": "You summarize web search results into end-use application shares and growth, Google AI-Overview style.",
        "schema_hint": (
            '{"applications": [{"name": <string>, "share_pct_of_market": <number>, '
            '"cagr_2025_2030_pct": <number>}, ... 4-7 items summing to ~100% share]}'
        ),
    },
    {
        "key": "sub_segments",
        "section": "Sec 7 - High-Growth Sub-Segment Opportunities",
        "queries": [
            "{market} fastest growing sub-segments by application",
            "{market} niche high growth product categories",
        ],
        "system": "You summarize web search results into high-growth sub-segments within the top applications, Google AI-Overview style.",
        "schema_hint": (
            '{"by_top_application": [{"application": <string>, "sub_segments": '
            '[{"name": <string, a real product/niche name -- never a search-result title, never '
            'containing the word "Market", "report", "projection", or a bracketed year>, '
            '"value_usd_mn": <number|null>, "cagr_pct": <number>}, ...4-6 items]}, '
            '... top 2-3 applications]}'
        ),
    },
    {
        "key": "derivatives_pricing",
        "section": "Sec 8 - Derivatives and Pricing Structure",
        "queries": [
            "{market} product derivatives market value breakdown",
            "{market} pricing per metric ton by grade tier",
        ],
        "system": "You summarize web search results into derivative product values and a price-tier table, Google AI-Overview style.",
        "schema_hint": (
            '{"derivatives": [{"name": <string, a real product/derivative name -- never a search-result '
            'title, never containing the word "Market" or a bracketed year}, "value_usd_mn": <number>}, '
            '... 4-8 items], '
            '"price_tiers": [{"tier": <string>, "price_low_usd_per_unit": <number>, '
            '"price_high_usd_per_unit": <number>, "unit": <string, the EXPLICIT pricing unit, e.g. '
            '"USD/kg", "USD/tonne", "USD/licence/yr" -- never omit this>, "grade": <string>, '
            '"principal_buyers": <string>}, ... 4-6 rows, cheapest tier first]}'
        ),
    },
    {
        "key": "regional",
        "section": "Sec 9 - Regional Market Dynamics and Outlook",
        "queries": [
            "{market} market share by region North America Europe Asia Pacific",
            "{market} regional market size and CAGR 2025 2030",
            "{market} country level market size breakdown",
        ],
        "system": (
            "You summarize web search results into a regional market breakdown, Google AI-Overview "
            "style. Regions MUST be exactly these five: North America, Europe, Asia Pacific, "
            "Latin America, Middle East & Africa -- do not use any other region names. Region "
            "convention: North America is the United States and Canada ONLY; Mexico is Latin America; "
            "Turkey and Israel are Middle East & Africa (Middle East); Egypt and North Africa are Middle "
            "East & Africa (Africa); Russia is Europe. Count and list each country only in its own region."
        ),
        "schema_hint": (
            '{"regions": {"North America": {"value_usd_mn": <number>, "share_pct_of_global": <number>, '
            '"cagr_2025_2030_pct": <number>, "forecast_value_usd_mn": <number>, '
            '"countries": [{"name": <string>, "value_usd_mn": <number>}, ... 3-7 countries], '
            '"application_mix_pct": [{"name": <string>, "share_pct": <number>}, ... 4-6 items]}, '
            '"Europe": {...same shape...}, "Asia Pacific": {...same shape...}, '
            '"Latin America": {...same shape...}, "Middle East & Africa": {...same shape...}}}'
        ),
    },
    {
        "key": "competitive_landscape",
        "section": "Sec 10 - Competitive Landscape and Market Share",
        "queries": [
            "{market} top companies market share",
            "{market} leading manufacturers competitive landscape",
            "{market} largest producers share percent",
        ],
        "system": (
            "You summarize web search results into the market's leading companies, Google AI-Overview style. "
            "List the top producers or vendors that EARN THE REVENUE the MARKET DEFINITION measures, at its "
            "value-chain level -- never suppliers of inputs, buyers, or companies whose business is another level "
            "of the value chain. If one country's producers dominate the market's supply (e.g. Chinese producers "
            "for vitamin C), the list must reflect that. share_pct is your estimate and only orders the list; it "
            "is never published. A share counts as SOURCED only when one WEB EVIDENCE line states that company's "
            "share of THIS market (or its revenue in it): copy that line exactly into share_quote and use its "
            "figure as share_pct (or revenue_usd_mn). Otherwise share_quote is \"\". Never invent a quote."
        ),
        "schema_hint": (
            '{"companies": [{"name": <string>, "share_pct": <number>, '
            '"share_quote": <the WEB EVIDENCE line stating this company\'s share of this market, copied exactly, or "">, '
            '"revenue_usd_mn": <number|null, only when share_quote states its revenue in this market>, '
            '"positioning_note": <string>}, '
            '... 8-12 named real companies, ranked descending by share], '
            '"others_share_pct": <number>}'
        ),
    },
    {
        "key": "regulation",
        "section": "Sec 11 - Regulation and Compliance Overview",
        "queries": [
            "{market} regulatory compliance requirements cost",
            "{market} certification standards GMP quality regulations",
        ],
        "system": "You summarize web search results into a regulatory compliance cost breakdown, Google AI-Overview style.",
        "schema_hint": (
            '{"compliance_areas": [{"name": <string>, "cost_pct_of_cogs_low": <number>, '
            '"cost_pct_of_cogs_high": <number>}, ... 6-10 items]}'
        ),
    },
    {
        "key": "willingness_to_pay",
        "section": "Sec 12 - End-User Behavior Outlook",
        "queries": [
            "{market} price per unit customers pay by segment",
            "{market} premium pricing willingness to pay buyers",
            "{market} cost per unit pricing small vs large buyers",
        ],
        "system": (
            "You summarize web search results into a willingness-to-pay matrix, Google AI-Overview style. "
            "Buyers may be businesses (farms, integrators, enterprises), not only consumers -- use the price "
            "unit that fits this market (e.g. USD/kg, USD per hectare per year, USD per user per month). "
            "'Proven performance' means clinical, field-trial or ROI evidence. Every price must be a positive "
            "estimate in that unit; never return 0. Within a segment, adding an attribute must not lower the price."
        ),
        "schema_hint": (
            '{"unit": <string, the single price unit used for every value>, '
            '"consumer_segments": [{"name": <string>, "baseline_usd": <number>, '
            '"with_clinical_evidence_usd": <number, with proven performance evidence>, '
            '"with_premium_positioning_usd": <number>, '
            '"with_top_tier_format_usd": <number, top-tier product/service format>}, ... 4-5 buyer segments, '
            'most price-sensitive first, least price-sensitive last]}'
        ),
    },
    {
        "key": "investment_priorities",
        "section": "Sec 13 - Strategic Investment Priorities",
        "queries": [
            "{market} investment opportunities strategic priorities",
            "{market} ROI growth opportunities by segment",
        ],
        "system": "You summarize web search results into a go-to-market/investment opportunity matrix, Google AI-Overview style.",
        "schema_hint": (
            '{"opportunities": [{"opportunity": <string>, "target_segment": <string>, '
            '"time_to_market": <string, e.g. "12-18 mo">, "roi_low_pct": <number>, '
            '"roi_high_pct": <number>, "capital_intensity": "<Low|Low-Med|Medium|Med-High|High>"}, '
            '... 5-8 rows]}'
        ),
    },
    {
        "key": "scenarios",
        "section": "Sec 14 - Scenario Analysis (Bear/Base/Bull)",
        "queries": [
            "{market} market forecast scenarios optimistic pessimistic",
            "{market} long term growth outlook 2035",
        ],
        "system": (
            "You summarize web search results into three market-size scenarios, Google AI-Overview "
            "style. Base case should be consistent with the base-case forecast a typical market "
            "report would give; bear/bull should bracket it plausibly."
        ),
        "schema_hint": (
            '{"bear": {"terminal_value_usd_mn": <number>, "cagr_pct": <number>}, '
            '"base": {"terminal_value_usd_mn": <number>, "cagr_pct": <number>}, '
            '"bull": {"terminal_value_usd_mn": <number>, "cagr_pct": <number>}}'
        ),
    },
]

FIELD_GROUP_KEYS = [g["key"] for g in FIELD_GROUPS]
