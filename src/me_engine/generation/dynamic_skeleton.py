"""Build a Skeleton for ANY market directly from the DB segmentation HTML.

The CMI segmentation HTML is a clean nested list:
    <li><strong>X Insights (Revenue ...)</strong>   -> dimension header  (depth 1)
      <li>Segment</li>                                -> top-level segment (depth 2)
        <li>Sub-segment</li>                          -> child segment    (depth 3)
    ...
    <li><strong>Regional Insights ...</strong>        -> geography tree
      <li>Region</li> <li>Country</li> ...

We parse it into the same Skeleton shape the generator already consumes
(dimensions -> sibling groups, regions -> countries), so no human sheet is
needed — the frontend can accept any market present in the DB.
"""
from __future__ import annotations

import html
import re

from dataclasses import replace

from .db_source import (
    DBEvidence, fetch_market_evidence, fetch_market_evidence_semantic, geo_qualifiers, verify_market_match,
)
from .skeleton import REGIONS, Skeleton

_REGION_TITLE = re.compile(r"region|geograph", re.I)


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text


def _dim_title(header: str) -> str:
    # "Analyzer Type Insights (Revenue, USD Million, 2021 - 2033)" -> "By Analyzer Type"
    t = re.sub(r"\(.*?\)", "", header)
    t = re.sub(r"\bInsights\b", "", t, flags=re.I).strip(" -–—")
    t = re.sub(r"\s+", " ", t).strip()
    return f"By {t}" if t else "By Segment"


def _walk(raw: str):
    """Yield (depth, is_strong, text) for each <li> in document order."""
    depth = 0
    for m in re.finditer(r"<ul>|</ul>|<li[^>]*>(.*?)(?=<ul>|</li>)", raw, flags=re.S):
        tok = m.group(0)
        if tok == "<ul>":
            depth += 1
            continue
        if tok == "</ul>":
            depth = max(0, depth - 1)
            continue
        inner = m.group(1) or ""
        text = _clean(inner)
        if text:
            yield depth, "<strong>" in inner, text


def build_skeleton_from_html(market_name: str, raw_html: str) -> Skeleton | None:
    dimensions: list[tuple[str, dict[str, list[str]]]] = []
    regions: dict[str, list[str]] = {}
    cur_dim: str | None = None
    cur_tree: dict[str, list[str]] = {}
    cur_parent: str | None = None
    in_geo = False
    cur_region: str | None = None

    def flush():
        nonlocal cur_dim, cur_tree
        if cur_dim and cur_tree.get("__root__"):
            dimensions.append((cur_dim, cur_tree))
        cur_dim, cur_tree = None, {}

    for depth, is_strong, text in _walk(raw_html):
        is_header = depth <= 1 and (is_strong or re.search(r"Insights|\(Revenue|\(USD", text))
        if is_header:
            flush()
            if _REGION_TITLE.search(text):
                in_geo, cur_region = True, None
                cur_dim = None
                continue
            in_geo = False
            cur_dim = _dim_title(text)
            cur_tree = {"__root__": []}
            cur_parent = None
            continue

        if in_geo:
            if depth <= 2:  # region
                cur_region = text
                regions.setdefault(cur_region, [])
            elif cur_region is not None:  # country
                regions[cur_region].append(text)
            continue

        if cur_dim is None:
            continue
        if depth <= 2:  # top-level segment
            cur_tree["__root__"].append(text)
            cur_parent = text
        else:  # child of the previous top-level segment
            if cur_parent is not None:
                cur_tree.setdefault(cur_parent, []).append(text)
    flush()

    if not dimensions or not regions:
        return None

    # de-duplicate: a parent listed in __root__ that also has children stays as a
    # group parent; drop any empty/degenerate dimensions.
    nesting: dict[str, dict[str, list[str]]] = {}
    for title, tree in dimensions:
        root = [s for s in tree["__root__"] if s]
        if not root:
            continue
        clean_tree: dict[str, list[str]] = {"__root__": root}
        for parent, kids in tree.items():
            if parent != "__root__" and parent in root and kids:
                clean_tree[parent] = kids
        base = title
        i = 2
        while title in nesting:  # avoid collisions
            title = f"{base} ({i})"
            i += 1
        nesting[title] = clean_tree

    geographies = [c for members in regions.values() for c in members]
    if not geographies:
        return None
    priced = next(iter(nesting))
    return Skeleton(
        market_name=market_name,
        geographies=geographies,
        regions=regions,
        nesting=nesting,
        priced_dimension=priced,
    )


def _tree_text(ev: DBEvidence) -> str:
    lines = []
    for line in ev.segmentation_tree:
        depth, label = line.split("|", 1)
        lines.append("  " * int(depth) + "- " + label)
    return "\n".join(lines)


def build_skeleton_via_llm(market_name: str, ev: DBEvidence, llm) -> Skeleton | None:
    """Let the LLM turn a (possibly messy) DB segmentation outline into a clean schema.

    Robust to malformed HTML the regex parser can't handle: the model reads the
    indented outline + narrative and returns structured dimensions + geography tree.
    It must not invent segments that are not in the outline.
    """
    outline = _tree_text(ev)
    if not outline.strip():
        return None
    system = (
        "You convert a market-research segmentation outline into a structured schema. "
        "Rules: (1) Create a SEPARATE dimension for EVERY distinct '... Insights' / "
        "top-level category in the outline (e.g. Analyzer Type, Technology, Sampling "
        "Type, Installation Type, Application, End User). Do NOT merge distinct "
        "categories into one dimension. Title each as 'By <name>'. (2) For each dimension "
        "list its top-level segments ('root') and any parent→children nesting under "
        "'children'. (3) SEPARATELY extract the GEOGRAPHY tree as regions→countries — "
        "never mix geographies into the segmentation dimensions. (4) Use ONLY labels "
        "present in the outline; do not invent, merge, or drop segments. (5) Pick the "
        "dimension most naturally priced (usually the first product/type dimension) as "
        "'priced_dimension'."
    )
    user = (
        f"Market: {market_name}\n\nSEGMENTATION OUTLINE (indentation = nesting):\n{outline}\n\n"
        f"CONTEXT: {ev.raw_narrative[:800]}\n\n"
        'Return JSON: {"dimensions": [{"title": "By ...", "root": ["seg", ...], '
        '"children": {"parent_seg": ["child", ...]}}], '
        '"regions": {"Region": ["Country", ...]}, "priced_dimension": "By ..."}'
    )
    try:
        data = llm.complete_json(system, user)
    except Exception:  # noqa: BLE001
        return None

    nesting: dict[str, dict[str, list[str]]] = {}
    for entry in data.get("dimensions", []) or []:
        title = str(entry.get("title", "")).strip()
        root = [str(s).strip() for s in (entry.get("root") or []) if str(s).strip()]
        if not title or not root:
            continue
        tree = {"__root__": root}
        for parent, kids in (entry.get("children") or {}).items():
            kid_list = [str(k).strip() for k in (kids or []) if str(k).strip()]
            if str(parent).strip() and kid_list:
                tree[str(parent).strip()] = kid_list
        base, i = title, 2
        while title in nesting:
            title = f"{base} ({i})"; i += 1
        nesting[title] = tree

    regions: dict[str, list[str]] = {}
    for region, countries in (data.get("regions") or {}).items():
        clist = [str(c).strip() for c in (countries or []) if str(c).strip()]
        if str(region).strip() and clist:
            regions[str(region).strip()] = clist

    geographies = [c for members in regions.values() for c in members]
    if not nesting or not regions or not geographies:
        return None
    priced = str(data.get("priced_dimension", "")).strip()
    if priced not in nesting:
        priced = next(iter(nesting))
    return Skeleton(
        market_name=market_name,
        geographies=geographies,
        regions=regions,
        nesting=nesting,
        priced_dimension=priced,
    )


def load_skeleton_from_db(market_name: str, cfg) -> tuple[Skeleton | None, DBEvidence | None]:
    """Fetch DB evidence and build a skeleton. Primary path: LLM schema extraction
    (robust to messy HTML). Fallback: regex HTML parser.

    A keyword LIKE match can land on a completely unrelated report (e.g. "Global
    Watch Market" matching a blood-pressure-monitor report that happens to share a
    substring) — this builds the ENTIRE category taxonomy, so an unverified match
    here is worse than an unverified market-size evidence brief. Same guard as
    structure_node's DB evidence fetch: verify, and on rejection retry once with a
    genuine semantic-only lookup before giving up."""
    llm = None
    if cfg.is_online:
        from .llm import GenLLM  # local import to avoid a hard dependency cycle
        llm = GenLLM(cfg)

    ev = fetch_market_evidence(cfg.neon_url, market_name)
    if ev is not None and llm is not None and not verify_market_match(market_name, ev.raw_narrative, llm):
        ev = None
        try:
            alt = fetch_market_evidence_semantic(cfg.neon_url, market_name)
            if alt is not None and verify_market_match(market_name, alt.raw_narrative, llm):
                ev = alt
        except Exception:  # noqa: BLE001
            pass
    if ev is None or not ev.raw_segmentation:
        return None, ev
    sk = None
    if llm is not None:
        try:
            sk = build_skeleton_via_llm(market_name, ev, llm)
        except Exception:  # noqa: BLE001
            sk = None
    if sk is None:
        sk = build_skeleton_from_html(market_name, ev.raw_segmentation)
    if sk is not None:
        sk = ensure_global_geography(sk, market_name)
    return sk, ev


def ensure_global_geography(sk: Skeleton, market_name: str) -> Skeleton:
    """A global market must keep a global geography even if the matched
    report covered one region (e.g. a GCC-only report gave 'GCC' as the
    whole world). Markets that name a region keep the report's geography."""
    if geo_qualifiers(market_name):
        return sk
    if len(sk.regions) >= 3:
        return sk
    regions = {r: list(cs) for r, cs in REGIONS.items()}
    return replace(sk, regions=regions, geographies=[c for cs in regions.values() for c in cs])
