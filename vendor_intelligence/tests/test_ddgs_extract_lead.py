from vendor_intel.scraping.page_lead import lead_from_markdown


def test_lead_from_markdown_headings():
    md = """# Acme Mobile India

## About us

We design smartphones in Bangalore.

Leading 5G devices.
"""
    out = lead_from_markdown(md, max_lines=10, max_chars=2000)
    assert "Acme Mobile India" in out
    assert "HEADINGS:" in out
    assert "LEAD:" in out
    assert "Bangalore" in out
