"""
Adaptive discovery — yield tracking, query cache, sector-tree expansion.

Stop on low query productivity (not raw company count alone).
"""
from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from vendor_intel.discovery.discovery_query_quality import (
    is_listicle_discovery_query,
    sanitize_discovery_query,
)
from vendor_intel.funnel.prompt_builder import _q, geo_search_label, refine_search_topic

_MUTATION_SUFFIXES: tuple[str, ...] = (
    "headquarters official website",
    "corporate site GMP certified",
    "company profile ISO",
    "official website registered",
    "manufacturing plant facility",
)

_SUB_SECTOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "manufacturers": ("manufacturer", "formulation", "generics", "plant"),
    "api_manufacturers": ("api", "bulk drug", "active pharmaceutical"),
    "cdmo": ("contract manufacturing", "cdmo", "cmo", "gmp"),
    "exporters": ("exporter", "export", "who gmp"),
    "distributors": ("distributor", "authorized distributor", "wholesale"),
    "biotech": ("biotech", "biosimilar", "r&d"),
    "cyber_vendors": ("security vendor", "endpoint", "siem", "mssp"),
    "integrators": ("system integrator", "managed security"),
    "contractors": ("contractor", "installer", "fit-out", "erection"),
    "competitors": ("competitor", "alternatives", "competitive landscape"),
    "general": (),
}

_FACADE_HIGH_YIELD_EXPANSIONS: dict[str, list[tuple[str, ...]]] = {
    "manufacturers": (
        ("curtain wall", "manufacturer", "official", "site"),
        ("rainscreen", "cladding", "manufacturer", "corporate"),
        ("aluminium composite panel", "producer", "official"),
    ),
    "distributors": (
        ("authorized", "cladding", "distributor", "firm"),
        ("architectural", "cladding", "supplier", "official", "website"),
    ),
    "integrators": (
        ("facade", "system", "integrator", "official", "site"),
        ("building envelope", "contractor", "corporate", "website"),
    ),
    "general": (
        ("aluminium", "facade", "manufacturer", "official"),
        ("cladding", "fabricator", "corporate", "site"),
    ),
}

_CHEM_HIGH_YIELD_EXPANSIONS: dict[str, list[tuple[str, ...]]] = {
    "manufacturers": (
        ("bio based ethylene", "producer", "official", "site"),
        ("renewable ethylene", "manufacturing", "plant"),
        ("green ethylene", "corporate", "website"),
    ),
    "cdmo": (
        ("contract manufacturing", "ethylene", "GMP"),
        ("chemical", "CDMO", "official", "site"),
    ),
    "exporters": (
        ("ethylene", "exporter", "certified", "company"),
        ("petrochemical", "export", "corporate", "site"),
    ),
    "distributors": (
        ("authorized", "chemical", "distributor", "firm"),
        ("ethylene", "supplier", "official", "website"),
    ),
    "integrators": (
        ("bio based", "chemical", "technology", "provider"),
        ("polymer", "producer", "official", "site"),
    ),
    "general": (
        ("ethylene", "plant", "facility", "operator"),
        ("bioethylene", "producer", "corporate"),
    ),
}

_PHARMA_HIGH_YIELD_EXPANSIONS: dict[str, list[tuple[str, ...]]] = {
    "api_manufacturers": (
        ("bulk drug manufacturers", "GMP official site"),
        ("active pharmaceutical ingredients", "manufacturer"),
        ("API GMP plants", "corporate website"),
        ("API exporters", "WHO GMP certified"),
    ),
    "cdmo": (
        ("contract manufacturing organization", "GMP"),
        ("third party pharma manufacturing", "CDMO"),
        ("CMO pharmaceutical", "official website"),
    ),
    "exporters": (
        ("pharmaceutical export company", "corporate site"),
        ("WHO GMP pharma exporter", "official"),
    ),
    "manufacturers": (
        ("formulation pharmaceutical", "manufacturer official"),
        ("finished dosage forms", "manufacturer"),
    ),
    "distributors": (
        ("pharmaceutical wholesale distributor", "authorized"),
        ("C&F pharma agent", "official firm"),
    ),
    "biotech": (
        ("biosimilar manufacturer", "official site"),
        ("biotech pharma pipeline", "company"),
    ),
}

_GENERIC_TOPIC_EXPANSIONS: dict[str, list[tuple[str, ...]]] = {
    "manufacturers": (
        ("manufacturer", "official", "site"),
        ("manufacturing", "company", "corporate", "website"),
    ),
    "distributors": (
        ("authorized", "distributor", "official", "firm"),
        ("supplier", "corporate", "website"),
    ),
    "exporters": (
        ("exporter", "corporate", "site"),
        ("export", "company", "official"),
    ),
    "integrators": (
        ("systems", "integrator", "official", "site"),
        ("solution", "provider", "corporate", "website"),
    ),
    "general": (
        ("manufacturer", "official", "website"),
        ("corporate", "headquarters", "site"),
    ),
}


def query_cache_key(text: str) -> str:
    return " ".join((text or "").lower().split())


def infer_sub_sector(query_text: str, prompt_id: str = "") -> str:
    low = f"{query_text} {prompt_id}".lower()
    for sector, keys in _SUB_SECTOR_KEYWORDS.items():
        if sector == "general":
            continue
        if any(k in low for k in keys):
            return sector
    return "general"


def mutate_low_yield_query(query: str, attempt: int = 0) -> str:
    base = sanitize_discovery_query(query)
    if not base:
        return ""
    suffix = _MUTATION_SUFFIXES[attempt % len(_MUTATION_SUFFIXES)]
    mutated = sanitize_discovery_query(f"{base} {suffix}")
    if not mutated or is_listicle_discovery_query(mutated):
        return ""
    if mutated.lower() == base.lower():
        return ""
    return mutated


def sub_sector_query_pool(
    sector: str,
    market: str,
    geo: str,
    *,
    count: int = 2,
) -> list[dict[str, str]]:
    g = geo_search_label(geo)
    topic = refine_search_topic(market, geo)
    templates: dict[str, list[str]] = {
        "manufacturers": [
            _q(topic, "manufacturer", g, "official", "site"),
            _q(topic, "manufacturing", g, "corporate", "website"),
        ],
        "api_manufacturers": [
            _q("API manufacturers", g, topic, "GMP", "official", "site"),
            _q("bulk drug", "API", "producer", g, "plant"),
        ],
        "cdmo": [
            _q("contract manufacturing", topic, g, "GMP", "CDMO"),
            _q("CDMO", topic, g, "official", "website"),
        ],
        "exporters": [
            _q(topic, "exporter", g, "WHO", "GMP", "certified"),
        ],
        "distributors": [
            _q("authorized", topic, "distributor", g, "firm", "official"),
        ],
        "biotech": [
            _q("biotech", topic, g, "R&D", "pipeline"),
        ],
        "general": [
            _q(topic, g, "headquarters", "corporate", "website"),
        ],
    }
    rows: list[dict[str, str]] = []
    for i, text in enumerate(templates.get(sector, templates["general"])[:count]):
        text = sanitize_discovery_query(text)
        if not text or is_listicle_discovery_query(text):
            continue
        rows.append(
            {
                "id": f"M{i + 1}_{sector[:4]}",
                "level": "mutation",
                "text": text,
                "sub_sector": sector,
            }
        )
    return rows


@dataclass
class QueryMetrics:
    query: str
    query_id: str
    sub_sector: str = "general"
    raw_hits: int = 0
    unique_companies: int = 0
    validated_companies: int = 0

    @property
    def yield_score(self) -> float:
        return self.unique_companies / max(self.raw_hits, 1)


@dataclass
class QueryYieldTracker:
    """Per-query yield memory + rolling window for smart stop."""

    by_query: dict[str, QueryMetrics] = field(default_factory=dict)
    sector_validated: dict[str, int] = field(default_factory=dict)
    sector_yield_samples: dict[str, list[float]] = field(default_factory=dict)
    recent_yields: deque[float] = field(default_factory=lambda: deque(maxlen=8))
    seen_queries: set[str] = field(default_factory=set)
    # First prompt id that ran each exact query text (for skip logging)
    searched_query_sources: dict[str, str] = field(default_factory=dict)

    def record(
        self,
        *,
        query_id: str,
        query: str,
        sub_sector: str,
        raw_hits: int,
        unique_added: int,
        validated_added: int,
    ) -> float:
        """Returns per-prompt yield ratio (new unique / raw hits)."""
        key = query_id or query[:40]
        m = self.by_query.get(key)
        if m is None:
            m = QueryMetrics(query=query, query_id=key, sub_sector=sub_sector)
            self.by_query[key] = m
        m.raw_hits += raw_hits
        m.unique_companies += unique_added
        m.validated_companies += validated_added
        if validated_added > 0:
            self.sector_validated[sub_sector] = (
                self.sector_validated.get(sub_sector, 0) + validated_added
            )

        ratio = unique_added / max(raw_hits, 1)
        self.recent_yields.append(ratio)
        samples = self.sector_yield_samples.setdefault(sub_sector, [])
        samples.append(ratio)
        if len(samples) > 12:
            del samples[:-12]
        return ratio

    def sector_avg_yield(self, sector: str) -> float:
        samples = self.sector_yield_samples.get(sector) or []
        if not samples:
            return 0.5
        return sum(samples) / len(samples)

    def should_stop_on_yield(
        self,
        unique_count: int,
        *,
        min_unique: int = 120,
        threshold: float = 0.08,
        window: int = 8,
        min_prompts_run: int = 15,
        prompts_run: int = 0,
    ) -> bool:
        """Stop when recent queries are unproductive (not merely high unique count)."""
        if prompts_run < min_prompts_run:
            return False
        if unique_count < min_unique:
            return False
        # The yield buffer is a bounded deque (maxlen=8). A window larger than the buffer (e.g.
        # LOW_YIELD_CONSECUTIVE=10) used to make this check unreachable — clamp it.
        maxlen = self.recent_yields.maxlen or window
        window = max(1, min(int(window), int(maxlen)))
        if len(self.recent_yields) < window:
            return False
        recent = list(self.recent_yields)[-window:]
        avg = sum(recent) / len(recent)
        return avg < threshold

    def priority_for_prompt(self, prompt: dict[str, str]) -> float:
        text = str(prompt.get("text") or "")
        pid = str(prompt.get("id") or "")
        sector = str(prompt.get("sub_sector") or infer_sub_sector(text, pid))
        base = self.sector_avg_yield(sector) * 4.0

        sector_count = self.sector_validated.get(sector, 0)
        if sector_count < 5:
            base += 1.5
        elif sector_count > 12:
            base -= 1.0

        m = self.by_query.get(pid)
        if m and m.raw_hits >= 3:
            base += m.yield_score * 2.0

        if prompt.get("level") == "sector_tree":
            base += 0.5
        return base

    def prioritize(self, prompts: list[dict[str, str]]) -> list[dict[str, str]]:
        return sorted(
            prompts,
            key=lambda p: self.priority_for_prompt(p),
            reverse=True,
        )

    def expansion_prompts_for_sector(
        self,
        sector: str,
        geo: str,
        *,
        seen: set[str],
        max_new: int = 4,
        market: str = "",
    ) -> list[dict[str, str]]:
        """Dynamic expansion when a sector query had high yield."""
        g = geo_search_label(geo)
        topic = refine_search_topic(market, geo) if market else ""
        blob = f"{topic} {sector}".lower()
        chemical = bool(
            re.search(
                r"\b(?:ethylene|petro|polymer|plastic|bio[\s-]?based|renewable\s+chem)\b",
                blob,
            )
        )
        facade = bool(
            re.search(
                r"\b(?:cladding|facade|façade|curtain\s*wall|rainscreen|"
                r"composite\s+panel|building\s+envelope)\b",
                blob,
            )
        )
        pharma = bool(
            re.search(
                r"\b(?:pharma\w*|pharmaceutical\w*|cdmo|bulk\s+drug|"
                r"active\s+pharmaceutical\s+ingredient)\b"
                r"|\bdrug\b(?!\s*(?:test|screen|abuse|polic|war|enforcement|awareness|addict))",
                blob,
            )
        )
        if chemical:
            templates = (
                _CHEM_HIGH_YIELD_EXPANSIONS.get(sector)
                or _CHEM_HIGH_YIELD_EXPANSIONS.get("general", ())
            )
        elif facade:
            templates = (
                _FACADE_HIGH_YIELD_EXPANSIONS.get(sector)
                or _FACADE_HIGH_YIELD_EXPANSIONS.get("general", ())
            )
        elif pharma:
            templates = (
                _PHARMA_HIGH_YIELD_EXPANSIONS.get(sector)
                or _PHARMA_HIGH_YIELD_EXPANSIONS.get("general", ())
            )
        else:
            topic_parts = tuple(w for w in topic.split() if len(w) >= 3)[:3]
            generic = _GENERIC_TOPIC_EXPANSIONS.get(sector) or _GENERIC_TOPIC_EXPANSIONS["general"]
            templates = tuple(
                (*topic_parts, *parts) if topic_parts else parts for parts in generic
            )
        out: list[dict[str, str]] = []
        for i, parts in enumerate(templates):
            if len(out) >= max_new:
                break
            text = sanitize_discovery_query(_q(*(*parts, g)))
            if not text:
                continue
            key = query_cache_key(text)
            if key in seen:
                continue
            # Do not seen.add(key) here — _run_prompt registers after a real search
            out.append(
                {
                    "id": f"HX{i}_{sector[:4]}",
                    "level": "expansion",
                    "text": text,
                    "sub_sector": sector,
                }
            )
        return out

    def low_yield_queries(self, *, max_items: int = 3) -> list[QueryMetrics]:
        weak = [
            m
            for m in self.by_query.values()
            if m.raw_hits >= 5 and m.yield_score < 0.15
        ]
        weak.sort(key=lambda m: m.yield_score)
        return weak[:max_items]

    def undercovered_sectors(self, *, target: int = 5) -> list[str]:
        seen = set(_SUB_SECTOR_KEYWORDS) - {"general"}
        out = [s for s in seen if self.sector_validated.get(s, 0) < target]
        out.sort(key=lambda s: self.sector_avg_yield(s), reverse=True)
        return out

    def summary(self) -> list[dict]:
        rows = []
        for m in sorted(self.by_query.values(), key=lambda x: -x.validated_companies):
            rows.append(
                {
                    "query_id": m.query_id,
                    "query": m.query[:80],
                    "sub_sector": m.sub_sector,
                    "raw_hits": m.raw_hits,
                    "unique_companies": m.unique_companies,
                    "validated_companies": m.validated_companies,
                    "yield_score": round(m.yield_score, 3),
                }
            )
        return rows

    def sector_yield_memory(self) -> dict[str, float]:
        return {
            s: round(self.sector_avg_yield(s), 3)
            for s in self.sector_yield_samples
        }
