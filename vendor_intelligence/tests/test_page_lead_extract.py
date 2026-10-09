"""Unit tests for HTML lead extraction (no Chrome required)."""
from vendor_intel.scraping.page_lead import extract_headings_and_lead


def test_extract_headings_and_lead_from_html():
    html = """
    <html><body>
    <h1>Acme Pharma India</h1>
    <h2>About us</h2>
    <p>We manufacture formulations and APIs in Mumbai.</p>
    <p>Leading pharmaceutical company in India.</p>
    </body></html>
    """
    out = extract_headings_and_lead(html, max_lines=10, max_chars=2000)
    assert "Acme Pharma India" in out
    assert "HEADINGS:" in out
    assert "LEAD:" in out or "manufacture" in out.lower()
