import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vendor_intel.discovery.entity_extract import (
    domain_to_brand_name,
    extract_names_from_text,
    hits_from_search_result,
    is_listicle_or_article_title,
    looks_like_company_site,
)


class TestEntityExtract(unittest.TestCase):
    def test_listicle_title(self) -> None:
        self.assertTrue(is_listicle_or_article_title("Top 10 Pharmaceutical Companies in India"))

    def test_company_site(self) -> None:
        self.assertTrue(
            looks_like_company_site(
                "https://sunpharma.com/",
                "sunpharma.com",
                "Sun Pharma official site",
            )
        )

    def test_domain_brand(self) -> None:
        self.assertIn(
            domain_to_brand_name("cooperpharma.com"),
            ("Cooper Pharma", "Cooperpharma"),
        )

    def test_extract_from_snippet(self) -> None:
        text = "Sun Pharmaceutical Industries Limited leads the market. Dr. Reddy's Laboratories is second."
        names = extract_names_from_text(text)
        self.assertTrue(any("Sun" in n for n in names))
        self.assertTrue(any("Reddy" in n for n in names))

    def test_listicle_yields_companies(self) -> None:
        scope = {"geographies": ["India"], "market": "pharmaceutical companies"}
        extracted = hits_from_search_result(
            "Top 10 Pharma Companies in India",
            "https://example.com/top-pharma",
            "Cipla and Sun Pharma are among the top firms in India.",
            prompt_id="P1",
            funnel_level="",
            backend="duckduckgo",
            search_theme="pharma India",
            scope=scope,
        )
        names = {e.name for e in extracted}
        self.assertTrue(names)


if __name__ == "__main__":
    unittest.main()
