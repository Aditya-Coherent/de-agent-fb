from AiAgentsFleet import sources


def snip(domain, text="Agricultural biostimulants market size was USD 4.3 billion in 2025"):
    return {"text": f"{text} ({domain})", "url": f"https://{domain}/page", "domain": domain}


JUNK = ["youtube.com", "m.youtube.com", "vkvideo.ru", "vk.com", "market.yandex.ru", "yandex.com.tr",
        "linkedin.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "tiktok.com",
        "pinterest.co.uk", "reddit.com", "quora.com", "hepsiburada.com", "amazon.de", "ebay.com",
        "aliexpress.ru", "astrohelper.ru", "scribd.com", "genera.so", "koji.so", "spa.so",
        "cheap.tk", "spam.xyz"]
GOOD = ["precedenceresearch.com", "grandviewresearch.com", "fao.org", "moa.gov.in", "usda.gov",
        "reuters.com", "ir.basf.com", "enaca.org", "vietnamnet.vn"]


def test_blocked_domains_are_dropped_with_reasons():
    kept, dropped = sources.filter_sources([snip(d) for d in JUNK + GOOD])
    assert {s["domain"] for s in kept} == set(GOOD)
    assert {d["domain"] for d in dropped} == set(JUNK)
    assert all(d["reason"] for d in dropped)


def test_link_farm_tld_blocked():
    assert "link-farm" in sources.block_reason("genera.so")
    assert "link-farm" in sources.block_reason("koji.so")
    assert sources.block_reason("example.com") is None
    assert sources.block_reason("x.com.example.org") is None     # x.com only as a real domain
    assert sources.block_reason("box.com") is None


def test_trusted_counted():
    kept, _ = sources.filter_sources([snip(d) for d in GOOD])
    q = sources.quality(kept)
    assert q["n"] == len(GOOD)
    assert q["distinct_domains"] == len(GOOD)
    # precedence, grandview, fao, gov.in, usda, reuters, ir.basf -- not enaca.org / vietnamnet.vn
    assert q["trusted"] == 7
    assert 0 <= q["score"] <= 1
    assert sources.trust_kind("agriculture.gouv.fr") == "government"
    assert sources.trust_kind("prnewswire.com") == "press_release"
    assert sources.trust_kind("youtube.com") is None


def test_sources_block_and_low_evidence():
    kept, dropped = sources.filter_sources([snip("youtube.com"), snip("enaca.org"), snip("vietnamnet.vn"),
                                            snip("mongabay.com")])
    block = sources.sources_block("market_size", kept, dropped)
    assert set(block) >= {"num_sources", "domains", "dropped_domains", "quality"}
    assert block["dropped_domains"] == ["youtube.com"]
    assert block["low_evidence"] is True                         # 3 kept but 0 trusted
    assert "low_evidence" not in sources.sources_block("drivers_restraints", kept, dropped)  # not numeric
    good, _ = sources.filter_sources([snip(d) for d in GOOD[:3]])
    assert "low_evidence" not in sources.sources_block("countries_mea", good, [])
    assert sources.sources_block("segments_global", good[:2], [])["low_evidence"] is True  # < 3 snippets


def test_on_topic_drops_dictionary_and_wordle_pages():
    market = "Global Vitamin C Market"
    junk = [{"text": "Regional: adjective. Relating to or characteristic of a region.",
             "domain": "dictionary.cambridge.org"},
            {"text": "REGIONAL definition and meaning", "domain": "merriam-webster.com"},
            {"text": "Today's Wordle answer and hints for the daily puzzle", "domain": "globle.wordleday.org"}]
    good = [{"text": "The vitamin C market in Europe was valued at USD 420 million", "domain": "fao.org"},
            {"text": "Asia Pacific Vitamin C demand grew 6% in 2025", "domain": "reuters.com"}]
    kept, off = sources.on_topic(junk + good, market)
    assert kept == good
    assert off == junk
    assert sources.market_tokens("Agricultural Biostimulants Market") == {"agricul", "biostim"}
    assert sources.on_topic(junk, "Global Market") == (junk, [])     # no usable stem: keep all
