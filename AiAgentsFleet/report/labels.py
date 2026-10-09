"""Names the dimension each segment list in whitepaper_fields.json actually
measures, so headings, chart labels and captions say the same thing.

The field-group schema has fixed keys borrowed from the Vitamin C template
(`by_grade_or_type`, `by_form`, `applications`, `application_mix_pct`), but
what the agents put in them varies by market: "forms" can be shrimp species,
"grades" can be software use-cases, a region's "application mix" can be a
feed-type mix. Hard-coding "Grades", "Forms" or "Applications" in a heading
then contradicts the chart beneath it.

Labels come from prose.json["labels"] (one LLM call in prose_writer.py, which
sees the actual item names); every label falls back to a neutral default.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DIMENSIONS = ("grade", "form", "application", "sub_segment_group")
DEFAULTS = {
    "grade": {"singular": "Product Type", "plural": "Product Types"},
    "form": {"singular": "Product Form", "plural": "Product Forms"},
    "application": {"singular": "Application", "plural": "Applications"},
    "sub_segment_group": {"singular": "Segment", "plural": "Segments"},
}
_STOP = {"and", "of", "the", "for", "other", "based", "a", "an", "in", "with"}


def _tokens(name: str, ignore: frozenset[str] = frozenset()) -> set[str]:
    toks = re.split(r"[^a-z0-9]+", str(name).split(" (")[0].lower())
    toks = {t[:-1] if len(t) > 3 and t.endswith("s") else t for t in toks if t and t not in _STOP}
    return toks - ignore


def same_item(a: str, b: str, ignore: frozenset[str] = frozenset()) -> bool:
    """`ignore`: market-name words ("shrimp", "biostimulant") that every item
    shares and that would otherwise make unrelated items look alike."""
    ta, tb = _tokens(a, ignore), _tokens(b, ignore)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta or len(ta & tb) / len(ta | tb) >= 0.5


def overlap(names: list[str], others: list[str], ignore: frozenset[str] = frozenset()) -> float:
    """Share of `names` that match some item in `others`."""
    if not names or not others:
        return 0.0
    return sum(any(same_item(n, o, ignore) for o in others) for n in names) / len(names)


def segment_names(fields: dict) -> dict[str, list[str]]:
    seg = fields.get("segmentation_grade_form") or {}
    return {
        "grade": [g.get("name", "") for g in seg.get("by_grade_or_type") or []],
        "form": [g.get("name", "") for g in seg.get("by_form") or []],
        "application": [a.get("name", "") for a in (fields.get("end_use_industry") or {}).get("applications") or []],
        "sub_segment_group": [g.get("application", "") for g in
                              (fields.get("sub_segments") or {}).get("by_top_application") or []],
    }


def _valid(v) -> bool:
    return (isinstance(v, dict) and all(isinstance(v.get(k), str) and 0 < len(v[k].split()) <= 4
                                         for k in ("singular", "plural")))


def resolve(fields: dict, llm_labels: dict | None = None, market: str = "") -> dict:
    """Returns {dimension: {"singular", "plural"}} for grade / form /
    application / sub_segment_group / region_mix, plus two flags:
      grade_duplicates_application -- Section 5's "grade" list is really the
        Section 6 application list again (skip it rather than print two
        conflicting splits of the same thing);
      sub_groups_are_applications -- Section 7's groups are Section 6's
        applications (so "within each major application" is true)."""
    llm_labels = llm_labels or {}
    out = {d: (llm_labels[d] if _valid(llm_labels.get(d)) else DEFAULTS[d]) for d in DIMENSIONS}
    out = {d: {k: v[k].strip().title() if v[k].islower() else v[k].strip() for k in ("singular", "plural")}
           for d, v in out.items()}

    names = segment_names(fields)
    ignore = frozenset(_tokens(market) - {"market", "global"})
    out["grade_duplicates_application"] = overlap(names["grade"], names["application"], ignore) >= 0.6
    out["sub_groups_are_applications"] = overlap(names["sub_segment_group"], names["application"], ignore) >= 0.6

    # A region's "application_mix_pct" is whichever global list its names match.
    regions = (fields.get("regional") or {}).get("regions") or {}
    mix = sorted({m.get("name", "") for r in regions.values() for m in r.get("application_mix_pct") or []})
    scores = {d: overlap(mix, names[d], ignore) for d in ("application", "grade", "form")}
    best = max(scores, key=lambda d: (scores[d], d == "application"))
    out["region_mix"] = out[best] if scores[best] >= 0.4 else {"singular": "Segment", "plural": "Segments"}
    return out


def load(market_dir: Path, fields: dict, market: str = "") -> dict:
    prose_path = Path(market_dir) / "prose.json"
    llm_labels = {}
    if prose_path.exists():
        llm_labels = json.loads(prose_path.read_text(encoding="utf-8")).get("labels") or {}
    return resolve(fields, llm_labels, market)


def lower(label: str) -> str:
    """'Product Types' -> 'product types', keeping acronyms ('AI Use Cases' -> 'AI use cases')."""
    return " ".join(w if w.isupper() and len(w) > 1 else w.lower() for w in label.split())


def mentions(text: str, name: str) -> bool:
    """True if every word of segment `name` appears in `text`."""
    tn = _tokens(name)
    return bool(tn) and tn <= _tokens(text)


def _words(text: str) -> set[str]:
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w
            for w in re.findall(r"[a-z]+", (text or "").lower()) if len(w) > 2 and w not in {"the", "and", "for"}}


def similar_heading(a: str, b: str, market: str = "") -> bool:
    """Two headings/taglines that say the same thing ('Europe leads, North
    America accelerates' vs 'Europe Anchors Value, North America Accelerates').
    Words of the market name are ignored -- nearly every heading contains them."""
    ignore = _words(market)
    wa, wb = _words(a) - ignore, _words(b) - ignore
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.5
