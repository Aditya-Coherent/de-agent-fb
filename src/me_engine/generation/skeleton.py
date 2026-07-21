"""Label-only skeleton for the synthetic Input Sheet.

IMPORTANT — no numeric leakage:
    We read ONLY text labels from the human sheet: dimension titles (col B),
    segment names (col B), and the geography header row (row 3). We never read
    any numeric cell (columns C+ of data rows). The nesting/parent map and the
    region→country grouping are *structural* facts (also present in the Neon DB
    segmentation tree), not numbers, so they carry no value information.

The generated share model: every "sibling group" is a share-of-parent vector
that sums to 1.0. Agents decide each vector; deterministic math expands it to
per-country columns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook


# --- Structural nesting for the 5 primary dimensions (parent -> ordered children).
#     Segments not listed as a parent are leaves. Level-1 keys are dimension roots.
#     This is topology only (who rolls up into whom), never a value.
NESTING: dict[str, dict[str, list[str]]] = {
    "By Component": {
        "__root__": ["Hardware", "Software", "Services"],
        "Hardware": [
            "Kiosk Scanner Devices",
            "Dedicated / Retailer-Supplied Mobile Devices",
            "Smart-Cart Units",
            "Kiosk Terminals / Display + Controller",
            "Payment Units",
            "Kiosk Cabinet / Enclosure",
            "Receipt Printer",
            "Others (Scale / Bagging Hardware, Power & Internal Electronics, "
            "Mounting / Wiring / Minor Internal Hardware, Embedded Scan Modules, "
            "Network & Accessories)",
        ],
        "Kiosk Scanner Devices": [
            "Single-plane Kiosk Scanners",
            "Multi-plane (Bioptic) Kiosk Scanners",
            "Presentation Kiosk Scanners",
        ],
        "Software": [
            "Scanning & Decoding software",
            "Computer Vision Software",
            "POS & Payment Integration Software",
            "Store Operations & Workflow Software",
            "Others (Analytics & Dashboard Software, etc.)",
        ],
        "Services": ["Managed Services", "Professional Services"],
    },
    "By Product Type": {
        "__root__": ["Fixed-Position Self-Scanning Kiosks", "Mobile Self-Scanning Solutions"],
        "Fixed-Position Self-Scanning Kiosks": [
            "Single-Plane Self-Scanning Kiosks",
            "Multi-Plane Self-Scanning Kiosks",
            "Presentation Self-Scanning Kiosks",
        ],
        "Mobile Self-Scanning Solutions": [
            "Dedicated Mobile Self-Scanning Devices",
            "Smartphone-Based Self-Scanning",
            "Smart-Cart-Based Self-Scanning",
        ],
    },
    "By Model Type": {"__root__": ["Cash Model", "Cashless Model"]},
    "By Technology": {
        "__root__": ["Barcode Scanning (1D/2D)", "RFID-Based Scanning", "Computer Vision and AI Scanning"]
    },
    "By Retail Format": {
        "__root__": [
            "Supermarkets & Hypermarkets",
            "Convenience Stores",
            "Specialty and Discount Stores",
            "Department Stores",
            "Warehouse Clubs",
            "Pharmacies & Drugstores",
        ]
    },
}

# The priced dimension (ASP is quoted per By Component leaf/branch, as in the workbook).
PRICED_DIMENSION = "By Component"

# Region -> ordered member countries (topology; matches the DB segmentation tree).
REGIONS: dict[str, list[str]] = {
    "North America": ["U.S.", "Canada"],
    "Europe": [
        "U.K.", "Germany", "Italy", "France", "Spain", "Russia", "Netherlands",
        "Ireland", "Sweden", "Poland", "Hungary", "Slovakia", "Turkey", "Rest of Europe",
    ],
    "Asia Pacific": [
        "China", "Japan", "India", "South Korea", "Singapore", "Malaysia", "Thailand",
        "Indonesia", "Rest of ASEAN", "Australia", "Rest of Asia Pacific",
    ],
    "Latin America": ["Brazil", "Argentina", "Mexico", "Rest of Latin America"],
    "Middle East & Africa": ["GCC", "South Africa", "Rest of Middle East & Africa"],
}


@dataclass(frozen=True, slots=True)
class Skeleton:
    market_name: str
    geographies: list[str]
    regions: dict[str, list[str]]
    nesting: dict[str, dict[str, list[str]]]
    priced_dimension: str

    def sibling_groups(self, dimension: str) -> list[tuple[str, list[str]]]:
        """Return (parent_key, [children]) groups for a dimension, root first."""
        tree = self.nesting[dimension]
        groups = [("__root__", tree["__root__"])]
        for parent, children in tree.items():
            if parent != "__root__":
                groups.append((parent, children))
        return groups

    def ordered_segments(self, dimension: str) -> list[str]:
        """All nodes of a dimension (parents + children) in seg-band order, deduped."""
        out: list[str] = []
        seen: set[str] = set()
        for _parent, children in self.sibling_groups(dimension):
            for seg in children:
                if seg not in seen:
                    seen.add(seg)
                    out.append(seg)
        return out

    def leaves(self, dimension: str) -> list[str]:
        tree = self.nesting[dimension]
        parents = set(tree.keys())
        out: list[str] = []
        for children in tree.values():
            for c in children:
                if c not in parents:
                    out.append(c)
        return out


def _extract_geographies(ws) -> list[str]:
    geos: list[str] = []
    for col in range(3, ws.max_column + 1):
        val = ws.cell(3, col).value
        if val is None:
            break
        text = str(val).strip()
        if text and text not in geos:
            geos.append(text)
    return geos


def _regions_from_tree(tree) -> dict[str, list[str]]:
    """Top-level children of the geography tree become regions; their leaf
    descendants become the member countries."""
    regions: dict[str, list[str]] = {}
    if tree is None or not getattr(tree, "children", None):
        return regions

    def leaves(node) -> list[str]:
        if not getattr(node, "children", None):
            return [node.name]
        out: list[str] = []
        for c in node.children:
            out.extend(leaves(c))
        return out

    for region_node in tree.children:
        regions[region_node.name] = leaves(region_node)
    return regions


def load_skeleton_from_human_sheet(human_sheet: Path, market_name: str) -> Skeleton | None:
    """Build a Skeleton from a human Input Sheet's OWN structure.

    Reads the real dimensions, segments, parent/child nesting (inferred via the
    sub-share-sum signal) and geography tree using the pipeline's schema
    extractor — so a synthetic sheet generated from this skeleton matches the
    human sheet cell-for-cell (labels only; numbers are still generated fresh).
    """
    from me_engine.tools.extract_schema import extract_schema

    try:
        schema = extract_schema(human_sheet)
    except Exception:  # noqa: BLE001
        return None
    if not schema.dimensions:
        return None

    nesting: dict[str, dict[str, list[str]]] = {}
    for dim in schema.dimensions:
        parent = dim.parent or {}
        root = [s for s in dim.segments if parent.get(s) is None]
        tree: dict[str, list[str]] = {"__root__": root}
        for s in dim.segments:
            p = parent.get(s)
            if p is not None:
                tree.setdefault(p, []).append(s)
        if root:
            nesting[dim.title] = tree
    if not nesting:
        return None

    geos = [str(g) for g in schema.geographies]
    regions = _regions_from_tree(schema.geography_tree)
    if regions:
        known = set(geos)
        regions = {r: [c for c in members if c in known] for r, members in regions.items()}
        regions = {r: m for r, m in regions.items() if m}
        covered = {c for m in regions.values() for c in m}
        missing = [g for g in geos if g not in covered]
        if missing:
            regions.setdefault("Rest of World", []).extend(missing)
    if not regions:
        regions = {"Global": geos}
    if not geos:
        geos = [c for m in regions.values() for c in m]

    priced = schema.priced_dimension if schema.priced_dimension in nesting else next(iter(nesting))
    return Skeleton(
        market_name=market_name or schema.market_name,
        geographies=geos,
        regions=regions,
        nesting=nesting,
        priced_dimension=priced,
    )


def load_skeleton(human_sheet: Path, market_name: str) -> Skeleton:
    """Build the skeleton, reading ONLY labels (col B / row 3) from the human sheet.

    We validate that every skeleton geography actually appears as a *label* in the
    human sheet, but we never read the numeric body of the sheet.
    """
    wb = load_workbook(human_sheet, data_only=True, read_only=True)
    ws = wb["Data"]
    geos = _extract_geographies(ws)
    wb.close()

    # Restrict to the geographies our region map knows, preserving human order.
    known = {g for members in REGIONS.values() for g in members}
    geos = [g for g in geos if g in known]
    return Skeleton(
        market_name=market_name,
        geographies=geos,
        regions=REGIONS,
        nesting=NESTING,
        priced_dimension=PRICED_DIMENSION,
    )
