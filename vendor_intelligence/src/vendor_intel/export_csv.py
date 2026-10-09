"""Write pipeline results to CSV files (required pipeline output)."""
from __future__ import annotations

import csv
import re
from pathlib import Path

from vendor_intel.config import _project_root
from vendor_intel.models import PipelineResult


def _slug(text: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return (s[:max_len] if s else "run") or "run"


def default_output_dir() -> Path:
    return _project_root() / "output"


def run_output_dir(result: PipelineResult, base_dir: Path | None = None) -> Path:
    """Per-run folder: output/{run_id}_{query_slug}/"""
    root = base_dir or default_output_dir()
    folder = root / f"{result.run_id}_{_slug(result.query)}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def export_pipeline_csv(
    result: PipelineResult,
    output_dir: Path | None = None,
    *,
    base_dir: Path | None = None,
) -> dict[str, str]:
    """
    Write CSV deliverables for a completed run.

    Returns mapping of logical name → absolute file path.
    """
    folder = output_dir or run_output_dir(result, base_dir)

    paths: dict[str, str] = {}

    company_path = folder / "company_list.csv"
    _write_company_list(company_path, result)
    paths["company_list"] = str(company_path.resolve())

    parent_path = folder / "parent_group_list.csv"
    _write_parent_groups(parent_path, result)
    paths["parent_group_list"] = str(parent_path.resolve())

    suppressed_path = folder / "suppressed_brands.csv"
    _write_suppressed(suppressed_path, result)
    paths["suppressed_brands"] = str(suppressed_path.resolve())

    meta_path = folder / "run_summary.csv"
    _write_run_summary(meta_path, result)
    paths["run_summary"] = str(meta_path.resolve())

    return paths


def _write_company_list(path: Path, result: PipelineResult) -> None:
    # Columns ordered per user request:
    # industry → company → functionality → country → scores/evidence
    fieldnames = [
        "rank",
        "industry_name",
        "display_name",
        "company_type",           # functionality / role in industry
        "country_presence",       # geography with presence
        "primary_domain",
        "tier",
        "score",
        "discovery_score",
        "evidence_score",
        "scrape_score",
        "verification_score",
        "distinct_domains",
        "short_rationale",
        "company_description",
        "evidence_urls",
        "inclusion_reason",
        "inclusion_sources",
        "parent_group",
        "listing_mode",
        "funnel_levels_seen",
        "run_id",
        "query",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for i, c in enumerate(result.company_list, 1):
            w.writerow(
                {
                    "rank": i,
                    "industry_name": c.industry_name or result.query,
                    "display_name": c.display_name,
                    "company_type": c.company_type,
                    "country_presence": c.country_presence,
                    "primary_domain": c.primary_domain,
                    "tier": c.tier,
                    "score": round(c.score, 4),
                    "discovery_score": round(c.discovery_score, 4),
                    "evidence_score": round(c.evidence_score, 4),
                    "scrape_score": round(c.scrape_score, 4),
                    "verification_score": round(c.verification_score, 4),
                    "distinct_domains": c.distinct_domains,
                    "short_rationale": c.short_rationale,
                    "company_description": c.company_description,
                    "evidence_urls": " | ".join(c.evidence_urls),
                    "inclusion_reason": c.inclusion_reason,
                    "inclusion_sources": " | ".join(c.inclusion_sources),
                    "parent_group": c.parent_group,
                    "listing_mode": c.listing_mode,
                    "funnel_levels_seen": " | ".join(c.funnel_levels_seen),
                    "run_id": result.run_id,
                    "query": result.query,
                }
            )


def _write_parent_groups(path: Path, result: PipelineResult) -> None:
    fieldnames = [
        "run_id",
        "query",
        "parent_name",
        "brands_in_company_list",
        "listing_mode",
        "collapsed_from",
        "excluded_subsidiaries",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for p in result.parent_group_list:
            w.writerow(
                {
                    "run_id": result.run_id,
                    "query": result.query,
                    "parent_name": p.parent_name,
                    "brands_in_company_list": " | ".join(p.brands_in_company_list),
                    "listing_mode": p.listing_mode,
                    "collapsed_from": " | ".join(p.collapsed_from),
                    "excluded_subsidiaries": " | ".join(p.excluded_subsidiaries),
                }
            )


def _write_suppressed(path: Path, result: PipelineResult) -> None:
    fieldnames = ["run_id", "query", "name", "reason"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for s in result.suppressed_brands:
            w.writerow(
                {
                    "run_id": result.run_id,
                    "query": result.query,
                    "name": s.name,
                    "reason": s.reason,
                }
            )


def _write_run_summary(path: Path, result: PipelineResult) -> None:
    fieldnames = ["field", "value"]
    rows = [
        ("run_id", result.run_id),
        ("query", result.query),
        ("interpretation_summary", result.interpretation_summary),
        ("mock_mode", str(result.mock_mode)),
        ("company_count", str(len(result.company_list))),
        ("parent_group_count", str(len(result.parent_group_list))),
        ("suppressed_count", str(len(result.suppressed_brands))),
        ("warnings", "; ".join(result.warnings)),
        ("discovery_hits", str(result.counts.get("discovery_hits", ""))),
        ("entities_total", str(result.counts.get("entities_total", ""))),
        ("tier_a", str(result.counts.get("tier_a", ""))),
        ("tier_b", str(result.counts.get("tier_b", ""))),
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for field, value in rows:
            w.writerow({"field": field, "value": value})
