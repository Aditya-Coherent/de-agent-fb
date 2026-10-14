"""CSV / Excel evidence acquirer.

    Source Discovery -> MULTI-candidate dataset URL verification (Phase 1's
    `asset_verifier`, tried in order until one actually resolves to real
    tabular data -- by CONTENT sniffing, never by URL extension alone) ->
    download -> deterministic pandas/openpyxl load -> LLM identifies the
    relevant sheet/column/row filter from a SCHEMA SUMMARY (not the whole
    dataset) -> CODE applies that filter deterministically -> structured fact

The LLM only proposes the MAPPING (which sheet/column/row); the actual
number always comes from a real pandas lookup, never from the LLM reading
the dataset itself.

Phase 4 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) hardens discovery -- the extraction
logic below this point (schema summary, LLM mapping proposal, deterministic
filter/lookup) is unchanged from Phase 0-3:
  1. Discovery tries several candidate URLs (the plan's own locator plus
     several search results), not just the first `.csv`/`.xlsx`/`.xls`-
     suffixed hit, mirroring Phase 3's `pdf_acquirer._search_pdf_candidates`.
  2. A candidate is accepted as real tabular data based on what
     Phase 1's `asset_verifier` actually SNIFFS the response body to be --
     not the URL's extension -- so a non-suffixed download URL, a redirect
     target, or a content-disposition-served file with no extension in its
     URL at all are all discoverable, as long as the bytes really are a CSV
     or an Excel workbook.
  3. If a candidate resolves to an HTML page instead of a file (a dataset
     "landing page" that merely links to the real download), a bounded,
     one-hop link-extraction step looks for a plausible download link on
     that page and verifies THAT instead of giving up immediately.
"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin

from me_engine.generation.llm import GenLLM

from . import asset_verifier, evidence, fact_validator, web_search

_SAMPLE_ROWS = 8
_MAX_SCHEMA_CHARS = 6000
_MAX_TABULAR_CANDIDATES = 3   # Phase 4: try several discovered URLs, not just the first
_MAX_LANDING_PAGE_LINKS = 5   # bounded fan-out when following links off a landing page

# Href hints that plausibly point at a real downloadable dataset asset,
# ordered most- to least-specific -- extension matches are tried before the
# generic "download"/"export" wording, since the latter is a weaker signal.
_TABULAR_LINK_HINTS = (".csv", ".xlsx", ".xls", "format=csv", "format=xlsx", "download", "export")
_HREF_RE = re.compile(rb'href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)


def _expected_tabular_type(url: str) -> asset_verifier.ExpectedType:
    return "excel" if url.lower().endswith((".xlsx", ".xls")) else "csv"


def _extract_tabular_links(html_bytes: bytes, base_url: str) -> list[str]:
    """Best-effort HTML landing-page link extraction (Phase 4): a dataset
    "landing page" typically links to the actual downloadable file rather
    than serving it directly. Pulls out href targets that plausibly point at
    a real CSV/XLSX asset (by extension, or a download/export/format= query
    hint) and resolves them against the page's own URL, so a relative link
    becomes a real, fetchable absolute URL. No HTML-parser dependency -- a
    regex over raw href attributes is enough for this narrow purpose. Never
    raises."""
    try:
        matches = [m.group(1).decode("utf-8", errors="ignore") for m in _HREF_RE.finditer(html_bytes)]
    except Exception:  # noqa: BLE001
        return []

    ranked: list[tuple[int, str]] = []
    for href in matches:
        low = href.lower()
        rank = next((i for i, hint in enumerate(_TABULAR_LINK_HINTS) if hint in low), None)
        if rank is not None:
            ranked.append((rank, href))
    ranked.sort(key=lambda t: t[0])

    links: list[str] = []
    for _, href in ranked:
        try:
            resolved = urljoin(base_url, href)
        except Exception:  # noqa: BLE001
            continue
        if resolved not in links:
            links.append(resolved)
    return links


def _resolve_dataset(
    url: str, *, follow_landing_page: bool = True,
) -> tuple[str | None, asset_verifier.AssetVerification | None, str, str | None]:
    """Verify `url` and determine, from the ACTUAL fetched content (never the
    URL string alone), whether it is real tabular data or an HTML landing
    page that merely links to one. Returns
    `(kind, verification, resolved_url, rejection_reason)` where `kind` is
    `"csv"`/`"excel"` on success, else `None`. On a landing page, follows the
    first plausible download link ONE hop deep (never recurses past that, to
    bound retry volume) via the same Phase 1 `asset_verifier` check."""
    guess = _expected_tabular_type(url)
    verification = asset_verifier.verify_asset(url, guess)
    if not verification.is_accessible or not verification.content:
        return None, verification, verification.resolved_url or url, (verification.rejection_reason or "download_failed")

    resolved_url = verification.resolved_url or url
    if verification.matches_expected_type:
        return guess, verification, resolved_url, None

    # The URL-extension guess didn't hold -- decide from the ACTUAL bytes
    # instead (this is what makes non-suffixed/content-disposition-served
    # URLs discoverable at all: a `.csv`-guessed URL that's really an Excel
    # workbook, or a bare `/download?id=123` URL with no extension hint).
    sniffed = asset_verifier.sniff_type(verification.content, verification.actual_content_type)
    if sniffed in ("csv", "excel"):
        return sniffed, verification, resolved_url, None

    if sniffed == "html" and follow_landing_page:
        for link in _extract_tabular_links(verification.content, resolved_url)[:_MAX_LANDING_PAGE_LINKS]:
            kind, sub_verification, sub_url, _reason = _resolve_dataset(link, follow_landing_page=False)
            if kind:
                return kind, sub_verification, sub_url, None
        return None, verification, resolved_url, "html_landing_page_not_asset"

    return None, verification, resolved_url, (verification.rejection_reason or "wrong_content_type")


def _search_dataset_candidates(source: dict[str, Any], geography: str) -> list[str]:
    """Multi-candidate dataset discovery (Phase 4): instead of committing to
    the first search result that literally ends in `.csv`/`.xlsx`/`.xls`,
    gather up to `_MAX_TABULAR_CANDIDATES` distinct candidate URLs -- the
    plan's own locator first, then extension-suffixed search results, then
    any remaining results (a non-suffixed dataset/download URL is still a
    legitimate candidate; `_resolve_dataset` decides what it really is by
    content, not by URL shape) -- so `acquire()` can verify each in turn
    instead of giving up after the first guess."""
    candidates: list[str] = []
    locator = str(source.get("locator", "") or "")
    if locator:
        candidates.append(locator)

    query = str(source.get("search_query_template", "") or "").replace("{country}", geography)
    if not any(t in query.lower() for t in ("filetype:csv", "filetype:xlsx", "filetype:xls")):
        query = f"{query} filetype:csv OR filetype:xlsx"
    results = web_search.search(query, max_results=max(_MAX_TABULAR_CANDIDATES * 2, 5))
    suffixed = [r.url for r in results if r.url and r.url.lower().endswith((".csv", ".xlsx", ".xls"))]
    others = [r.url for r in results if r.url and not r.url.lower().endswith((".csv", ".xlsx", ".xls"))]
    for url in suffixed + others:
        if url not in candidates:
            candidates.append(url)
    return candidates[:_MAX_TABULAR_CANDIDATES]


def _load_sheets(content: bytes, url: str, kind: str | None = None) -> dict[str, Any] | None:
    import io

    import pandas as pd

    # `kind` (from `_resolve_dataset`'s content-sniffing) is authoritative
    # when known; falling back to sniffing/URL-suffix keeps this function
    # usable standalone (e.g. directly unit-tested) without a caller having
    # already resolved the kind.
    kind = kind or asset_verifier.sniff_type(content) or _expected_tabular_type(url)
    try:
        if kind == "excel":
            return pd.read_excel(io.BytesIO(content), sheet_name=None, engine="openpyxl")
        return {"Sheet1": pd.read_csv(io.BytesIO(content))}
    except Exception:  # noqa: BLE001
        return None


def acquire(
    llm: GenLLM, source: dict[str, Any], geography: str, *, unit: str, context: str, year: int | None,
    emitter: Any | None = None, stage: str = "evidence_search", bucket: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    attempt: dict[str, Any] = {"source_type": "csv_excel", "source_name": source["source_name"], "found": False}

    candidates = _search_dataset_candidates(source, geography)
    if not candidates:
        attempt["source_url"] = ""
        attempt["rejection_reason"] = "no_source_found"
        return None, attempt

    kind: str | None = None
    verification: asset_verifier.AssetVerification | None = None
    resolved_url = ""
    tried: list[dict[str, Any]] = []
    for url in candidates:
        k, v, r, reason = _resolve_dataset(url)
        tried.append({"source_url": url, "resolved_url": r, "rejection_reason": reason})
        if emitter is not None:
            emitter.emit(
                stage, "source_attempt", bucket=bucket, country=geography if geography != "global" else None,
                source={"name": source.get("source_name"), "url": r or url, "type": "tabular"},
                status="success" if k else "failed", error=reason,
            )
        if k:
            kind, verification, resolved_url = k, v, r
            break
    attempt["candidates_tried"] = tried

    if not kind or verification is None:
        last = tried[-1] if tried else {}
        attempt["source_url"] = last.get("resolved_url") or last.get("source_url", "")
        attempt["rejection_reason"] = last.get("rejection_reason") or "download_failed"
        return None, attempt

    # Provenance requirement (Phase 1): the persisted fact's source_url must
    # be the RESOLVED (post-redirect, post-landing-page) URL of the
    # candidate that actually worked, never the originally-guessed one.
    url = resolved_url
    attempt["source_url"] = url
    attempt["resolved_url"] = verification.resolved_url
    content = verification.content
    assert content is not None  # guaranteed by `_resolve_dataset`'s success path

    sheets = _load_sheets(content, url, kind)
    if not sheets:
        attempt["rejection_reason"] = "extraction_failed"
        return None, attempt

    schema_summary = "\n\n".join(
        f"Sheet '{name}' columns: {list(df.columns)}\nSample rows:\n{df.head(_SAMPLE_ROWS).to_string(index=False)}"
        for name, df in sheets.items()
    )[:_MAX_SCHEMA_CHARS]

    system = (
        "You are a dataset-schema analyst. Given a real dataset's column names and sample rows, "
        f"identify which sheet/column/row would contain: {context} (target unit: {unit}"
        + (f", year {year}" if year else "") + f", geography '{geography}'). Propose the MAPPING "
        "only -- you do NOT read the value yourself, code will look it up deterministically. If "
        "nothing in this schema plausibly contains it, say so honestly by returning found=false. "
        "If the column you'd pick actually measures something DIFFERENT from what was requested "
        "-- e.g. a dollar market-value column instead of a physical volume column, a capacity "
        "column instead of a production column -- you must return found=false rather than "
        "proposing it anyway."
    )
    user = (
        f"{context}\nTarget unit: {unit}\nGeography: {geography}\nYear: {year}\n\n{schema_summary}\n\n"
        'Return JSON: {"found": <true|false>, "sheet": "<sheet name>", "value_column": "<column '
        'name>", "filter": {"<column name>": "<value to match>", ...}, "aggregation": '
        '"<none|sum|mean>", "column_meaning": "<in your own words, what this column actually '
        'measures, based on its header/sample values>"}'
    )
    llm_error: str | None = None
    try:
        data = llm.complete_json(system, user)
    except Exception as exc:  # noqa: BLE001
        data = None
        llm_error = str(exc)
    if emitter is not None:
        emitter.emit(
            stage, "llm_call", bucket=bucket, country=geography if geography != "global" else None,
            llm={"prompt": f"{system}\n\n{user}"[:4000], "response": str(data)[:2000] if data else "", "error": llm_error},
            status="failed" if llm_error else "success",
        )
    if llm_error is not None:
        attempt["rejection_reason"] = "extraction_failed"
        return None, attempt
    if not data or not data.get("found"):
        attempt["rejection_reason"] = "not_relevant_page"
        return None, attempt

    sheet_name = data.get("sheet")
    value_col = data.get("value_column")
    filters = data.get("filter") or {}
    aggregation = str(data.get("aggregation") or "none").strip().lower()
    df = sheets.get(sheet_name)
    if df is None or value_col not in getattr(df, "columns", []):
        attempt["rejection_reason"] = "extraction_failed"
        return None, attempt

    # Extraction-quality gate (`fact_validator.py`, post-Phase-12 addendum):
    # a real, well-formed dataset was found and loaded, but the column the
    # LLM picked -- by its own name and declared meaning -- measures
    # something different from what was requested (e.g. a "Market Value
    # (USD Bn)" column selected for a physical production-volume request).
    # Deterministic, no extra LLM call: the column HEADER text and the
    # LLM's own `column_meaning` are both real, already-available strings.
    rejection = fact_validator.validate_candidate(
        requested_unit=unit, requested_context=context,
        matched_metric_label=f"{value_col} {data.get('column_meaning', '')}",
        evidence_text=str(value_col), is_direct_figure=True,
    )
    if rejection:
        attempt["rejection_reason"] = rejection
        return None, attempt

    subset = df
    for col, val in filters.items():
        if col in subset.columns:
            subset = subset[subset[col].astype(str).str.strip().str.lower() == str(val).strip().lower()]
    if subset.empty:
        attempt["rejection_reason"] = "geography_mismatch" if filters else "extraction_failed"
        return None, attempt

    try:
        numeric_col = subset[value_col].astype(float)
    except (ValueError, TypeError):
        attempt["rejection_reason"] = "extraction_failed"
        return None, attempt

    ambiguous = len(subset) > 1
    if ambiguous and aggregation == "sum":
        value = float(numeric_col.sum())
    elif ambiguous and aggregation == "mean":
        value = float(numeric_col.mean())
    else:
        value = float(numeric_col.iloc[0])

    location_ref = f"sheet='{sheet_name}', column='{value_col}', filter={filters}"
    fact = evidence.build_structured_fact(
        value=value, unit=unit, year=year, source_name=source["source_name"], source_url=url,
        location_ref=location_ref, geography_matched=bool(filters), ambiguous=ambiguous,
    )
    fact["file_name"] = url.rsplit("/", 1)[-1]
    fact["sheet"] = sheet_name
    attempt.update({
        "found": True, "value": value, "unit": unit, "year": year, "source_url": url,
        "location_ref": location_ref, "evidence_type": fact["evidence_type"],
    })
    return fact, attempt
