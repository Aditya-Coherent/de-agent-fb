from vendor_intel.config import Settings
from vendor_intel.models import PipelineResult


def run_quality_gate(result: PipelineResult, settings: Settings) -> tuple[bool, list[str]]:
    issues: list[str] = []
    names = [c.display_name.lower() for c in result.company_list]
    if len(names) != len(set(names)):
        issues.append("duplicate_companies_in_list")
    if len(result.company_list) < settings.min_final_count:
        issues.append(f"below_min_count:{len(result.company_list)}<{settings.min_final_count}")

    for c in result.company_list:
        if not (c.inclusion_reason or c.short_rationale):
            issues.append(f"missing_inclusion_reason:{c.display_name}")
        if not c.inclusion_sources and not c.evidence_urls:
            issues.append(f"missing_sources:{c.display_name}")

    passed = "duplicate_companies_in_list" not in issues
    return passed, issues
