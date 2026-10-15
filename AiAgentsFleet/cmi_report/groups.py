"""Extra field groups the CMI whitepaper template needs on top of the
fleet's whitepaper_fields.json: country splits for every region, the Middle
East / Africa split, type and demand-segment mixes per geography, MEA
dynamics, use-case heat maps, regulation, vendors, strategy and published
third-party estimates.

Same shape as ../field_groups.py: one agent per group (web search -> LLM ->
optional +/-5-10% randomization). `randomize` says whether the group's
numbers are estimates to jitter (shares, CAGRs) or must stay as returned
(1-5 scores, dates, published third-party figures).

`{market}` is the market name; `{context}` is a JSON block of numbers the
fleet already has for this market (passed so the agents stay consistent
with them). No numbers live here.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# Geography. The template's regions and table sizes are fixed; WHICH countries
# are named in each region is chosen per market by the `geography` agent (by
# real relevance: production, consumption, investment, project pipeline).
# "Rest of ..." rows are asked for explicitly: the country agents size them
# from their members (MEMBERS minus the named countries, see rest_members).
# --------------------------------------------------------------------------- #
SLOTS = {"North America": 2, "Europe": 5, "Asia Pacific": 5, "Latin America": 5, "Middle East": 4, "Africa": 5}
DEFAULT_COUNTRIES = {
    "North America": ["United States", "Canada"],
    "Europe": ["Germany", "United Kingdom", "France", "Italy", "Spain"],
    "Asia Pacific": ["China", "Japan", "India", "South Korea", "Australia"],
    "Latin America": ["Brazil", "Mexico", "Argentina", "Chile", "Colombia"],
    "Middle East": ["Saudi Arabia", "UAE", "Turkey", "Israel"],
    "Africa": ["South Africa", "Egypt", "Nigeria", "Morocco", "Kenya"],
}
# Region membership (the report's convention: Turkey and Israel are Middle East,
# Egypt and North Africa are Africa, Russia is Europe, Mexico is Latin America).
MEMBERS = {
    "North America": ["United States", "Canada"],
    "Europe": ["Germany", "United Kingdom", "France", "Italy", "Spain", "Russia", "Netherlands", "Poland", "Belgium",
               "Sweden", "Norway", "Denmark", "Finland", "Ireland", "Portugal", "Greece", "Austria", "Switzerland",
               "Czech Republic", "Romania", "Hungary", "Ukraine", "Bulgaria", "Croatia", "Serbia", "Slovakia",
               "Iceland", "Belarus", "Lithuania", "Latvia", "Estonia", "Slovenia", "Luxembourg"],
    "Asia Pacific": ["China", "Japan", "India", "South Korea", "Australia", "Indonesia", "Vietnam", "Thailand",
                     "Malaysia", "Philippines", "Bangladesh", "Pakistan", "Taiwan", "Singapore", "New Zealand",
                     "Sri Lanka", "Myanmar", "Cambodia", "Nepal", "Kazakhstan", "Uzbekistan", "Hong Kong"],
    "Latin America": ["Brazil", "Mexico", "Argentina", "Chile", "Colombia", "Peru", "Ecuador", "Venezuela",
                      "Guatemala", "Honduras", "Costa Rica", "Panama", "Uruguay", "Paraguay", "Bolivia",
                      "Dominican Republic", "Cuba", "Nicaragua", "El Salvador"],
    "Middle East": ["Saudi Arabia", "UAE", "Qatar", "Kuwait", "Oman", "Bahrain", "Turkey", "Israel", "Iran", "Iraq",
                    "Jordan", "Lebanon", "Syria", "Yemen"],
    "Africa": ["South Africa", "Egypt", "Nigeria", "Algeria", "Morocco", "Kenya", "Ethiopia", "Ghana", "Tunisia",
               "Tanzania", "Uganda", "Cote d'Ivoire", "Senegal", "Cameroon", "Angola", "Zambia", "Zimbabwe",
               "Mozambique", "Madagascar", "Libya", "Sudan", "Rwanda", "Mali", "Namibia", "Botswana"],
}
_ALIASES = {"united arab emirates": "UAE", "usa": "United States", "us": "United States", "uk": "United Kingdom",
            "korea": "South Korea", "republic of korea": "South Korea", "turkiye": "Turkey", "ivory coast":
            "Cote d'Ivoire", "czechia": "Czech Republic", "ksa": "Saudi Arabia"}
REST = {r: f"Rest of {r}" for r in SLOTS if r != "North America"}


def canonical(name: str) -> str:
    n = (name or "").strip()
    return _ALIASES.get(n.lower(), n)


def region_of(country: str) -> str | None:
    """The report-convention region a country belongs to (None if unknown)."""
    c = canonical(country).lower()
    return next((r for r, ms in MEMBERS.items() if c in {m.lower() for m in ms}), None)


def rest_members(geo: dict[str, list[str]]) -> dict[str, list[str]]:
    """{region: the countries its 'Rest of' row covers} -- MEMBERS minus the
    countries named for that region. Built from data, never from LLM text."""
    return {r: [c for c in MEMBERS[r] if c not in (geo.get(r) or [])] for r in REST}


def misplaced(raw: dict | None) -> list[str]:
    """Picks the geography agent put in the wrong region (the report's
    convention: Mexico is Latin America, Turkey/Israel Middle East, Egypt Africa)."""
    out = []
    for region in SLOTS:
        for item in (raw or {}).get(region) or []:
            c = canonical(item.get("country") if isinstance(item, dict) else item)
            home = region_of(c)
            if home and home != region:
                out.append(f"{c} is {home}, not {region} (report convention)")
    return out


def validate_geography(raw: dict | None) -> tuple[dict[str, list[str]], dict[str, str], list[str]]:
    """(countries per region, why each was chosen, notes). Each pick must belong
    to its region; missing or invalid slots are filled from the defaults."""
    raw = raw or {}
    out, why, notes = {}, {}, []
    for region, n in SLOTS.items():
        members = {c.lower(): c for c in MEMBERS[region]}
        picked = []
        for item in raw.get(region) or []:
            c = canonical(item.get("country") if isinstance(item, dict) else item)
            if c.lower() in members and members[c.lower()] not in picked:
                picked.append(members[c.lower()])
                why[members[c.lower()]] = (item.get("why") if isinstance(item, dict) else "") or ""
            elif c:
                home = region_of(c)
                notes.append(f"{c} is {home}, not {region} (report convention) -- not used there" if home
                             else f"{c} is not in {region} -- not used there")
        for d in DEFAULT_COUNTRIES[region]:
            if len(picked) >= n:
                break
            if d not in picked:
                picked.append(d)
                if region != "North America":           # fixed by the template, not chosen
                    notes.append(f"{region}: '{d}' filled in from defaults")
        out[region] = picked[:n]
    return out, why, notes


def mea_units(geo: dict) -> list[str]:
    return geo["Middle East"] + [REST["Middle East"]] + geo["Africa"] + [REST["Africa"]]


def short(c: str) -> str:
    """Heat-map column labels."""
    return {"Saudi Arabia": "KSA", "South Africa": "S. Africa", "United Kingdom": "UK"}.get(c, c)


_ANALYST = (
    "You are a senior market analyst at Coherent Market Insights. You turn web search results into "
    "structured estimates for a market whitepaper. Where the evidence is silent, give your best "
    "domain-informed estimate -- never leave a field empty. Keep every estimate consistent with the "
    "CONTEXT numbers already published for this market and with its DEFINITION. Write in UK English. Return "
    "ONLY valid JSON."
)


def _country_block(regions: list[str], geo: dict) -> str:
    """'"Europe": {"Germany": {...}, ..., "Rest of Europe": {...}}, ...' for the
    schema hint. 'Rest of' names its members so the agent sizes them knowingly."""
    cell = '{"share_pct_of_region": <number>, "cagr_2026_2033_pct": <number>}'
    rest = rest_members(geo)

    def rest_cell(r: str) -> str:
        return (f', "{REST[r]}": {{"share_pct_of_region": <number>, "cagr_2026_2033_pct": <number>, '
                f'"members_note": <1 sentence on its largest members; it covers: {", ".join(rest[r])}>}}')
    return ", ".join(f'"{r}": {{' + ", ".join(f'"{c}": {cell}' for c in geo[r])
                     + (rest_cell(r) if r in REST else "") + "}" for r in regions)


def schema_for(g: dict, geo: dict) -> str:
    """A group's schema with this market's countries filled in."""
    me_cols = ", ".join(f'"{short(c)}": "High|Medium|Low"' for c in geo["Middle East"]) + ', "Rest of ME": "High|Medium|Low"'
    af_cols = ", ".join(f'"{short(c)}": "High|Medium|Low"' for c in geo["Africa"])
    reg_countries = ", ".join(geo["Middle East"] + geo["Africa"])
    return (g["schema"]
            .replace("{CB_MAIN}", _country_block(["North America", "Europe", "Asia Pacific", "Latin America"], geo))
            .replace("{CB_MEA}", _country_block(["Middle East", "Africa"], geo))
            .replace("{MEA_UNITS}", ", ".join(mea_units(geo)))
            .replace("{ME_COLS}", me_cols).replace("{AF_COLS}", af_cols)
            .replace("{REG_COUNTRIES}", reg_countries))


GEOGRAPHY_GROUP = {
    "key": "geography",
    "randomize": False,
    "queries": ["{market} largest countries by production consumption", "{market} market by country leading countries",
                "{market} Middle East Africa leading countries", "{market} Asia Pacific Latin America top countries"],
    "system": _ANALYST + (
        " Choose which countries to name individually in each region of the report, by their real relevance to "
        "THIS market as defined (production, consumption, investment, known project pipeline). Any country with a "
        "major share of real-world activity in this market MUST be named -- never leave a dominant country inside "
        "'Rest of'. Use the report's region convention: Turkey and Israel are Middle East; Egypt and North Africa "
        "are Africa; Russia is Europe; Mexico is Latin America -- a country listed under another region is "
        "not used. Order each region's list by relevance. For each, give one sentence on why it matters for "
        "this market."),
    "schema": ('{"Europe": [{"country": <string>, "why": <1 sentence>}, x5], "Asia Pacific": [... x5], '
               '"Latin America": [... x5], "Middle East": [... x4], "Africa": [... x5]}'),
}


GROUPS: list[dict] = [
    {
        "key": "countries_main",
        "randomize": True,
        "queries": ["{market} market size by country United States China Brazil Germany",
                    "{market} fastest growing countries CAGR"],
        "system": _ANALYST + (
            " For each region give each listed country's share of the REGION's 2026 market value and its "
            "2026-2033 CAGR, and the same for 'Rest of <region>'. 'Rest of <region>' covers EVERY other country "
            "of the region (the shape lists them): size it from those members -- it is a real group of "
            "countries, not a balancing figure. Within each region the shares, INCLUDING 'Rest of', must sum "
            "to 100 (+/-1). North America is the United States and Canada only (Mexico is Latin America): "
            "their two shares sum to 100, and each gets its own CAGR from the evidence."),
        "schema": "{{CB_MAIN}}",
    },
    {
        "key": "countries_mea",
        "randomize": True,
        "queries": ["{market} Middle East market by country", "{market} Africa market by country",
                    "{market} GCC MEA market size CAGR"],
        "system": _ANALYST + (
            " Split the Middle East & Africa (MEA) market into the Middle East and Africa, then give each "
            "listed country's share of ITS sub-region's 2026 value and its 2026-2033 CAGR, and the same for "
            "'Rest of Middle East' and 'Rest of Africa'. Each 'Rest of' covers EVERY other country of its "
            "sub-region (the shape lists them): size it from those members -- it is a real group of countries, "
            "not a balancing figure. Within each sub-region the shares, INCLUDING 'Rest of', must sum to 100 "
            "(+/-1). Turkey and Israel are Middle East; Egypt and North Africa are Africa."),
        "schema": '{"middle_east_share_of_mea_pct": <number>, {CB_MEA}}',
    },
    {
        # Two segment dimensions per market: the supply-side "type" list the
        # fleet already found (CONTEXT.types), and a demand-side dimension
        # (end user / application / crop type / industry ...) chosen here.
        "key": "segments_global",
        "randomize": ("type_cagr_2026_2033_pct", "demand_segments"),
        "queries": ["{market} market segmentation by type share CAGR",
                    "{market} market by end user application share CAGR",
                    "{market} market segmentation analysis"],
        "system": _ANALYST + (
            " Give the global 2026-2033 CAGR for each segment in CONTEXT.types (keep those exact names) and name "
            "that dimension. Then pick the demand-side segmentation that is standard for THIS market (end user, "
            "application, crop type, industry vertical... -- whichever reports on this market use), name it, and "
            "give 4-7 segments with each one's share of the 2026 global market (summing to 100) and 2026-2033 "
            "CAGR. Dimension names are 1-3 words, Title Case, without the market name. The demand segments MUST "
            "obey the DEFINITION and its VALUE-CHAIN LEVEL: for 'farm-gate output' (or 'producer') the demand "
            "segments are buyers or uses of the OUTPUT, and segments that are equipment, technology or systems "
            "sold to producers are FORBIDDEN; for 'vendor/software' they are buyer types or applications of the "
            "software, never the value of what those buyers produce; for 'manufacturer' they are the product's "
            "buyers or uses. Never upstream suppliers (feed, inputs, raw materials), never "
            "sales channels (retail, online) for a non-retail market, never anything the definition EXCLUDES; "
            "use an 'Other' segment only if it is genuinely needed. "
            "Each dimension must be mutually exclusive and collectively exhaustive: define EVERY segment in one "
            "line (segment_definitions), and where the source taxonomy overlaps (a type that is also part of a "
            "broader category) state the allocation rule. "
            "If the value-chain level is 'manufacturer', 'vendor/software' or 'retail', say for every pair of types "
            "(except 'Other ...') whether buyers actually price or specify them differently (grade, form, purity, "
            "application) rather than only by production history or route; two types that are really one product "
            "go in type_merges. For 'farm-gate output', 'producer' or 'service' markets a type dimension by "
            "production system, technology or species is a valid split: keep those types distinct (type_split_check "
            "[]) and merge only types that genuinely overlap. "
            "Then judge, honestly, whether each dimension is a real, commonly reported way to segment THIS market "
            "(false if the segments are artificial, or if the two dimensions describe the same thing)."),
        "schema": ('{"type_dimension": {"singular": <e.g. "Product Type">, "plural": <e.g. "Product Types">}, '
                   '"type_cagr_2026_2033_pct": {"<type>": <number>, ...}, '
                   '"demand_dimension": {"singular": <e.g. "End User">, "plural": <e.g. "End Users">}, '
                   '"demand_segments": [{"name": <string>, "share_pct_2026": <number>, "cagr_2026_2033_pct": <number>}, ...], '
                   '"type_applicable": <true|false>, "demand_applicable": <true|false>, '
                   '"applicability_reason": <1 sentence>, '
                   '"segment_definitions": {"type": {"<each type>": <one-line definition>}, '
                   '"demand": {"<each demand segment>": <one-line definition>}}, '
                   '"allocation_rules": [<one sentence per unavoidable overlap, e.g. "Seaweed-derived products are '
                   'counted under Seaweed Extracts, not Extract-Based Biostimulants.">; [] if the segments are '
                   'already mutually exclusive], '
                   '"type_split_check": [{"a": <type>, "b": <type>, "buyers_differentiate": <true|false>, '
                   '"basis": "grade|form|purity|application|production history|other"}, one per pair of types; [] for farm-gate output, producer and service '
                   'markets], "type_merges": [{"merge": [<type>, <type>], "name": <merged name>, "definition": <one line>}; '
                   '[] if every type is a distinct product]}'),
    },
    {
        "key": "segments_mea",
        "randomize": True,
        "queries": ["{market} Middle East demand by application end user",
                    "{market} Africa demand by application end user",
                    "{market} Middle East Africa market segmentation"],
        "system": _ANALYST + (
            " For EVERY listed MEA country or remainder group, give the 2026 mix of the market by type and by "
            "demand segment (each mix sums to 100; use exactly the names in CONTEXT.types and "
            "CONTEXT.demand_segments) and each segment's 2026-2033 CAGR in that country. Mixes must differ by "
            "country according to the evidence on its economy, crops or industry structure and policy priorities."),
        "schema": ('{"<country or remainder group>": {"type": {"<name>": {"share_pct": <number>, '
                   '"cagr_pct": <number>}, ...}, "demand": {"<name>": {"share_pct": <number>, "cagr_pct": '
                   '<number>}, ...}}, ... one entry for each of: {MEA_UNITS}}'),
    },
    {
        "key": "dynamics",
        "randomize": False,
        "queries": ["{market} trends 2026", "{market} Middle East Africa growth drivers challenges",
                    "{market} Middle East Africa opportunities investment"],
        "system": _ANALYST + (
            " Describe the market's structural dynamics with a focus on the Middle East & Africa (MEA). "
            "Impact numbers are percentage points (pp) of MEA CAGR versus a neutral baseline: drivers "
            "+0.5 to +3.0, restraints -0.3 to -2.0."),
        "schema": ('{"trends": [{"title": <2-4 words>, "tagline": <6-10 words>}, x6], '
                   '"mea_drivers": [{"name": <3-7 words>, "impact_pp": <number>}, x6], '
                   '"mea_restraints": [{"name": <3-7 words>, "impact_pp": <negative number>}, x6], '
                   '"impact_matrix": [{"factor": <string>, "type": "Driver|Restraint", "impact": "High|Medium|Low", '
                   '"timeframe": "Short|Short-Mid|Mid|Mid-Long|Short-Long", "most_exposed": <2-3 MEA countries or groups>}, '
                   'x8, 4 drivers then 4 restraints]}'),
    },
    {
        "key": "use_cases",
        "randomize": False,
        "queries": ["{market} use cases applications Middle East GCC",
                    "{market} applications adoption Africa"],
        "system": _ANALYST + (" Rate how attractive each use case is in each country (High, Medium or Low). List "
                              "only use cases that really exist for this market in that region; return an empty "
                              "list for a region where the market has no distinct use cases. A use case is a buyer "
                              "or use of what the DEFINITION measures, at its value-chain level: never something "
                              "the definition excludes, and for a 'farm-gate output' or 'producer' market never "
                              "the equipment, technology or systems used to produce the output."),
        "schema": ('{"middle_east": [{"use_case": <string>, {ME_COLS}}, x5-7 use cases], '
                   '"africa": [{"use_case": <string>, {AF_COLS}}, x5-6 use cases]}'),
    },
    {
        "key": "regulation",
        "randomize": False,
        "queries": ["{market} regulation Middle East GCC regulator",
                    "{market} regulation Africa regulator",
                    "{market} regulatory framework approval requirements",
                    "{market} new regulation law 2025 2026",
                    "{market} regulation European Union United States"],
        "system": _ANALYST + (
            " Describe the regulatory environment, focusing on MEA. For each country name the competent authority "
            "for THIS product category as the DEFINITION describes it (a food supplement, a medicine, a "
            "fertiliser and an animal feed are each regulated by a different body) -- the CURRENT, SPECIFIC "
            "government agency that registers or approves it, under its name as of today. "
            "Ministries are often renamed, merged or split: give the current name, never a former one. Never "
            "list a research institute or an industry association as a regulator. Name the legal instrument "
            "with its name and number in the form '<Name> Act <number> of <year>'. Milestones must each be a DIFFERENT jurisdiction and real: the date is the year "
            "the instrument ENTERED INTO FORCE (not when it was proposed or announced), and each names its "
            "instrument; give the year only unless the month is certain. Do not describe an amendment, "
            "revision or phase-out unless the EVIDENCE names it. Every regulator, requirement and milestone is "
            "checked claim by claim against independent evidence and left out if it cannot be confirmed, so "
            "prefer fewer, certain items. Scores are 1 (restrictive) to 5 (enabling), integers, and are an "
            "assessment. If the market has no product- or sector-specific regulation worth a chapter, set "
            "applicable to false."),
        "schema": ('{"applicable": <true|false>, "category_terms": [<1-3 words or short phrases naming the regulatory '
                   'category this product is registered under, e.g. "fertiliser", "food supplement", "animal feed">], '
                   '"milestones": [{"date": <year it entered into force, e.g. "2022">, '
                   '"jurisdiction": <string>, "event": <8-14 words>, "instrument": <legal instrument name and '
                   'number>}, x5, chronological, 5 different jurisdictions], '
                   '"scorecard_dimensions": [<5 short names of the regulatory dimensions that matter for THIS '
                   'market, e.g. "Registration framework", "Approval speed", "Import & licensing">], '
                   '"scorecard": {"<country>": [<5 integer scores>], ... one per country in: {REG_COUNTRIES}}, '
                   '"snapshot": [{"country": <string>, "regulator": <current competent authority for this product '
                   'category>, "instrument": <the legal instrument name and number it registers under>, '
                   '"requirements": <1-2 sentences>}, one per country in: {REG_COUNTRIES}], '
                   '"watchlist": [{"theme": <string>, "what_to_watch": <string>, "implication": <string>}, x5]}'),
    },
    {
        "key": "vendors",
        # vendor data is never jittered (PLAN-05): a share is printed only when a quote states it
        "randomize": False,
        "queries": ["{market} leading producers manufacturers companies market share",
                    "{market} Middle East Africa companies distributors market share",
                    "{market} value chain suppliers manufacturers distribution margins",
                    "{market} company acquisitions partnerships Middle East Africa 2025"],
        "system": _ANALYST + (
            " Describe the vendor landscape. Name only real, verifiable companies, and only companies that EARN "
            "THE REVENUE the DEFINITION measures, at its VALUE-CHAIN LEVEL -- never suppliers of inputs or of "
            "excluded products, never buyers, never firms whose business is another level of the value chain. "
            "If one country's producers dominate this market's supply (e.g. Chinese producers for vitamin C), "
            "the lists must reflect that; include the largest real local/regional players in MEA. Count a parent "
            "and its WHOLLY OWNED subsidiaries as one company; a joint venture is its own company. "
            "A vendor's MEA share is given ONLY when a WEB EVIDENCE line states that company's share of the MEA "
            "market (or its MEA revenue in this market): copy that line exactly into quote. Otherwise share_pct "
            "is null and quote is \"\" -- never estimate a share and never invent a quote. "
            "List in positioning every company in CONTEXT.top_companies that earns the defined revenue and the "
            "largest MEA players (no ranking or tier); MEA presence is a CMI assessment. Capability scores are "
            "integers 1 (limited) to 5 (leading) and are an assessment. Classify "
            "every company in CONTEXT.top_companies: its parent group, business type, and whether it earns the "
            "revenue the DEFINITION measures. For each dashboard row set verified=true only if the EVIDENCE "
            "confirms the MEA moves described; give the year of every deal, acquisition or expansion, and leave "
            "out a move whose year is not in the evidence (never call it 'recent')."),
        "schema": ('{"value_chain": [{"stage": <1-3 words>, "examples": [<3-4 items, 1-3 words each>], '
                   '"share_low_pct": <number>, "share_high_pct": <number>}, x5 stages from inputs to the sales '
                   'channel; the end-user stage is added automatically], '
                   '"mea_shares": [{"vendor": <string>, "share_pct": <number|null, ONLY if a WEB EVIDENCE line states '
                   'it>, "quote": <that evidence line copied exactly, or "">}, ... up to 7 companies], '
                   '"positioning": [{"vendor": <string>, "role": <one line: what it sells in THIS market>, '
                   '"mea_presence": "Leading|Strong|Moderate|Limited|None"}, ... 8-12 companies, every company in '
                   'CONTEXT.top_companies that earns the defined revenue plus the largest MEA players], '
                   '"capability_dimensions": [<6 short names (1-3 words) of the capabilities that differentiate vendors '
                   'in THIS market; the last one is "MEA presence">], '
                   '"capabilities": {"<vendor>": [<6 integer scores>], ... x10 vendors}, '
                   '"dashboard": [{"vendor": <string>, "hq": <country>, "core_strength": <string>, '
                   '"mea_footprint": <1 sentence on MEA presence and dated moves (with their year)>, '
                   '"verified": <true|false>, '
                   '"ownership_note": <"now part of X", "formerly Y", "acquired Z in 2024" -- current ownership, '
                   'mergers and rebrands; "" if none>}, x8], '
                   '"company_classification": [{"company": <name exactly as in CONTEXT.top_companies>, '
                   '"parent_group": <string>, "business_type": <e.g. "producer", "technology provider", '
                   '"input supplier">, "earns_defined_revenue": <true|false>}, one per company in CONTEXT.top_companies]}'),
    },
    {
        "key": "strategy",
        "randomize": False,
        "queries": ["{market} market entry strategy distribution partnerships",
                    "{market} growers adoption barriers recommendations"],
        "system": _ANALYST + " Turn the outlook into concrete, market-specific recommendations focused on MEA.",
        "schema": ('{"phases": [{"name": <2-3 words>, "window": <e.g. "0-12 MONTHS">, "actions": [<3 actions, 4-8 '
                   'words each>]}, x3], '
                   '"stakeholders": [{"stakeholder": <string>, "priority_moves": <2-3 sentences>, "kpis": <3 KPIs, '
                   'semicolon-separated>}, x6], '
                   '"priority_actions": [<5 actions for 2026-27, 1 sentence each>]}'),
    },
    {
        "key": "published",
        "randomize": False,
        "queries": ["{market} market size 2025 USD billion", "{market} market size 2024 report CAGR",
                    "{market} Middle East Africa market size report", "{market} market size Grand View Research",
                    "{market} market size MarketsandMarkets Mordor Intelligence"],
        "system": (
            "You extract PUBLISHED third-party market-size figures from web search results. Use ONLY figures "
            "that appear in the EVIDENCE, copied exactly; name the publisher as it appears there. Only figures for "
            "THIS market's scope as in the DEFINITION -- never a parent or adjacent market (e.g. the whole shrimp "
            "market is not 'super-intensive shrimp farming'). Never estimate or invent. Return ONLY valid JSON."),
        "schema": ('{"global_2025": [{"publisher": <string>, "value_usd_bn": <number, the 2025 value in USD '
                   'billion>, "quote": <the evidence text it came from>}, ... up to 6], '
                   '"references": [{"name": <"Publisher: scope", e.g. "Mordor Intelligence: Middle East & Africa">, '
                   '"scope": <string>, "published_value": <e.g. "$4.2B (2025) to $8.9B (2030), 16.2% CAGR">, '
                   '"quote": <the evidence text it came from>}, ... up to 7, prefer regional / MEA / country figures]}'),
    },
]
