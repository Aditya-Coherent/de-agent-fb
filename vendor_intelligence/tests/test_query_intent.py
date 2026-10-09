"""Unit tests for generic query parsing and prompt generation."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vendor_intel.funnel.prompt_builder import (
    build_competitor_search_prompts,
    build_discovery_prompts,
    build_funnel_prompts,
    refine_search_topic,
    topic_variants,
)
from vendor_intel.funnel.query_intent import (
    enrich_scope_from_query,
    extract_anchor_company_from_query,
    parse_query_parts,
)


class TestQueryIntent(unittest.TestCase):
    def test_geo_comma(self):
        market, geo = parse_query_parts(
            "leading artisanal wasabi paste producers in Shizuoka Prefecture, Japan"
        )
        self.assertIn("wasabi", market.lower())
        self.assertIn("japan", geo.lower())

    def test_refine_drops_ambiguous_modifier(self):
        topic = refine_search_topic("modular liquid cooling vendors for data centers")
        self.assertNotIn("modular", topic.lower())

    def test_refine_keeps_specific_tokens(self):
        topic = refine_search_topic("industrial kelp harvesting equipment suppliers")
        self.assertIn("kelp", topic.lower())
        self.assertIn("equipment", topic.lower())

    def test_no_duplicate_suppliers(self):
        funnel = build_funnel_prompts(
            "industrial kelp harvesting equipment suppliers", "Nova Scotia, Canada"
        )
        self.assertLessEqual(funnel[1]["text"].lower().count("suppliers"), 1)

    def test_discovery_includes_competitors(self):
        disc = build_discovery_prompts(
            "pharmaceutical companies", "India", anchor_company=None
        )
        joined = " ".join(p["text"] for p in disc).lower()
        self.assertGreaterEqual(len(disc), 7)
        self.assertIn("competitors", joined)

    def test_anchor_company_from_query(self):
        anchor = extract_anchor_company_from_query(
            "competitors of Sun Pharma in India"
        )
        self.assertIsNotNone(anchor)
        self.assertIn("sun", anchor.lower())

    def test_anchor_competitor_prompts(self):
        prompts = build_competitor_search_prompts(
            "pharmaceutical companies", "India", anchor_company="Sun Pharma"
        )
        joined = " ".join(prompts).lower()
        self.assertIn("competitors", joined)
        self.assertIn("sun", joined)

    def test_funnel_l2_competitors(self):
        funnel = build_funnel_prompts("pharmaceutical companies", "India")
        self.assertIn("competitors", funnel[2]["text"].lower())

    def test_variants_from_market_only(self):
        variants = topic_variants("modular liquid cooling for data centers", "Iceland")
        joined = " ".join(variants).lower()
        self.assertNotIn("modular", joined)

    def test_enrich_search_topic(self):
        scope = enrich_scope_from_query(
            {}, "best Eri silk handloom yarn exporters in Assam, India"
        )
        self.assertIn("search_topic", scope)
        self.assertEqual(scope["geographies"], ["Assam, India"])


if __name__ == "__main__":
    unittest.main()
