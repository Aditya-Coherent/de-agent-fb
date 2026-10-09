"""Fields for pipeline CSV export — website extract and data provenance."""
from __future__ import annotations

import re
from typing import Any


_DESC_JUNK = re.compile(
    r"\[skip to content\]|\bcookies?\b|this site uses cookies|open menu|"
    r"\[\d+\]|\[open menu\]|\*\s*call us|login\s*\||register\s*\||"
    r"privacy policy|all rights reserved|subscribe to our|"
    r"product\s*\[open menu\]|solutions\s*\[open menu\]|resources\s*\[open menu\]",
    re.I,
)
_NAV_LINK = re.compile(r"\[[^\]]{1,40}\]|\(\s*open menu\s*\)|#{1,3}\s", re.I)
_SCHEMA_JUNK_TOKENS = frozenset(
    {
        "name",
        "website",
        "products",
        "services",
        "summary",
        "javascript",
        "appears",
        "disabled",
        "search",
        "company",
    }
)

# Website nav / footer / social — not products
_NAV_UI_TOKENS = frozenset(
    {
        "skip",
        "content",
        "navigation",
        "menu",
        "open",
        "close",
        "facebook",
        "twitter",
        "instagram",
        "linkedin",
        "pinterest",
        "telegram",
        "youtube",
        "cookie",
        "cookies",
        "privacy",
        "login",
        "register",
        "search",
        "home",
        "contact",
        "about",
        "news",
        "blog",
        "brands",
        "locations",
        "sustainability",
        "partner",
        "partners",
        "careers",
        "legal",
        "imprint",
        "europe",
        "global",
        "english",
        "language",
    }
)

_GENERIC_VENDOR_PHRASE = re.compile(
    r"\bvendor in\b|\bparticipates in\b|â€|â€“",
    re.I,
)


def _tokenize_low(text: str) -> list[str]:
    low = re.sub(r"[^\w\s,]", " ", (text or "").lower())
    return [x.strip() for x in re.split(r"[\s,]+", low) if x.strip()]


def is_nav_keyword_junk(text: str) -> bool:
    """Comma-separated nav/social tokens masquerading as a product list."""
    tokens = _tokenize_low(text)
    if len(tokens) < 4:
        return False
    nav_hits = sum(1 for t in tokens if t in _NAV_UI_TOKENS)
    if nav_hits >= 3 and nav_hits / len(tokens) >= 0.35:
        return True
    if "skip" in tokens and ("navigation" in tokens or "content" in tokens):
        return True
    social = sum(1 for t in tokens if t in ("facebook", "twitter", "instagram", "linkedin", "pinterest"))
    return social >= 2


def filter_product_keywords(keywords: list[str], *, max_items: int = 8) -> list[str]:
    """Drop nav/schema tokens from signal keyword lists."""
    out: list[str] = []
    for k in keywords:
        s = str(k).strip()
        low = s.lower()
        if len(low) < 4 or low in _SCHEMA_JUNK_TOKENS or low in _NAV_UI_TOKENS:
            continue
        if low in out:
            continue
        out.append(s)
        if len(out) >= max_items:
            break
    return out


def is_schema_junk_text(text: str) -> bool:
    """Detect LLM/crawl artifacts like 'name, website, products, services, summary'."""
    t = (text or "").strip()
    if not t:
        return True
    low = re.sub(r"[^\w\s,]", " ", t.lower())
    tokens = [x.strip() for x in re.split(r"[\s,]+", low) if x.strip()]
    if not tokens:
        return True
    schema_hits = sum(1 for tok in tokens if tok in _SCHEMA_JUNK_TOKENS)
    if schema_hits >= 4 and schema_hits / max(len(tokens), 1) >= 0.45:
        return True
    if re.search(
        r"\b(?:name|website|products|services|summary)\b\s*,\s*"
        r"\b(?:name|website|products|services|summary)\b",
        t,
        re.I,
    ):
        return True
    if is_nav_keyword_junk(t):
        return True
    return False


def is_weak_role_description(text: str) -> bool:
    """True when description should be replaced by LLM."""
    t = (text or "").strip()
    if not t or len(t) < 12:
        return True
    if is_schema_junk_text(t) or is_nav_keyword_junk(t):
        return True
    if _GENERIC_VENDOR_PHRASE.search(t):
        return True
    if _DESC_JUNK.search(t) or _NAV_LINK.search(t):
        return True
    # "Company: word, word, word" keyword salad
    if re.match(r"^[\w\s]+:\s*[\w\s,]{10,}$", t) and t.count(",") >= 3:
        if is_nav_keyword_junk(t.split(":", 1)[-1]):
            return True
    return False


# CEO CSV Functionality — role-led phrase (e.g. Manufacturer of BMS); trim on word boundaries
FUNCTIONALITY_MAX_LEN = 96
FUNCTIONALITY_MAX_WORDS = 14
ROLE_DESCRIPTION_MAX_LEN = FUNCTIONALITY_MAX_LEN
ROLE_DESCRIPTION_MAX_WORDS = FUNCTIONALITY_MAX_WORDS

_ROLE_OF_PREFIX: dict[str, str] = {
    "Manufacturer": "Manufacturer of",
    "Supplier": "Supplier of",
    "Distributor": "Distributor of",
    "Technology Provider": "Technology provider of",
    "Integrator": "System integrator of",
    "EPC / Engineering": "Engineering contractor for",
    "Project Developer": "Project developer for",
    "Research / Consulting": "Research and consulting on",
    "Industry Body": "Industry body for",
    "Other": "Participant in",
}

_ROLE_PLURAL_ONLY: dict[str, str] = {
    "Manufacturer": "Manufacturers",
    "Supplier": "Suppliers",
    "Distributor": "Distributors",
    "Technology Provider": "Technology providers",
    "Integrator": "System integrators",
    "Other": "Other market participants",
}

_ROLE_LEAD_RE = re.compile(
    r"^(?:manufacturers?|suppliers?|distributors?|technology\s+providers?|"
    r"system\s+integrators?|oem(?:s)?|engineering\s+contractors?|"
    r"other\s+[\w\s/+-]{0,32}(?:manufacturers?|distributors?|suppliers?|participants?))\b",
    re.I,
)


def role_value_chain_label(role: str) -> str:
    """Back-compat — maps Role to a short value-chain noun."""
    r = (role or "").strip()
    if r in _ROLE_OF_PREFIX:
        return _ROLE_OF_PREFIX[r].split()[0]
    return "Participant"


def finalize_functionality(
    text: str,
    *,
    max_len: int = FUNCTIONALITY_MAX_LEN,
    max_words: int = FUNCTIONALITY_MAX_WORDS,
) -> str:
    """Functionality cell — keep role-led phrasing (do not strip 'Manufacturer of')."""
    s = _fix_role_text_encoding(_one_line(text or "", max_len=400))
    if s.endswith("..."):
        s = s[:-3].rstrip(" ,.;")
    s = re.sub(r"\s*,\s*", ", ", s.rstrip(".,;"))
    words = s.split()
    if not words:
        return ""
    kept: list[str] = []
    for w in words:
        if len(kept) >= max_words:
            break
        candidate = " ".join(kept + [w])
        if len(candidate) > max_len and kept:
            break
        kept.append(w)
    while kept and _looks_truncated_word(kept[-1]):
        kept.pop()
    while kept and kept[-1].lower() in {"and", "or", "&"}:
        kept.pop()
    line = " ".join(kept)
    return _strip_incomplete_for_tail(line)


def _functionality_focus_from_products(key_products: str) -> str:
    parts = [p.strip() for p in (key_products or "").split(",") if p.strip()]
    return parts[0] if parts else ""


def _functionality_focus_from_function(company_function: str, market_ctx: str) -> str:
    fn = re.sub(r"[_-]", " ", (company_function or "").strip().strip("."))
    if not fn or is_nav_keyword_junk(fn) or _is_generic_company_function(fn):
        return ""
    fn = re.sub(
        r"^(?:manufacturer|supplier|distributor|wholesaler|integrator|oem)\s+(?:of\s+)?",
        "",
        fn,
        flags=re.I,
    ).strip()
    if not fn or _is_generic_company_function(fn):
        return ""
    ctx = (market_ctx or "").strip()
    if ctx and not _market_terms_overlap(fn, ctx):
        return f"{fn} for {ctx}"
    return fn[0].upper() + fn[1:] if fn else fn


def _description_already_role_led(text: str) -> bool:
    t = (text or "").strip()
    return bool(t and _ROLE_LEAD_RE.match(t))


def _wants_oem_phrase(*blobs: str) -> bool:
    low = " ".join(b for b in blobs if b).lower()
    return bool(re.search(r"\boem(?:s)?\b", low))


def _compose_functionality_phrase(
    role: str,
    focus: str,
    *,
    market_ctx: str,
    company_function: str = "",
) -> str:
    role_key = (role or "").strip()
    focus = (focus or "").strip(" .,-")
    ctx = (market_ctx or "").strip()
    fn_low = re.sub(r"[_-]", " ", (company_function or "")).lower()

    if _wants_oem_phrase(focus, role_key, company_function):
        obj = focus or _short_market_hint(ctx) or ctx
        obj = re.sub(r"^oem(?:s)?\s+of\s+", "", obj, flags=re.I).strip() or obj
        return f"OEM of {obj}"

    if role_key == "Other":
        if "component" in fn_low and "distrib" in fn_low:
            return "Other component distributors"
        if "component" in fn_low:
            return "Other component manufacturers"
        if focus:
            return f"Other {focus} participant"
        if ctx:
            hint = _short_market_hint(ctx) or ctx
            return f"Other {hint} participants"

    if focus:
        prefix = _ROLE_OF_PREFIX.get(role_key, "Participant in")
        if role_key == "Technology Provider":
            return f"Technology provider of {focus}"
        if role_key == "Integrator":
            return f"System integrator of {focus}"
        if role_key == "Distributor":
            return f"Distributor of {focus}"
        if role_key == "Manufacturer":
            return f"Manufacturer of {focus}"
        if role_key == "Supplier":
            return f"Supplier of {focus}"
        return f"{prefix} {focus}"

    plural = _ROLE_PLURAL_ONLY.get(role_key, "")
    if plural:
        return plural

    if ctx:
        hint = _short_market_hint(ctx) or ctx
        prefix = _ROLE_OF_PREFIX.get(role_key, "Participant in")
        return f"{prefix} {hint}"
    return _ROLE_PLURAL_ONLY.get(role_key, "Market participant")


def _is_meta_functionality_focus(focus: str) -> bool:
    """Reject 'Leading manufacturer', 'European brazed exchanger', bare role labels, etc."""
    d = re.sub(r"\s+", " ", (focus or "").strip().lower())
    if not d or len(d) < 5:
        return True
    if d in _GENERIC_FN or _is_generic_company_function(d):
        return True
    # Ends with role/meta noun and lacks a concrete product token
    _CONCRETE = (
        r"bphe|brazed\s+plate|plate\s+heat|shell.?and.?tube|heat\s+pump|chiller|"
        r"evaporator|condenser|refrigerant|hydronic|compressor|cooling\s+tower|"
        r"bus\s?bar|copper\s+(?:wire|strip|rod|bar)|battery|signage|cms|"
        r"software|platform|module|valve|coil|gasket|refrigeration\s+system"
    )
    has_concrete = bool(re.search(_CONCRETE, d, re.I))
    if re.search(
        r"\b(manufacturer|distributor|supplier|integrator|wholesaler|specialist|"
        r"leader|vendor|provider|participant|equipment\s+manufacturer)\s*$",
        d,
    ) and not has_concrete:
        return True
    if re.match(
        r"^(?:leading|global|major|top|premier|world(?:wide|'?s?)?|european|"
        r"chinese|bulgarian|uk|regional|international|diversified)\b",
        d,
    ) and not has_concrete:
        return True
    if re.match(r"^(?:manufacturer|distributor|supplier|integrator)\s+of\b", d):
        return True
    words = [w for w in re.findall(r"[a-z0-9]+", d) if len(w) > 2]
    if len(words) <= 1 and not has_concrete:
        return True
    if len(words) == 2 and words[-1] in {
        "manufacturer",
        "distributor",
        "supplier",
        "specialist",
        "leader",
        "exchanger",
    }:
        return True
    return False


def _pick_best_product_focus(key_products: str) -> str:
    parts = [
        p.strip()
        for p in (key_products or "").split(",")
        if p.strip() and not is_nav_keyword_junk(p) and not is_schema_junk_text(p)
    ]
    if not parts:
        return ""
    ranked = sorted(parts, key=lambda x: (len(x), x.count(" ")), reverse=True)
    for p in ranked:
        if not _is_meta_functionality_focus(p):
            return p
    return ranked[0]


def _market_application_hint(market_ctx: str, scope: dict | None) -> str:
    if isinstance(scope, dict):
        terms = [str(t).strip() for t in (scope.get("industry_terms") or []) if str(t).strip()]
        if terms:
            return ", ".join(terms[:2]).lower()
        sm = str(scope.get("market") or "").strip()
        if sm:
            return sm[:56].lower()
    hint = _short_market_hint(market_ctx) or (market_ctx or "").strip()
    return re.sub(r"\s*market\s*$", "", hint, flags=re.I).strip().lower()


def _focus_with_market_application(focus: str, market_ctx: str, scope: dict | None) -> str:
    f = (focus or "").strip(" .,-")
    if not f or _is_meta_functionality_focus(f):
        return ""
    app = _market_application_hint(market_ctx, scope)
    if not app or _market_terms_overlap(f, app) or _market_terms_overlap(f, market_ctx):
        return f
    combined = f"{f} for {app}"
    if len(combined) <= FUNCTIONALITY_MAX_LEN:
        return combined
    return f


def _object_from_role_led_phrase(text: str) -> str:
    m = re.match(
        r"^(?:manufacturers?|suppliers?|distributors?|technology\s+providers?|"
        r"system\s+integrators?|oem(?:s)?)\s+of\s+(.+)$",
        (text or "").strip(),
        re.I,
    )
    return m.group(1).strip(" .,-") if m else ""


def _sanitize_functionality_focus(
    focus: str,
    *,
    market_ctx: str,
    scope: dict | None,
) -> str:
    f = _strip_role_label_prefix(_one_line(focus or "", max_len=200)).strip(" .,-")
    if not f or is_nav_keyword_junk(f) or is_schema_junk_text(f):
        return ""
    if _is_meta_functionality_focus(f):
        return ""
    return _focus_with_market_application(f, market_ctx, scope)


def _resolve_functionality_focus(
    role: str,
    description: str,
    key_products: str,
    company_function: str,
    *,
    market_ctx: str,
    scope: dict | None,
    max_len: int,
) -> str:
    """Pick the most concrete 'what they do' phrase — products first, not meta labels."""
    kp_full = key_products or ""

    for candidate in (
        _pick_best_product_focus(kp_full),
        _sanitize_functionality_focus(
            _blend_product_phrase(kp_full, market_ctx=market_ctx, max_len=max_len),
            market_ctx=market_ctx,
            scope=scope,
        ),
        _sanitize_functionality_focus(
            _functionality_focus_from_function(company_function, market_ctx),
            market_ctx=market_ctx,
            scope=scope,
        ),
    ):
        if candidate:
            return candidate

    raw = _one_line(description or "", max_len=240)
    if raw and _description_already_role_led(raw):
        obj = _object_from_role_led_phrase(raw)
        clean = _sanitize_functionality_focus(obj, market_ctx=market_ctx, scope=scope)
        if clean:
            return clean
    else:
        desc_line = _ceo_market_line_from_description(
            description, market_ctx=market_ctx, max_len=max_len
        )
        clean = _sanitize_functionality_focus(desc_line, market_ctx=market_ctx, scope=scope)
        if clean:
            return clean

    app = _market_application_hint(market_ctx, scope)
    if app and not _is_meta_functionality_focus(app):
        return app
    return ""


def truncate_for_csv(text: str, *, max_len: int = 72) -> str:
    """CEO CSV cell — one short line."""
    s = _one_line(text or "", max_len=max_len + 20)
    if len(s) <= max_len:
        return s
    return s[: max_len - 3].rsplit(" ", 1)[0] + "..."


_DANGLING_END = frozenset(
    {
        "and",
        "or",
        "for",
        "with",
        "in",
        "to",
        "the",
        "a",
        "an",
        "of",
        "on",
        "at",
        "by",
        "&",
    }
)

# Valid short tokens — do not strip as truncation fragments
_SHORT_WORD_OK = frozenset(
    {
        "ai",
        "io",
        "iot",
        "oem",
        "cms",
        "epc",
        "mss",
        "api",
        "aws",
        "usa",
        "uk",
        "eu",
        "plc",
        "ltd",
        "arm",
        "eoat",
        "cam",
        "erp",
        "mes",
        "scada",
        "plc",
        "3d",
        "2d",
        "of",
        "in",
        "for",
    }
)

# Obvious mid-word cuts from old [:36] / hard char slices
_TRUNCATED_TAIL = re.compile(
    r"(?:programmin|specia|compon|integrat|delive|manuf|manu|cont|automatio|"
    r"grippe|sy|comp|pro|del|wit|co|robo)$",
    re.I,
)


def _fix_role_text_encoding(text: str) -> str:
    """Fix common Windows/UTF-8 mojibake in CSV role lines."""
    s = (text or "").replace("\ufffd", " ")
    for bad, good in (
        ("\u00e2\u20ac\u201d", "\u2014"),  # â€" -> em dash
        ("\u00e2\u20ac\u2122", "'"),       # â€™ -> apostrophe
        ("\u00e2\u20ac\u0153", '"'),        # â€œ -> open quote
        ("\u00e2\u20ac", '"'),             # â€ -> close quote fragment
    ):
        s = s.replace(bad, good)
    return s


def _looks_truncated_word(word: str) -> bool:
    """True when the last token is likely a chopped word, not a complete one."""
    w = (word or "").strip().rstrip(".,;:")
    if not w:
        return True
    low = w.lower()
    if low in _SHORT_WORD_OK:
        return False
    if len(w) <= 3:
        return True
    if _TRUNCATED_TAIL.search(low):
        return True
    if len(w) <= 5 and not re.search(
        r"(ing|tion|ment|ence|ance|ness|ists?|ally|ical|able|ible|ware|"
        r"works|bots?|ics|ers?|ors?|ism|ary|ous|ive|ful|less|ed|es|os|us|ly|al|ic)$",
        low,
    ):
        return True
    return False


def _strip_truncated_tail_words(words: list[str]) -> list[str]:
    kept = list(words)
    while kept and _looks_truncated_word(kept[-1]):
        kept.pop()
    while kept and kept[-1].lower() in _DANGLING_END:
        kept.pop()
    return kept


def trim_complete_phrase(
    text: str,
    *,
    max_len: int = 72,
    max_words: int = 12,
) -> str:
    """Short CEO line — complete words only, never trailing ellipsis."""
    s = _fix_role_text_encoding(_strip_role_label_prefix(_one_line(text or "", max_len=400)))
    if s.endswith("..."):
        s = s[:-3].rstrip(" ,.;")
    s = re.sub(r"\s*,\s*", ", ", s.rstrip(".,;"))
    words = s.split()
    if not words:
        return ""
    kept: list[str] = []
    for w in words:
        if len(kept) >= max_words:
            break
        candidate = " ".join(kept + [w])
        if len(candidate) > max_len and kept:
            break
        kept.append(w)
    kept = [w for w in kept if not _looks_truncated_word(w)]
    kept = _strip_truncated_tail_words(kept)
    return " ".join(kept)


def _strip_incomplete_for_tail(text: str) -> str:
    """Drop truncated 'for digital' / bare 'for' tails from stored LLM snippets."""
    s = (text or "").rstrip(".,;")
    if s.endswith("..."):
        s = s[:-3].rstrip(" ,.;")
    m = re.search(
        r"\s+for\s+(?:digital|commercial|retail|enterprise|global)(?:\s+signage)?\s*$",
        s,
        re.I,
    )
    if m:
        s = s[:m.start()].rstrip(" ,.;")
    # Bare trailing "for" left after word-cap trim (e.g. "automation comp for")
    if re.search(r"\s+for\s*$", s, re.I):
        s = re.sub(r"\s+for\s*$", "", s, flags=re.I).rstrip(" ,.;")
    words = s.split()
    return " ".join(_strip_truncated_tail_words(words))


def finalize_role_description(
    text: str,
    *,
    max_len: int = ROLE_DESCRIPTION_MAX_LEN,
    max_words: int = ROLE_DESCRIPTION_MAX_WORDS,
) -> str:
    """Role_Description CSV cell — full phrase, no cut-off sentences."""
    line = trim_complete_phrase(text, max_len=max_len, max_words=max_words)
    return _strip_incomplete_for_tail(line)


_ROLE_LABEL_PREFIX = re.compile(
    r"^(?:manufacturer|supplier|distributor|integrator|technology provider|"
    r"software vendor|software provider|oem|vendor|provider|company)\s+(?:of\s+)?",
    re.I,
)


def _strip_role_label_prefix(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    prev = None
    while s != prev:
        prev = s
        s = _ROLE_LABEL_PREFIX.sub("", s, count=1).strip()
    return s


def _short_word_line(text: str, *, max_words: int = 10, max_len: int = 72) -> str:
    """First N complete words — no ellipsis."""
    cleaned = _strip_role_label_prefix(_one_line(text or "", max_len=240))
    if not cleaned or is_weak_role_description(cleaned) or is_nav_keyword_junk(cleaned):
        return ""
    return trim_complete_phrase(cleaned, max_len=max_len, max_words=max_words)


def _normalize_field_compare(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip())


def _market_context_label(*, market: str = "", scope: dict | None = None) -> str:
    """Short market phrase for CEO-readable role lines."""
    if scope and isinstance(scope, dict):
        sm = str(scope.get("market") or "").strip()
        if sm:
            return sm[:48]
    label = re.sub(r"\s*market\s*$", "", (market or "").strip(), flags=re.I)
    return label[:48] if label else "this market"


def _short_market_hint(market_ctx: str) -> str:
    """Compact market label so suffixes fit without truncation."""
    ctx = (market_ctx or "").strip()
    low = ctx.lower()
    if "signage" in low:
        return "digital signage"
    label = re.sub(r"\s*market\s*$", "", ctx, flags=re.I).strip()
    return label[:28] if label else ""


def _market_terms_overlap(line: str, market_ctx: str) -> bool:
    line_words = set(re.findall(r"[a-z0-9]+", (line or "").lower()))
    ctx_words = set(re.findall(r"[a-z0-9]+", (market_ctx or "").lower()))
    ctx_words -= {"market", "global", "systems", "system", "the", "and", "for"}
    return bool(line_words & ctx_words)


def _is_duplicate_of_key_products(line: str, key_products: str) -> bool:
    """True only when the line is literally the same comma-list as Key_Products."""
    if not line or not key_products:
        return False
    norm_line = _normalize_field_compare(line)
    parts = [p.strip() for p in key_products.split(",") if p.strip()]
    if not parts:
        return False
    for n in (1, 2, 3):
        if norm_line == _normalize_field_compare(", ".join(parts[:n])):
            return True
    return False


_GENERIC_FN = frozenset(
    {
        "vendor",
        "distributor",
        "manufacturer",
        "supplier",
        "integrator",
        "reseller",
        "dealer",
        "wholesaler",
        "importer",
        "exporter",
        "oem",
        "partner",
        "service_provider",
        "service provider",
        "other",
    }
)

_GENERIC_TEMPLATE = re.compile(
    r"^(?:vendor|distributor|manufacturer|supplier|integrator|reseller|dealer|"
    r"service[_\s-]?provider|oem)\s+(?:in|for)\s+",
    re.I,
)


def _is_generic_company_function(fn: str) -> bool:
    t = re.sub(r"[_-]", " ", (fn or "").strip().lower())
    t = re.sub(r"\s+", " ", t)
    if not t or t in _GENERIC_FN:
        return True
    words = t.split()
    if len(words) <= 2 and all(w in _GENERIC_FN | {"digital", "signage", "commercial"} for w in words):
        return True
    return False


def _is_generic_template_line(line: str) -> bool:
    return bool(_GENERIC_TEMPLATE.match((line or "").strip()))


_BOILERPLATE_DESC = (
    "cloud-based digital signage cms platform",
    "cloud-based digital signage content management",
    "digital signage cms platform and content",
    "commercial displays, led video walls",
    "enterprise digital signage platform and system",
)


def _is_boilerplate_description(raw: str) -> bool:
    low = (raw or "").strip().lower()
    return any(p in low for p in _BOILERPLATE_DESC)


def _product_list_style(text: str) -> bool:
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    return len(parts) >= 2


def _with_market_context(line: str, market_ctx: str, *, max_len: int = 72) -> str:
    if not line:
        return ""
    line = finalize_role_description(line, max_len=max_len)
    ctx = _short_market_hint(market_ctx)
    if not ctx or _market_terms_overlap(line, ctx):
        return line
    if re.search(r"\bfor\s+(?:digital|commercial|retail|enterprise|global)", line.lower()):
        return line
    extended = finalize_role_description(f"{line} for {ctx}", max_len=max_len)
    if ctx.lower() not in extended.lower():
        return line
    return extended


def _combine_phrases(
    left: str,
    connector: str,
    right: str,
    *,
    max_len: int = 72,
) -> str:
    if not right:
        return finalize_role_description(left, max_len=max_len)
    combined = finalize_role_description(f"{left} {connector} {right}", max_len=max_len)
    if combined and _normalize_field_compare(combined) != _normalize_field_compare(left):
        return combined
    return finalize_role_description(left, max_len=max_len)


def _ceo_market_line_from_description(
    description: str,
    *,
    market_ctx: str,
    max_len: int = 72,
) -> str:
    """LLM/stored blurb — short CEO line, not a product comma list."""
    raw = _strip_role_label_prefix(_one_line(description, max_len=240))
    if raw.endswith("..."):
        raw = raw[:-3].rstrip(" ,.;")
    if not raw or is_nav_keyword_junk(raw) or _is_boilerplate_description(raw):
        return ""
    if is_schema_junk_text(raw):
        return ""

    line = _short_word_line(raw, max_words=12, max_len=max_len)
    if not line:
        return ""
    if _product_list_style(line) and is_weak_role_description(raw):
        return ""
    return _with_market_context(line, market_ctx, max_len=max_len)


def _function_to_market_line(company_function: str, market_ctx: str, *, max_len: int = 72) -> str:
    fn = re.sub(r"[_-]", " ", (company_function or "").strip().strip("."))
    if not fn or is_nav_keyword_junk(fn) or _is_generic_company_function(fn):
        return ""
    ctx = (market_ctx or "").strip()
    if ctx and not _market_terms_overlap(fn, ctx):
        line = f"{fn} for {ctx}"
    else:
        line = fn[0].upper() + fn[1:] if fn else fn
    return finalize_role_description(line, max_len=max_len)


def _tail_from_boilerplate_description(description: str, *, max_len: int = 72) -> str:
    raw = _strip_role_label_prefix(_one_line(description, max_len=240))
    if not _is_boilerplate_description(raw):
        return ""
    low = raw.lower()
    for anchor in (
        "cms platform with ",
        "cms platform for ",
        "cms platform and ",
        "content management platform with ",
        "cms and content ",
        "signage platform with ",
    ):
        idx = low.find(anchor)
        if idx >= 0:
            tail = raw[idx + len(anchor) :].strip(" .,-")
            if tail and len(tail) >= 6 and not is_nav_keyword_junk(tail):
                return finalize_role_description(
                    _short_word_line(tail, max_words=8, max_len=max_len) or tail,
                    max_len=max_len,
                )
    return ""


def _blend_product_phrase(key_products: str, *, market_ctx: str = "", max_len: int = 72) -> str:
    """Two products as a readable phrase — distinct from comma Key_Products."""
    parts = [p.strip() for p in key_products.split(",") if p.strip()]
    if not parts:
        return ""
    if len(parts) >= 3:
        phrase = f"{parts[0]} and {parts[2]}"
    elif len(parts) >= 2:
        phrase = f"{parts[0]} and {parts[1]}"
    else:
        phrase = parts[0]
    phrase = _strip_role_label_prefix(phrase)
    line = finalize_role_description(phrase, max_len=max_len)
    if not line:
        return ""
    return _with_market_context(line, market_ctx, max_len=max_len)


def _enriched_description_line(
    description: str,
    key_products: str,
    *,
    market_ctx: str,
    max_len: int = 72,
) -> str:
    """Prefer unique LLM line; for boilerplate, blend products + distinctive tail."""
    if not _is_boilerplate_description(description):
        return _ceo_market_line_from_description(description, market_ctx=market_ctx, max_len=max_len)

    tail = _tail_from_boilerplate_description(description, max_len=max_len)
    blend = _blend_product_phrase(key_products, market_ctx="", max_len=max_len)
    if blend and tail:
        return _combine_phrases(blend, "with", tail, max_len=max_len)
    if blend:
        return blend
    if tail:
        return finalize_role_description(f"Digital signage CMS with {tail}", max_len=max_len)
    return _ceo_market_line_from_description(description, market_ctx=market_ctx, max_len=max_len)


def _role_aware_fallback(role: str, key_products: str, *, market_ctx: str, max_len: int = 72) -> str:
    """Last resort — weave role + one product without generic 'vendor in market' templates."""
    parts = [p.strip() for p in key_products.split(",") if p.strip()]
    prod = parts[0] if parts else ""
    role_key = (role or "").strip().lower()
    ctx = (market_ctx or "").strip()
    if prod and role_key == "integrator":
        line = f"Integrates and deploys {prod.lower()}"
    elif prod and role_key in ("distributor", "supplier"):
        line = f"Distributes {prod.lower()}"
    elif prod and role_key == "technology provider":
        line = f"Provides {prod.lower()}"
    elif prod:
        line = prod
    elif ctx:
        line = f"Active in {ctx}"
    else:
        return ""
    if ctx and prod and not _market_terms_overlap(line, ctx):
        line = f"{line} for {ctx}"
    return finalize_role_description(line, max_len=max_len)


def _is_weak_functionality_detail(detail: str) -> bool:
    """True when detail is too generic to pair with a role-led phrase."""
    return _is_meta_functionality_focus(detail)


def format_csv_functionality(
    role: str,
    description: str = "",
    key_products: str = "",
    *,
    market: str = "",
    company_function: str = "",
    scope: dict | None = None,
    max_len: int = FUNCTIONALITY_MAX_LEN,
    max_words: int = FUNCTIONALITY_MAX_WORDS,
) -> str:
    """CSV Functionality — role + concrete what-they-do in this market (no meta tautologies)."""
    ctx = _market_context_label(market=market, scope=scope)

    def _done(line: str) -> str:
        return finalize_functionality(line, max_len=max_len, max_words=max_words)

    focus = _resolve_functionality_focus(
        role,
        description,
        key_products,
        company_function,
        market_ctx=ctx,
        scope=scope,
        max_len=max_len,
    )

    raw = _one_line(description or "", max_len=240)
    if raw and _description_already_role_led(raw):
        obj = _object_from_role_led_phrase(raw)
        if obj and not _is_meta_functionality_focus(obj):
            return _done(raw)

    line = _compose_functionality_phrase(
        role, focus, market_ctx=ctx, company_function=company_function
    )
    return _done(line)


def format_csv_role_description(
    role: str,
    description: str = "",
    key_products: str = "",
    *,
    market: str = "",
    company_function: str = "",
    scope: dict | None = None,
    max_len: int = FUNCTIONALITY_MAX_LEN,
    max_words: int = FUNCTIONALITY_MAX_WORDS,
) -> str:
    """Back-compat alias — CSV column is Functionality."""
    return format_csv_functionality(
        role,
        description,
        key_products,
        market=market,
        company_function=company_function,
        scope=scope,
        max_len=max_len,
        max_words=max_words,
    )


def truncate_key_products(text: str, *, max_items: int = 4, max_len: int = 90) -> str:
    """Comma list capped for spreadsheet readability."""
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    if not parts:
        return ""
    short = ", ".join(parts[:max_items])
    return truncate_for_csv(short, max_len=max_len)


def polish_role_description(
    text: str,
    *,
    key_products: str = "",
    company: str = "",
    market: str = "",
    max_len: int = ROLE_DESCRIPTION_MAX_LEN,
) -> str:
    """CEO-ready one-liner — strip cookie/nav junk from crawled text."""
    raw = _one_line(text or "", max_len=800)
    if (
        is_schema_junk_text(raw)
        or is_nav_keyword_junk(raw)
        or _DESC_JUNK.search(raw)
        or _NAV_LINK.search(raw)
        or raw.count("[") >= 3
    ):
        raw = ""
    prod = (key_products or "").strip()
    if prod and (is_schema_junk_text(prod) or is_nav_keyword_junk(prod)):
        prod = ""
    if raw:
        parts = re.split(r"(?<=[.!?])\s+", raw)
        for part in parts:
            p = part.strip()
            if len(p) < 25 or _DESC_JUNK.search(p) or is_nav_keyword_junk(p):
                continue
            if p.count("|") >= 2 or p.count("*") >= 3:
                continue
            if _GENERIC_VENDOR_PHRASE.search(p):
                continue
            return finalize_role_description(p, max_len=max_len)
    # Never emit nav-keyword salads as role_description
    return ""


def _one_line(text: str, max_len: int = 2000) -> str:
    s = re.sub(r"[\r\n\t]+", " ", (text or "").strip())
    s = re.sub(r"\s{2,}", " ", s)
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


def extract_company_summary(smart_data: dict[str, Any] | None, *, max_len: int = 2000) -> str:
    """Short blurb from crawl/SSC/ddgs text for the CSV."""
    if not smart_data:
        return ""
    err = str(smart_data.get("error") or "")
    if err in ("no_crawl", "ssc_failed", "no_domain") and not smart_data.get("pages"):
        return ""

    bits: list[str] = []
    data = smart_data.get("data") or {}
    if isinstance(data, dict):
        co = data.get("company") or {}
        if isinstance(co, dict):
            for key in ("description", "about", "tagline", "overview"):
                val = co.get(key)
                if val and str(val).strip():
                    bits.append(str(val).strip())
            name = co.get("name")
            if name and str(name).strip() and str(name).strip().lower() not in bits[0:1]:
                pass
        biz = data.get("business") or {}
        if isinstance(biz, dict):
            for key in ("products", "services", "industries", "segments"):
                val = biz.get(key)
                if isinstance(val, list) and val:
                    label = key.replace("_", " ").title()
                    bits.append(f"{label}: {', '.join(str(x) for x in val[:10])}")
                elif val and not isinstance(val, list):
                    bits.append(f"{key}: {val}")
        intel = data.get("intel") or {}
        if isinstance(intel, dict):
            summary = intel.get("summary") or intel.get("synthesis")
            if summary and str(summary).strip():
                bits.append(str(summary).strip())

    pages = smart_data.get("pages") or []
    if pages and isinstance(pages[0], dict):
        page_text = str(pages[0].get("text") or "").strip()
        if page_text and len(page_text) >= 80 and not is_schema_junk_text(page_text[:400]):
            if not bits:
                bits.insert(0, page_text[:1400])
            elif len(" ".join(bits)) < 200:
                bits.insert(0, page_text[:800])

    if not bits and err:
        return _one_line(f"(enrichment: {err})", max_len=200)

    return _one_line(" ".join(bits), max_len=max_len)


_SUMMARY_LABEL_CUT = re.compile(r"\b(?:Products|Services|Industries|Segments)\s*:", re.I)


_SUMMARY_LABEL_LED = re.compile(
    r"^\s*(?:products|services|industries|segments|categories|solutions|offerings|menu)\s*:",
    re.I,
)

# Navigation / contact / e-commerce / boilerplate phrases that mean "not a company description".
_SUMMARY_JUNK = re.compile(
    r"(javascript|cookie|leave a message|call you back|toggle navigation|skip to content|"
    r"for best experience|send inquiry|send email|get best price|all rights reserved|"
    r"add to cart|sign in|log ?in|subscribe|newsletter|read more|click here|view more|"
    r"contact us|home page|privacy policy|terms of use)",
    re.I,
)

# Mojibake / encoding-artifact markers (from badly decoded scraped pages).
_MOJIBAKE = re.compile(r"(�|â€|Ã.|Â[ ·»]|ð\x9f|â)")


def _is_descriptive_sentence(sent: str) -> bool:
    """True when a sentence reads like prose about a company (not nav/list/markup junk)."""
    s = (sent or "").strip()
    if len(s) < 25 or len(s.split()) < 5:
        return False
    if is_schema_junk_text(s) or is_nav_keyword_junk(s):
        return False
    if _SUMMARY_LABEL_LED.match(s) or _SUMMARY_JUNK.search(s) or _MOJIBAKE.search(s):
        return False
    if s[:1] in "[<{|•·-*#" or s.lower().startswith(("http", "www.")):
        return False
    # Markdown / link / menu markup.
    if "](" in s or "][" in s or s.count("[") >= 2 or s.count("*") >= 2 or s.count("#") >= 1:
        return False
    letters = sum(c.isalpha() for c in s)
    if letters < len(s) * 0.55:  # too many symbols/numbers → likely junk
        return False
    return True


def _looks_like_page_junk(raw: str) -> bool:
    """A whole blob that carries scraped-page markers (a clean LLM summary has none)."""
    if _SUMMARY_JUNK.search(raw) or _MOJIBAKE.search(raw):
        return True
    if "[" in raw or "]" in raw or "#" in raw or raw.count("*") >= 2:
        return True
    if re.search(r"\bSUBMIT\b", raw) or raw.count("!") >= 2:
        return True
    return False


def summarize_for_csv(text: str, *, max_sentences: int = 3, max_len: int = 320) -> str:
    """Trim a company blurb to 1-3 clean prose sentences for the CSV Summary column.

    Drops trailing 'Products:/Services:' list-dumps and any sentence that reads like
    navigation, schema, or random page text. Returns '' when nothing usable remains.
    """
    raw = _one_line(text or "", max_len=2000)
    if not raw or _looks_like_page_junk(raw):
        return ""
    # Cut off structured list-dumps appended after the prose blurb.
    head = _SUMMARY_LABEL_CUT.split(raw, 1)[0].strip()
    if len(head) >= 40:
        raw = head
    sentences = re.split(r"(?<=[.!?])\s+", raw)
    picked: list[str] = []
    total = 0
    for sent in sentences:
        sent = sent.strip()
        if not _is_descriptive_sentence(sent):
            continue
        picked.append(sent if sent[-1] in ".!?" else sent + ".")
        total += len(sent) + 1
        if len(picked) >= max_sentences or total >= max_len:
            break
    summary = " ".join(picked).strip()
    if len(summary) > max_len:
        summary = summary[:max_len].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    return summary


def company_summary_cell(
    raw_summary: str,
    *,
    company: str = "",
    role: str = "",
    role_description: str = "",
    key_products: str = "",
    max_sentences: int = 3,
    max_len: int = 320,
) -> str:
    """Final Summary cell: clean crawl prose, else a sentence composed from the
    company's own classified fields. Never returns raw page/nav junk."""
    prose = summarize_for_csv(raw_summary, max_sentences=max_sentences, max_len=max_len)
    if prose:
        return prose

    rd = _one_line(role_description or "", max_len=200).strip().rstrip(".")
    kp = truncate_key_products(key_products or "", max_items=4, max_len=120)
    parts: list[str] = []
    if rd:
        if company and not rd.lower().startswith(company.lower()):
            parts.append(f"{company} - {rd}.")
        else:
            parts.append(f"{rd}.")
    elif company and role:
        parts.append(f"{company} is a {role.lower()} active in this market.")
    if kp and kp.lower() not in (rd or "").lower():
        parts.append(f"Key products: {kp}.")
    return _one_line(" ".join(parts), max_len=max_len).strip()


_PRESENCE_TRIGGERS = (
    "headquartered",
    "head office",
    "headquarters",
    "plant in",
    "facility in",
    "exports to",
    "exported to",
    "distributed in",
    "distribution in",
    "offices in",
    "operations in",
    "operating in",
    "manufacturing in",
    "presence in",
    "available in",
    "serves",
    "markets in",
    "based in",
)


def _flatten_page_text(smart_data: dict[str, Any] | None) -> str:
    if not smart_data:
        return ""
    parts: list[str] = []
    for page in smart_data.get("pages") or []:
        if isinstance(page, dict) and page.get("text"):
            parts.append(str(page["text"]))
    data = smart_data.get("data") or {}
    if isinstance(data, dict):
        parts.append(str(data))
    return " ".join(parts)


def extract_operational_presence(
    smart_data: dict[str, Any] | None,
    query_country: str,
    *,
    signals: dict[str, Any] | None = None,
    max_len: int = 300,
) -> str:
    """
    Extract geographic operational presence from crawl text.
    Falls back to signal-derived countries or query geography.
    """
    text = _flatten_page_text(smart_data)
    if not text:
        summary = extract_company_summary(smart_data, max_len=4000)
        text = summary

    sentences = re.split(r"(?<=[.!?])\s+", text)
    hits: list[str] = []
    for sent in sentences:
        low = sent.lower()
        if not any(t in low for t in _PRESENCE_TRIGGERS):
            continue
        clean = _one_line(sent, max_len=160)
        if clean and clean not in hits:
            hits.append(clean)
        if len(hits) >= 3:
            break

    if hits:
        return _one_line("; ".join(hits), max_len=max_len)

    sig = signals or {}
    mentioned = sig.get("mentioned_countries") or []
    if mentioned:
        return _one_line(
            "Markets mentioned on website: " + ", ".join(str(c) for c in mentioned[:8]),
            max_len=max_len,
        )

    hq = str(sig.get("hq_country") or "").strip()
    if hq:
        return _one_line(f"Headquartered in {hq}", max_len=max_len)

    qc = (query_country or "").strip()
    if qc and qc.lower() not in ("global", "worldwide", "international"):
        return f"Operations in {qc}"

    return "Not stated on website"


def format_data_sources(
    discovery_source: str,
    enrichment_source: str,
    smart_data: dict[str, Any] | None,
) -> str:
    """Human-readable provenance: search → website fetch method → URL."""
    parts: list[str] = []
    disc = (discovery_source or "").strip()
    if disc:
        parts.append(f"discovery={disc}")

    enrich = (enrichment_source or "").strip()
    if enrich:
        parts.append(f"enrichment={enrich}")

    if smart_data:
        inner = smart_data.get("source")
        if not inner and isinstance(smart_data.get("data"), dict):
            inner = (smart_data.get("data") or {}).get("source")
        if inner and str(inner) not in enrich:
            parts.append(f"fetch={inner}")

        pages = smart_data.get("pages") or []
        if pages and isinstance(pages[0], dict):
            url = str(pages[0].get("url") or "").strip()
            if url:
                parts.append(f"page={url[:160]}")

        if not enrich and smart_data.get("error"):
            parts.append(f"enrichment_note={smart_data.get('error')}")

    return " | ".join(parts) if parts else "unknown"
