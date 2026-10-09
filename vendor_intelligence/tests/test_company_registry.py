"""Registry and quality filters."""
from __future__ import annotations

from vendor_intel.discovery.company_registry import (
    is_blocklisted_domain,
    is_registry_company,
    registry_domain_for_name,
    resolve_official_domain,
)
from vendor_intel.discovery.entity_extract import (
    is_plausible_company_name,
    is_validation_ready_name,
)


def test_registry_sun_pharma():
    assert registry_domain_for_name("Sun Pharma") == "sunpharma.com"


def test_registry_dr_reddys_over_franchise():
    dom = resolve_official_domain(
        "Dr. Reddy's",
        ["indiapharmafranchise.com", "drreddys.com"],
    )
    assert dom == "drreddys.com"


def test_blocklist_franchise():
    assert is_blocklisted_domain("indiapharmafranchise.com")


def test_reject_fashion_title():
    assert not is_plausible_company_name("Buy Shirts and Tops for Women Online in India")
    assert not is_validation_ready_name("Women's Tops", "zara.com")


def test_reject_home_gov():
    assert not is_validation_ready_name("Home", "pharma-dept.gov.in")


def test_registry_company_flag():
    assert is_registry_company("Cipla")
    assert not is_registry_company("Python Lists")


def test_reject_w3schools():
    assert is_blocklisted_domain("w3schools.com")
