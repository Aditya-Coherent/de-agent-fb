"""Company market role classification (rules + text signals)."""
from __future__ import annotations

import re
from enum import Enum

from vendor_intel.models import Entity, RunConfig


class CompanyType(str, Enum):
    MANUFACTURER = "Manufacturer"
    DISTRIBUTOR = "Distributor"
    RETAILER = "Retailer"
    ODM_OEM = "ODM/OEM"
    COMPONENT = "Component supplier"
    UNKNOWN = "Unknown"


RETAILER_NAMES = {
    "croma", "reliance digital", "amazon", "flipkart", "walmart", "best buy",
}
COMPONENT_NAMES = {"intel", "amd", "qualcomm", "nvidia", "mediatek"}
DISTRIBUTOR_KEYWORDS = ("distributor", "distribution", "wholesale", "authorised dealer")
RETAILER_KEYWORDS = ("retailer", "retail chain", "e-commerce", "online store", "showroom")
MANUFACTURER_KEYWORDS = ("manufacturer", "manufacturing", "oem", "odm", "factory", "product lineup")
ODM_KEYWORDS = ("contract manufacturing", "white label", "odm", "oem partner")


def target_types_from_scope(scope: dict) -> set[str]:
    q = (scope.get("interpretation_summary") or "") + " " + str(scope.get("market") or "")
    intent = str(scope.get("intent") or "").lower()
    combined = q.lower()
    if "manufacturer" in combined or "oem" in combined or intent == "competitor_set":
        return {CompanyType.MANUFACTURER.value, CompanyType.ODM_OEM.value}
    if "retailer" in combined:
        return {CompanyType.RETAILER.value}
    return {
        CompanyType.MANUFACTURER.value,
        CompanyType.ODM_OEM.value,
        CompanyType.DISTRIBUTOR.value,
    }


def _text_blob(entity: Entity) -> str:
    parts = [entity.canonical_name, entity.scraped_text]
    for items in entity.gates.values():
        for it in items:
            parts.append(it.snippet)
    return " ".join(parts).lower()


def classify_entity(entity: Entity) -> str:
    name = entity.canonical_name.lower()
    if name in RETAILER_NAMES or any(k in name for k in (" croma", "amazon", "flipkart")):
        return CompanyType.RETAILER.value
    if name in COMPONENT_NAMES:
        return CompanyType.COMPONENT.value

    text = _text_blob(entity)
    if any(k in text for k in ODM_KEYWORDS):
        return CompanyType.ODM_OEM.value
    if any(k in text for k in DISTRIBUTOR_KEYWORDS):
        return CompanyType.DISTRIBUTOR.value
    if any(k in text for k in RETAILER_KEYWORDS):
        return CompanyType.RETAILER.value
    if any(k in text for k in MANUFACTURER_KEYWORDS) or entity.scraped_text:
        return CompanyType.MANUFACTURER.value
    return CompanyType.UNKNOWN.value


def filter_entities_by_intent(
    entities: list[Entity],
    config: RunConfig,
) -> list[Entity]:
    scope = config.scope or {}
    targets = target_types_from_scope(scope)
    query = str(scope.get("interpretation_summary") or "")
    if "manufacturer" not in query.lower() and "oem" not in query.lower():
        if not scope.get("target_company_types"):
            return entities

    out: list[Entity] = []
    for e in entities:
        if e.company_type in targets or e.company_type == CompanyType.UNKNOWN.value:
            out.append(e)
        else:
            e.excluded_from_company_list = True
            e.suppression_reason = f"company_type:{e.company_type}"
    return out
