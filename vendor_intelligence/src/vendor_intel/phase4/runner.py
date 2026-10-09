"""Phase 4 — export CSV from Phase 3 validation JSON."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

# ---------------------------------------------------------------------------
# Human-readable company function labels (used in company_type CSV column)
# ---------------------------------------------------------------------------
_FUNCTION_LABELS: dict[str, str] = {
    "manufacturer": "Manufacturer",
    "oem_manufacturer": "OEM Manufacturer",
    "contract_manufacturer": "Contract Manufacturer",
    "distributor": "Distributor",
    "wholesaler": "Wholesaler",
    "importer": "Importer",
    "exporter": "Exporter",
    "retailer": "Retailer / Dealer",
    "service_provider": "IT / Managed Service Provider",
    "mssp": "Managed Security Service Provider (MSSP)",
    "msp": "Managed Service Provider (MSP)",
    "system_integrator": "System Integrator (SI)",
    "var": "Value-Added Reseller (VAR)",
    "consulting": "Consulting Firm",
    "cro": "Contract Research Organization (CRO)",
    "software_vendor": "Software / SaaS Vendor",
    "vendor": "Product Vendor",  # CHANGED: neutral label (not pharma-specific "Security Vendor")
    "brand": "Brand / OEM",
    "market_leader": "Market Leader",
    "landscape": "Industry Player",
    "unknown": "Industry Player",
    "unclear": "Role unclear — needs validation",
}


def _infer_label_from_name(name: str) -> str | None:
    low = (name or "").lower()
    if re.search(r"\blaborator", low):
        return "Manufacturer"
    if re.search(r"\bcontract\s+research\b|\bcro\b", low):
        return "Contract Research Organization (CRO)"
    if re.search(r"\bdistribut", low):
        return "Distributor"
    if re.search(r"\bwholesal", low):
        return "Wholesaler"
    if re.search(r"\bexport", low):
        return "Exporter"
    return None


def _function_label(company_function: str, company_name: str = "") -> str:
    """Map internal company_function code → readable CSV label."""
    code = str(company_function or "").lower()
    if code in ("unknown", "unclear", "vendor", "landscape"):
        inferred = _infer_label_from_name(company_name)
        if inferred:
            return inferred
    return _FUNCTION_LABELS.get(code, "Industry Player")


def _build_company_type_label(row: dict) -> str:
    """Build a compound, human-readable company_type from all discovered functions."""
    display = str(row.get("canonical_name") or row.get("display_name") or "")
    all_fns: list[str] = list(row.get("discovered_functions") or [])
    primary_fn = str(row.get("company_function") or "unknown")

    if not all_fns:
        all_fns = [primary_fn] if primary_fn and primary_fn != "unknown" else []

    if not all_fns:
        return _function_label(str(row.get("company_type") or "unknown"), display)

    seen: set[str] = set()
    deduped: list[str] = []
    for fn in all_fns:
        if fn not in seen and fn not in ("unknown", ""):
            seen.add(fn)
            deduped.append(fn)
    deduped = deduped[:3]

    if not deduped:
        return _function_label(primary_fn, display)

    fn_set = set(deduped)
    if fn_set >= {"mssp", "consulting"} or (primary_fn == "mssp" and "consulting" in fn_set):
        return "Managed Security Service Provider (MSSP — Risk / Compliance / Advisory)"
    if primary_fn == "service_provider" and "mssp" in fn_set:
        return "IT / Managed Security Service Provider (MSSP)"

    return " + ".join(_function_label(fn, display) for fn in deduped)

from vendor_intel.config import Settings, _project_root
from vendor_intel.discovery.company_registry import (
    is_blocklisted_domain,
    is_registry_company,
    registry_domain_for_name,
    set_registry_scope,
)
from vendor_intel.discovery.entity_extract import (
    is_generic_category_name,
    is_likely_real_company_name,
)
from vendor_intel.export_csv import export_pipeline_csv
from vendor_intel.models import CompanyListItem, ParentGroupItem, PipelineResult, SuppressedBrand


def load_phase3_validation(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        if not p.is_absolute():
            alt = _project_root() / p
            if alt.is_file():
                p = alt
        if not p.is_file():
            plans_dir = _project_root() / "output" / "phase3"
            available = (
                sorted(plans_dir.glob("phase3_validation_*.json"))
                if plans_dir.is_dir()
                else []
            )
            hint = ""
            if available:
                hint = "\n  Available validation files:\n" + "\n".join(
                    f"    {f.relative_to(_project_root())}" for f in available
                )
            raise FileNotFoundError(f"Phase 3 validation file not found: {path}{hint}")
    plan = json.loads(p.read_text(encoding="utf-8"))
    if plan.get("phase") != 3:
        raise ValueError(f"Not a Phase 3 file (phase={plan.get('phase')}): {p}")
    return plan


def _slug_from_scope(scope: dict[str, Any], query: str) -> str:
    slug_base = f"{scope.get('market', query)}_{(scope.get('geographies') or [''])[0]}"
    return re.sub(r"[^a-z0-9]+", "_", slug_base.lower())[:56].strip("_") or "run"


def build_pipeline_result_from_phase3(
    manifest: dict[str, Any],
    settings: Settings,
    *,
    tiers: tuple[str, ...] = ("A", "B"),
    quality_filter: bool = True,
) -> PipelineResult:
    query = str(manifest.get("query") or "").strip()
    scope = dict(manifest.get("scope") or {})
    set_registry_scope(scope)
    max_count = int(scope.get("max_final_count") or settings.max_final_count)
    min_count = int(scope.get("min_final_count") or settings.min_final_count)

    # Scope metadata for CSV columns
    industry_name = str(scope.get("industry_vertical") or scope.get("market") or "")
    country_presence = ", ".join(scope.get("geographies") or []) or "Global"

    rows = list(manifest.get("validated_entities") or [])
    selected: list[dict[str, Any]] = []
    suppressed: list[SuppressedBrand] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("canonical_name") or "").strip()
        tier = str(row.get("tier") or "C")
        domain = str(row.get("primary_domain") or "")
        gates = row.get("gate_pass") or {}
        op_ok = bool(gates.get("operational"))
        prod_ok = bool(gates.get("product"))
        export_tier = tier in tiers

        if export_tier:
            if quality_filter and (
                is_generic_category_name(name)
                or not is_likely_real_company_name(name, domain)
                or is_blocklisted_domain(domain)
            ):
                suppressed.append(
                    SuppressedBrand(
                        name=name,
                        reason="junk_candidate_name"
                        if not is_likely_real_company_name(name, domain)
                        else "blocklisted_domain",
                    )
                )
                continue
            selected.append(row)
        else:
            reason = str(row.get("suppression_reason") or f"tier_{tier.lower()}")
            suppressed.append(SuppressedBrand(name=name, reason=reason))

    selected.sort(
        key=lambda r: (
            0 if r.get("tier") == "A" else 1,
            -float(r.get("composite_score") or 0),
            -int(r.get("discovery_count") or 0),
        )
    )

    from vendor_intel.utils.domains import company_dedupe_key

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in selected:
        key = company_dedupe_key(str(row.get("canonical_name") or ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    unique = unique[:max_count]

    company_list: list[CompanyListItem] = []
    for row in unique:
        urls = list(row.get("sample_evidence_urls") or [])
        gates = row.get("gate_pass") or {}
        passed = [g for g, ok in gates.items() if ok]
        bd = row.get("score_breakdown") if isinstance(row.get("score_breakdown"), dict) else {}
        # Derive readable company type — uses all discovered_functions for multi-label
        readable_fn = _build_company_type_label(row)

        company_list.append(
            CompanyListItem(
                display_name=str(row.get("canonical_name") or ""),
                parent_group=str(row.get("parent_group") or row.get("canonical_name") or ""),
                primary_domain=str(row.get("primary_domain") or ""),
                listing_mode="SINGLE_BRAND",
                score=float(row.get("composite_score") or 0),
                discovery_score=float(bd.get("discovery_score") or 0),
                evidence_score=float(bd.get("evidence_score") or 0),
                scrape_score=float(bd.get("scrape_score") or 0),
                verification_score=float(
                    bd.get("verification_score") or row.get("verification_confidence") or 0
                ),
                tier=str(row.get("tier") or "B"),
                distinct_domains=int(row.get("distinct_domains") or 0),
                evidence_urls=urls,
                short_rationale=f"Tier {row.get('tier')} — gates: {', '.join(passed) or 'n/a'}",
                company_type=readable_fn,
                industry_name=industry_name,
                country_presence=country_presence,
                inclusion_reason=f"Phase 3 validation tier {row.get('tier')}",
                inclusion_sources=urls,
                company_description=str(row.get("company_description") or "")[:2000],
                funnel_levels_seen=[],
            )
        )

    parent_group_list = [
        ParentGroupItem(
            parent_name=c.parent_group,
            brands_in_company_list=[c.display_name],
            listing_mode="SINGLE_BRAND",
        )
        for c in company_list
    ]

    warnings: list[str] = []
    if len(company_list) < min_count:
        warnings.append(
            f"Only {len(company_list)} companies in CSV (target {min_count}). No padding."
        )

    tier_a = sum(1 for c in company_list if c.tier == "A")
    tier_b = sum(1 for c in company_list if c.tier == "B")

    return PipelineResult(
        run_id=str(manifest.get("run_id") or uuid4()),
        query=query,
        interpretation_summary=str(scope.get("interpretation_summary") or ""),
        company_list=company_list,
        parent_group_list=parent_group_list,
        suppressed_brands=suppressed,
        counts={
            "input_validated": len(rows),
            "company_list_returned": len(company_list),
            "tier_a": tier_a,
            "tier_b": tier_b,
            "suppressed": len(suppressed),
        },
        audit_manifest={
            "phase": 4,
            "source": manifest.get("output_path"),
            "phase2_discovery_path": manifest.get("phase2_discovery_path"),
            "exported_at": datetime.now(timezone.utc).isoformat(),
        },
        warnings=warnings,
        mock_mode=bool(manifest.get("mock_mode")),
    )


def run_phase4_export(
    phase3_validation_path: str | Path,
    settings: Settings | None = None,
    *,
    csv_dir: str | Path | None = None,
    quality_filter: bool = True,
) -> dict[str, Any]:
    settings = settings or Settings.load()
    manifest = load_phase3_validation(phase3_validation_path)
    result = build_pipeline_result_from_phase3(
        manifest, settings, quality_filter=quality_filter
    )

    base = Path(csv_dir or settings.csv_output_dir)
    if not base.is_absolute():
        base = _project_root() / base

    if not settings.csv_output_enabled:
        return {
            "phase": 4,
            "query": result.query,
            "csv_enabled": False,
            "company_count": len(result.company_list),
            "warnings": ["CSV_OUTPUT_ENABLED=false — no files written"],
        }

    paths = export_pipeline_csv(result, base_dir=base)
    result.csv_paths = paths

    out_manifest: dict[str, Any] = {
        "phase": 4,
        "query": result.query,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "phase3_validation_path": str(Path(phase3_validation_path).resolve()),
        "company_count": len(result.company_list),
        "tier_a": result.counts.get("tier_a", 0),
        "tier_b": result.counts.get("tier_b", 0),
        "suppressed_count": len(result.suppressed_brands),
        "csv_paths": paths,
        "warnings": result.warnings,
    }

    slug = _slug_from_scope(manifest.get("scope") or {}, result.query)
    out_dir = _project_root() / "output" / "phase4"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_path = out_dir / f"phase4_export_{slug}.json"
    meta_path.write_text(json.dumps(out_manifest, indent=2), encoding="utf-8")
    out_manifest["output_path"] = str(meta_path.resolve())
    return out_manifest


def print_phase4_summary(manifest: dict[str, Any]) -> None:
    print("\n=== Phase 4 complete (CSV export) ===")
    print(f"  Manifest: {manifest.get('output_path')}")
    print(f"  From Phase 3: {manifest.get('phase3_validation_path')}")
    print(f"  Companies in CSV: {manifest.get('company_count', 0)}")
    print(f"  Tier A: {manifest.get('tier_a', 0)} | Tier B: {manifest.get('tier_b', 0)}")
    print(f"  Suppressed (not in company_list.csv): {manifest.get('suppressed_count', 0)}")
    print("\n  CSV files:")
    for name, path in (manifest.get("csv_paths") or {}).items():
        print(f"    {name}: {path}")
    for w in manifest.get("warnings") or []:
        print(f"  Warning: {w}")
