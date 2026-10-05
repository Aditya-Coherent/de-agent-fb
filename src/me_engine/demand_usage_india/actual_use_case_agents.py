"""Actual Use Case discovery (India-only) -- the one genuinely new LLM node
this package adds.

For each Major Use Case bucket already discovered by
`me_engine.demandUseCaseCountry_approach2B.agents.usage_decomposition_node` (reused unchanged),
this module discovers India-specific "Actual Use Cases" -- concrete,
evidence-grounded demand/consumption pathways nested one level under that
Major Use Case, never a generic segmentation dimension (Product, Source,
Flavor, Grade, Form, Channel, B2B/B2C, Region, Country -- the Step-4
guardrail). Same two-pass discipline the rest of this pipeline already uses
everywhere else: a deterministic regex-first check that can force a
rejection on its own, then an LLM classification pass as the second,
independent check -- so the LLM is never the only protection.

Evidence is gathered BEFORE candidates are finalized (same ordering
`usage_decomposition_node` already follows) via
`me_engine.generation.web_evidence.gather`, unchanged, with India-qualified
queries.
"""
from __future__ import annotations

import json
import re
from typing import Any

from me_engine.generation.llm import GenLLM
from me_engine.generation.web_evidence import gather

# Step-4 guardrail (verbatim from the spec): these are traditional
# SEGMENTATION dimensions, not genuine demand/consumption pathways. A
# candidate "Actual Use Case" that is really just a rewording of one of these
# is rejected -- market-agnostic, no vocabulary tied to any one market's own
# product names.
_SEGMENTATION_DIMENSION_TERMS = re.compile(
    r"\b("
    r"product\s+type|by\s+product|source(?:\s+type)?|flavor|flavour|grade|form(?:at)?|"
    r"channel|distribution\s+channel|b2b|b2c|business[\s-]to[\s-]business|business[\s-]to[\s-]consumer|"
    r"region(?:al)?|country|geograph\w*"
    r")\b",
    re.IGNORECASE,
)

_EXCLUSION_LIST_TEXT = (
    "Product, Source, Flavor, Grade, Form, Channel, B2B/B2C, Region, Country"
)


def _evidence_brief(snippets: list[str], limit: int = 10) -> str:
    if not snippets:
        return "(no external evidence available)"
    return "WEB EVIDENCE:\n" + "\n".join(f"- {s}" for s in snippets[:limit])


def _deterministic_segmentation_hit(name: str, description: str) -> str | None:
    m = _SEGMENTATION_DIMENSION_TERMS.search(f"{name} {description}")
    return m.group(0) if m else None


def india_actual_use_case_node(state: dict[str, Any]) -> dict[str, Any]:
    """For each retained Major Use Case bucket, one LLM call proposing
    candidate Actual Use Cases nested under it, India-scoped web evidence
    first, then a validation pass (deterministic segmentation-dimension
    check + LLM genuine-demand-pathway classification) before any candidate
    is accepted. Returns `actual_use_cases` -- the flattened leaf-bucket list
    Phase 3 already expects (`name`, `description`, `bucket_category`), each
    additionally tagged `major_use_case` (inert to every existing Phase 3
    node -- they only read the first three fields)."""
    cfg = state["config"]
    llm: GenLLM = state["llm"]
    market = state["market_name"]
    log = state.setdefault("log", [])
    major_use_cases = state.get("usage_buckets", [])
    emitter = state.get("emitter")
    if emitter is not None:
        emitter.emit("india_actual_use_case", "stage_start", data={"node": "india_actual_use_case_node"})

    actual_use_cases: list[dict[str, Any]] = []
    rejected_candidates: list[dict[str, Any]] = []

    for major in major_use_cases:
        major_name = major["name"]
        major_desc = major.get("description", "")

        snippets: list[str] = []
        if cfg.enable_web:
            try:
                snippets = gather([
                    f"{market} {major_name} India applications",
                    f"{market} {major_name} India end use",
                    f"{market} {major_name} India demand drivers",
                ], per_query=5, cap=15)
            except Exception as exc:  # noqa: BLE001
                log.append(f"[india_actual_use_case] could not fetch web evidence for '{major_name}': {exc}")

        system = (
            "You are a demand-decomposition analyst specializing in the INDIAN market. Given a "
            f"Major Use Case of '{market}', discover the concrete, REAL Actual Use Cases through which "
            "India specifically consumes/applies this Major Use Case -- distinct, genuine demand/"
            "consumption pathways, evidence-grounded wherever possible.\n\n"
            "CRITICAL guardrail: an Actual Use Case must be a genuine demand PATHWAY (why/where the "
            "product is actually used), never a traditional SEGMENTATION DIMENSION. Do NOT propose "
            f"anything that is really just one of these dimensions reworded: {_EXCLUSION_LIST_TEXT}. "
            "For example, for a Crude Oil market's 'Transportation Fuels' Major Use Case, valid Actual "
            "Use Cases in India might be 'Two-Wheeler and Passenger Vehicle Fuel Demand' or 'Freight "
            "and Commercial Trucking Fuel Demand' -- NOT 'Petrol' / 'Diesel' (that is a Product/Grade "
            "split), and NOT 'North India' / 'South India' (that is a Region split). For a Vinegar "
            "market's 'Food Preservation' Major Use Case, a valid Actual Use Case is 'Household Pickle "
            "(Achaar) Preservation' -- NOT 'Bottled' vs. 'Bulk' (Form/Channel), NOT 'Retail' vs. "
            "'Institutional' (Channel/B2B-B2C).\n\n"
            "Prefer 2-5 Actual Use Cases per Major Use Case, each specific enough to be distinguished "
            "from every other one."
        )
        user = (
            f"Market: {market}\nMajor Use Case: {major_name} -- {major_desc}\n\n"
            f"{_evidence_brief(snippets, limit=15)}\n\n"
            'Return JSON: {"actual_use_cases": [{"name": "<short label>", "description": '
            '"<1-2 sentences, India-specific, distinct from every other Actual Use Case>", '
            '"evidence_type": "<evidence_backed|reasoning_only>", "evidence_snippet": '
            '"<quoted phrase or empty string>"}]}'
        )
        llm_error: str | None = None
        try:
            data = llm.complete_json(system, user)
        except Exception as exc:  # noqa: BLE001
            log.append(f"[india_actual_use_case] LLM call failed for '{major_name}': {exc}")
            data = {}
            llm_error = str(exc)
        if emitter is not None:
            emitter.emit(
                "india_actual_use_case", "llm_call", bucket=major_name,
                llm={"prompt": f"{system}\n\n{user}", "response": json.dumps(data, ensure_ascii=False) if data else "", "error": llm_error},
                status="failed" if llm_error else "success",
            )

        candidates = []
        for c in data.get("actual_use_cases", []) or []:
            name = str(c.get("name") or "").strip()
            desc = str(c.get("description") or "").strip()
            if not name:
                continue
            evidence_type = str(c.get("evidence_type") or "").strip()
            if evidence_type not in ("evidence_backed", "reasoning_only"):
                evidence_type = "reasoning_only"
            evidence_snippet = str(c.get("evidence_snippet") or "").strip() if evidence_type == "evidence_backed" else ""
            candidates.append({
                "name": name, "description": desc or name,
                "evidence_type": evidence_type, "evidence_snippet": evidence_snippet,
            })
        if not candidates:
            # Never leave a Major Use Case with zero Actual Use Cases -- fall
            # back to treating the Major Use Case itself as its own single
            # Actual Use Case, so it still flows through Phase 3.
            candidates = [{
                "name": major_name, "description": major_desc,
                "evidence_type": "reasoning_only", "evidence_snippet": "",
            }]
            log.append(
                f"[india_actual_use_case] LLM returned no Actual Use Cases for '{major_name}' -- "
                "falling back to the Major Use Case itself as a single Actual Use Case."
            )

        # Step-4 guardrail, deterministic pass first (never solely LLM-gated).
        det_hits: dict[str, str] = {}
        for c in candidates:
            hit = _deterministic_segmentation_hit(c["name"], c["description"])
            if hit:
                det_hits[c["name"]] = hit

        # LLM classification pass -- same two-pass discipline
        # `usage_validation_node` already uses (deterministic first, LLM
        # second, deterministic wins on conflict).
        class_system = (
            "You are a demand-methodology auditor. For EACH candidate 'Actual Use Case' below, decide "
            "whether it is a genuine demand/consumption PATHWAY (a real reason/context in which the "
            "product is used) or actually just a traditional SEGMENTATION DIMENSION reworded -- "
            f"{_EXCLUSION_LIST_TEXT}. This must work for ANY market -- judge on general principle."
        )
        class_user = (
            json.dumps([{"name": c["name"], "description": c["description"]} for c in candidates], ensure_ascii=False)
            + '\n\nReturn JSON: {"verdicts": [{"name": "<same name>", "is_genuine_use_case": <true|false>, '
            '"reason": "<1 sentence>"}]}'
        )
        try:
            class_data = llm.complete_json(class_system, class_user)
            verdicts = {str(v.get("name", "")).strip(): v for v in (class_data.get("verdicts") or [])}
        except Exception as exc:  # noqa: BLE001
            log.append(f"[india_actual_use_case] classification LLM call failed for '{major_name}': {exc}")
            verdicts = {}

        for c in candidates:
            name = c["name"]
            det_hit = det_hits.get(name)
            llm_verdict = verdicts.get(name, {})
            llm_genuine = bool(llm_verdict.get("is_genuine_use_case", True))  # absence of a verdict must
            # not silently reject a legitimate candidate -- only an explicit
            # false, or a deterministic hit, rejects it.
            if det_hit or not llm_genuine:
                reason = det_hit and f"matched generic segmentation-dimension term: '{det_hit}'" or str(
                    llm_verdict.get("reason") or "classified as a segmentation dimension, not a demand pathway"
                )
                rejected_candidates.append({
                    "name": name, "description": c["description"], "major_use_case": major_name,
                    "reason": reason, "deterministic_flag": det_hit,
                })
                log.append(
                    f"[india_actual_use_case] rejected candidate '{name}' under '{major_name}' -- {reason}"
                )
                continue
            actual_use_cases.append({
                "name": name,
                "description": c["description"],
                "bucket_category": "physical_consumption",
                "major_use_case": major_name,
                "provenance": {
                    "evidence_type": c["evidence_type"],
                    "evidence_snippet": c["evidence_snippet"],
                },
            })

    if not actual_use_cases:
        # Never leave the pipeline with zero leaf buckets -- fall back to
        # treating every Major Use Case as its own Actual Use Case.
        log.append(
            "[india_actual_use_case] every candidate was rejected -- falling back to one Actual Use "
            "Case per Major Use Case."
        )
        actual_use_cases = [
            {
                "name": m["name"], "description": m.get("description", ""),
                "bucket_category": "physical_consumption", "major_use_case": m["name"],
                "provenance": {"evidence_type": "reasoning_only", "evidence_snippet": ""},
            }
            for m in major_use_cases
        ]

    log.append(
        f"Discovered {len(actual_use_cases)} India Actual Use Case(s) across {len(major_use_cases)} "
        f"Major Use Case(s); {len(rejected_candidates)} candidate(s) rejected as segmentation "
        "dimensions/non-genuine pathways."
    )
    if emitter is not None:
        emitter.emit(
            "india_actual_use_case", "stage_end",
            data={"node": "india_actual_use_case_node", "num_actual_use_cases": len(actual_use_cases)},
        )
    return {"actual_use_cases": actual_use_cases, "rejected_actual_use_case_candidates": rejected_candidates}
