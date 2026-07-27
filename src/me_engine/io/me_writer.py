"""Render a MarketResult into an ME workbook in the exact source style.

Strategy: load the reference workbook as a *style template* and overwrite only
the numeric cells (metric paths, Y-o-Y, CAGR, market share) at positions found by
`SheetLayout`. Because we never touch formatting, headers or spacing, the output
is style-identical to the source; only the numbers are (re)written from the
assembled result. When no template is supplied, a blank workbook could be built,
but template-based rendering is what guarantees parity for Stage 1.

IMPORTANT: this only works when the template's sheets/row-labels already match
the generated result's geographies/segments (e.g. re-running the SAME market
the template belongs to). If a sheet or a (band, label) isn't found in the
template, that cell is a no-op by construction — the template's OWN original
value silently survives into the "output" workbook, looking like a real result.
`write()` tracks exactly this and raises `TemplateMismatchError` rather than
letting that happen quietly. There is currently no from-scratch writer for a
market with no matching template — that needs to be built separately.
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet

from ..assembly.model import BandResult, MarketResult, MetricRow
from ..domain.taxonomy import YEARS
from .layout import SheetLayout, cagr_col_for, metric_col, share_col, yoy_col

# Require near-total coverage: a mismatch here means the template belongs to a
# different market/taxonomy, not just cosmetic label drift.
_MIN_ROW_COVERAGE = 0.95


class TemplateMismatchError(RuntimeError):
    """The template's sheets/rows don't sufficiently match the generated
    result — writing would silently leave most cells as the template's stale
    original data instead of the freshly computed result."""


class MEWorkbookWriter:
    """Writes a MarketResult onto a copy of a reference template workbook."""

    def __init__(self, template_path: Path | str) -> None:
        self._template_path = Path(template_path)

    def write(self, result: MarketResult, out_path: Path | str) -> Path:
        wb = load_workbook(self._template_path)        # keeps styles & layout
        missing_sheets: list[str] = []
        missing_rows: list[str] = []
        expected = 0
        written = 0

        for name, geo in result.geographies.items():
            if name not in wb.sheetnames:
                missing_sheets.append(name)
                expected += sum(len(b.rows_by_label) for b in geo.bands.values())
                continue
            ws = wb[name]
            layout = SheetLayout.discover(ws)
            for band_result in geo.bands.values():
                w, e, miss = self._write_band(ws, layout, band_result)
                written += w
                expected += e
                missing_rows.extend(f"{name}/{band_result.band}/{label}" for label in miss)

        coverage = (written / expected) if expected else 1.0
        if missing_sheets or coverage < _MIN_ROW_COVERAGE:
            def _sample(items: list[str], n: int = 5) -> str:
                shown = ", ".join(items[:n])
                return shown + ("..." if len(items) > n else "")

            raise TemplateMismatchError(
                f"Template '{self._template_path.name}' does not match this "
                f"market's structure — only {written}/{expected} cells "
                f"({coverage:.0%}) could be written.\n"
                f"  Missing sheets ({len(missing_sheets)}): {_sample(missing_sheets)}\n"
                f"  Missing rows ({len(missing_rows)}): {_sample(missing_rows)}\n"
                "This template can only be used for the market it was built "
                "for. Provide a matching --reference/--template, or build a "
                "from-scratch ME workbook for this market (not yet implemented)."
            )

        out = Path(out_path)
        wb.save(out)
        return out

    def _write_band(self, ws: Worksheet, layout: SheetLayout,
                     band: BandResult) -> tuple[int, int, list[str]]:
        """Write every row of `band`; return (written, expected, missing_labels)."""
        written = 0
        missing: list[str] = []
        for label, row in band.rows_by_label.items():
            coord = layout.row_of.get((band.band, label))
            if coord is not None:
                self._write_row(ws, coord, row)
                written += 1
            else:
                missing.append(label)
        return written, len(band.rows_by_label), missing

    def _write_row(self, ws: Worksheet, coord: int, row: MetricRow) -> None:
        self._write_metric(ws, coord, row)
        self._write_yoy(ws, coord, row)
        self._write_cagr(ws, coord, row)
        self._write_share(ws, coord, row)

    @staticmethod
    def _write_metric(ws: Worksheet, coord: int, row: MetricRow) -> None:
        for year in YEARS:
            ws.cell(coord, metric_col(year)).value = row.series.at(year)

    @staticmethod
    def _write_yoy(ws: Worksheet, coord: int, row: MetricRow) -> None:
        for year, value in row.series.yoy().items():
            ws.cell(coord, yoy_col(year)).value = value

    @staticmethod
    def _write_cagr(ws: Worksheet, coord: int, row: MetricRow) -> None:
        ws.cell(coord, cagr_col_for()).value = row.series.cagr()

    @staticmethod
    def _write_share(ws: Worksheet, coord: int, row: MetricRow) -> None:
        if row.share_of_parent is None:
            return
        for year in YEARS:
            ws.cell(coord, share_col(year)).value = row.share_of_parent.at(year)
