"""PDF evidence acquirer.

    Source Discovery -> MULTI-candidate PDF URL verification (Phase 1's
    `asset_verifier`, tried in order until one actually resolves to a real,
    accessible PDF) -> download -> deterministic text/table extraction
    (pdfplumber, NO LLM) -> keyword-relevance page shortlist (deterministic)
    -> LLM interpretation ONLY on the shortlisted pages -> source URL + page
    (+ table) reference -> structured fact -> evidence verification

The LLM never sees the raw PDF bytes and never invents a page number -- it
must point at one of the ALREADY-SHORTLISTED page indices, and the value
must be regex-traceable in that specific page's own extracted text (same
`evidence.build_fact` gate as the web acquirer) before it can be `observed`.
It MAY additionally point at a specific table on that page (`table_index`),
but an invalid/unresolvable table pointer only drops the table provenance --
it never invalidates an otherwise-valid page-anchored value.

Phase 3 (`DEMAND_SIDE_EVIDENCE_PLAN.md`) hardens this module along three
axes, all deterministic (no LLM involved):
  1. Discovery tries several candidate URLs, not just the first search hit,
     verifying each via Phase 1's shared `asset_verifier` (redirects,
     magic-byte sniffing, HTML-landing-page detection) until one is a real
     PDF -- so one broken/guessed link doesn't sink the whole source.
  2. Extraction distinguishes WHY it failed (`encrypted_pdf`,
     `scanned_no_text_layer`, `corrupted_or_unreadable`) instead of a single
     generic `extraction_failed`, so the audit trail can tell "this report
     genuinely has no machine-readable text" apart from "the file was
     corrupt" apart from "it's password protected."
  3. Table provenance: tables are extracted per page, a lightweight
     continuation heuristic merges a table that spans two consecutive pages,
     and the LLM may name which table (if any) a value came from --
     `evidence.build_fact`'s traceability check still runs against the
     actual extracted text/table content either way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from me_engine.generation.llm import GenLLM

from . import asset_verifier, evidence, fact_validator, web_search

_MAX_PAGES_TO_SCAN = 40      # don't choke on a 500-page annual report
_MAX_CANDIDATE_PAGES = 5
_MAX_PAGE_CHARS_TO_LLM = 3000
_MAX_PDF_CANDIDATES = 3      # Phase 3: try several discovered URLs, not just the first


@dataclass
class _PageContent:
    """One page's deterministically-extracted content. `combined_text` (plain
    text + serialized tables) is what keyword shortlisting and the
    text-traceability acceptance gate operate on; `tables` is kept
    separately so a value can be anchored to a specific table index."""

    text: str
    tables: list[list[list[str]]] = field(default_factory=list)
    combined_text: str = ""


def _download_pdf_bytes(url: str) -> asset_verifier.AssetVerification:
    """Deterministically verify + fetch a candidate PDF URL -- delegates all
    redirect-following, magic-byte (`%PDF-`), and HTML-landing-page
    detection to the shared `asset_verifier` module (Phase 1,
    `DEMAND_SIDE_EVIDENCE_PLAN.md`) instead of trusting the `.pdf` extension
    or a bare `content-type` header check alone."""
    return asset_verifier.verify_asset(url, "pdf")


def _serialize_tables(tables: list[list[list[str]]]) -> str:
    return "\n".join(
        "\n".join(" | ".join(str(c) if c is not None else "" for c in row) for row in table)
        for table in tables
    )


def _rebuild_combined_text(page: _PageContent) -> str:
    table_text = _serialize_tables(page.tables)
    return page.text if not table_text else f"{page.text}\n{table_text}"


def _merge_spanning_tables(pages: list[_PageContent]) -> None:
    """Heuristic table-continuation merge (Phase 3): pdfplumber extracts one
    table per page even when the SAME logical table spans a page break. If a
    page's LAST table has the same column count as the NEXT page's FIRST
    table, treat the next page's table as a continuation of the earlier one
    -- appended in place -- so a value that happens to land on the
    continuation page still resolves back to the table's actual starting
    page/table index. Best-effort only; never raises."""
    for i in range(len(pages) - 1):
        cur, nxt = pages[i], pages[i + 1]
        if not cur.tables or not nxt.tables:
            continue
        last_table, first_table = cur.tables[-1], nxt.tables[0]
        if not last_table or not first_table or not last_table[0] or not first_table[0]:
            continue
        if len(last_table[0]) != len(first_table[0]):
            continue  # different column count -- not the same logical table
        last_table.extend(first_table)
        nxt.tables = nxt.tables[1:]
        cur.combined_text = _rebuild_combined_text(cur)
        nxt.combined_text = _rebuild_combined_text(nxt)


def _extract_pages(pdf_bytes: bytes) -> tuple[list[_PageContent], str | None, str]:
    """Deterministic text/table extraction, one `_PageContent` per page.
    Returns `(pages, failure_reason, document_title)`. `failure_reason` is
    None on success; on failure `pages` is always `[]`. Distinguishes:
      - `encrypted_pdf`            -- password-protected, can't be opened
      - `scanned_no_text_layer`    -- pages exist but carry no extractable
                                       text at all (a strong scanned/image
                                       -only signal, not a genuinely blank
                                       document)
      - `corrupted_or_unreadable`  -- malformed file, or any other
                                       pdfplumber/pdfminer failure
    Never raises."""
    try:
        import io

        import pdfplumber
    except Exception:  # noqa: BLE001 -- missing/broken dependency
        return [], "corrupted_or_unreadable", ""

    document_title = ""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            if getattr(pdf, "is_encrypted", False):
                return [], "encrypted_pdf", ""
            document_title = str((pdf.metadata or {}).get("Title") or "").strip()
            pages: list[_PageContent] = []
            for page in pdf.pages[:_MAX_PAGES_TO_SCAN]:
                text = page.extract_text() or ""
                tables = page.extract_tables() or []
                combined = text if not tables else f"{text}\n{_serialize_tables(tables)}"
                pages.append(_PageContent(text=text, tables=tables, combined_text=combined))
    except Exception as exc:  # noqa: BLE001
        name, msg = type(exc).__name__.lower(), str(exc).lower()
        if "password" in name or "password" in msg or "encrypt" in name or "encrypt" in msg:
            return [], "encrypted_pdf", ""
        return [], "corrupted_or_unreadable", ""

    if not pages:
        return [], "corrupted_or_unreadable", ""
    if all(not p.text.strip() for p in pages):
        # Pages exist (the file opened fine) but NONE of them yielded any
        # extractable text -- classic scanned/image-only PDF, distinct from a
        # genuinely corrupt or genuinely-empty document.
        return [], "scanned_no_text_layer", document_title

    _merge_spanning_tables(pages)
    return pages, None, document_title


def _keyword_shortlist(pages: list[str], keywords: list[str]) -> list[int]:
    """Deterministic relevance ranking -- pages scored by keyword hit count,
    top N kept, ties broken by original page order (NOT by index descending)
    so the shortlist is stable and reproducible for identical input. No LLM
    involved in this step."""
    keywords_lower = [k.lower() for k in keywords if k]
    scored: list[tuple[int, int]] = []
    for i, text in enumerate(pages):
        if not text:
            continue
        low = text.lower()
        score = sum(low.count(k) for k in keywords_lower)
        if score > 0:
            scored.append((score, i))
    scored.sort(key=lambda t: (-t[0], t[1]))  # score desc, then original order for ties
    return [i for _, i in scored[:_MAX_CANDIDATE_PAGES]]


def _search_pdf_candidates(source: dict[str, Any], geography: str) -> list[str]:
    """Multi-candidate PDF discovery (Phase 3): instead of committing to the
    first search result that merely LOOKS like a PDF link, gather up to
    `_MAX_PDF_CANDIDATES` distinct candidate URLs -- the plan's own locator
    first (if it already looks like a direct PDF URL), then `.pdf`-looking
    search results, then any remaining results -- so `acquire()` can verify
    each in turn via `asset_verifier` until one actually resolves to a real,
    accessible PDF instead of giving up after the first guess."""
    candidates: list[str] = []
    locator = str(source.get("locator", "") or "")
    if locator.lower().endswith(".pdf"):
        candidates.append(locator)

    query = str(source.get("search_query_template", "") or "").replace("{country}", geography)
    if "filetype:pdf" not in query.lower():
        query = f"{query} filetype:pdf"
    results = web_search.search(query, max_results=max(_MAX_PDF_CANDIDATES * 2, 5))
    pdf_like = [r.url for r in results if r.url and r.url.lower().endswith(".pdf")]
    others = [r.url for r in results if r.url and not r.url.lower().endswith(".pdf")]
    for url in pdf_like + others:
        if url not in candidates:
            candidates.append(url)
    return candidates[:_MAX_PDF_CANDIDATES]


def _format_page_block(idx: int, page: _PageContent) -> str:
    block = f"[PAGE {idx}]\n{page.text[:_MAX_PAGE_CHARS_TO_LLM]}"
    for t_idx, table in enumerate(page.tables):
        table_text = "\n".join(" | ".join(str(c) if c is not None else "" for c in row) for row in table)
        block += f"\n[PAGE {idx} TABLE {t_idx}]\n{table_text[:_MAX_PAGE_CHARS_TO_LLM]}"
    return block


def acquire(
    llm: GenLLM, source: dict[str, Any], geography: str, *, unit: str, context: str, keywords: list[str],
    emitter: Any | None = None, stage: str = "evidence_search", bucket: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    attempt: dict[str, Any] = {"source_type": "pdf", "source_name": source["source_name"], "found": False}

    candidates = _search_pdf_candidates(source, geography)
    if not candidates:
        attempt["source_url"] = ""
        attempt["rejection_reason"] = "no_source_found"
        return None, attempt

    verification: asset_verifier.AssetVerification | None = None
    pdf_url = candidates[0]
    tried: list[dict[str, Any]] = []
    for url in candidates:
        v = _download_pdf_bytes(url)
        tried.append({"source_url": url, "resolved_url": v.resolved_url, "rejection_reason": v.rejection_reason})
        if emitter is not None:
            emitter.emit(
                stage, "source_attempt", bucket=bucket, country=geography if geography != "global" else None,
                source={"name": source.get("source_name"), "url": v.resolved_url or url, "type": "pdf"},
                status="success" if (v.is_accessible and v.matches_expected_type and v.content) else "failed",
                error=v.rejection_reason,
            )
        if v.is_accessible and v.matches_expected_type and v.content:
            verification, pdf_url = v, url
            break
    attempt["candidates_tried"] = tried

    if verification is None:
        last = tried[-1] if tried else {}
        attempt["source_url"] = last.get("resolved_url") or last.get("source_url", "")
        attempt["rejection_reason"] = last.get("rejection_reason") or "download_failed"
        return None, attempt

    # Provenance requirement (Phase 1): the persisted fact's source_url must
    # be the RESOLVED (post-redirect) URL of the candidate that actually
    # worked, never the originally-guessed one.
    pdf_url = verification.resolved_url or pdf_url
    attempt["source_url"] = pdf_url
    attempt["resolved_url"] = verification.resolved_url
    pdf_bytes = verification.content

    pages, extraction_failure, document_title = _extract_pages(pdf_bytes)
    if extraction_failure:
        attempt["rejection_reason"] = extraction_failure
        return None, attempt
    if not pages:
        attempt["rejection_reason"] = "extraction_failed"
        return None, attempt

    combined_texts = [p.combined_text for p in pages]
    candidate_idx = _keyword_shortlist(combined_texts, keywords)
    if not candidate_idx:
        attempt["rejection_reason"] = "not_relevant_page"
        return None, attempt

    system = (
        "You are a document-extraction analyst. You will be given SHORTLISTED pages (and, where "
        "present, tables on those pages) from a real PDF report. Your ONLY job is to find, inside "
        f"these pages, an explicit numeric figure for: {context} (target unit: {unit}). Do NOT "
        "estimate or invent a number -- if none of these pages/tables states one, return "
        "found=false. If the figure comes from a specific table shown as '[PAGE N TABLE T]', name "
        "that table's index T; otherwise leave table_index null. CRITICAL: 'value' must be the "
        "FULLY EXPANDED absolute number, with any scale word applied (e.g. '450 million tonnes' -> "
        "450000000). If the number you found actually measures something DIFFERENT from what was "
        "requested -- e.g. a market value in dollars instead of a physical production/consumption "
        "volume, installed capacity instead of production, a different country/region, or a "
        "different year -- you must return found=false rather than reporting it anyway. Never "
        "invent a page, table, citation, year, unit, or geography that isn't actually present on "
        "the shortlisted pages."
    )
    pages_text = "\n\n".join(_format_page_block(i, pages[i]) for i in candidate_idx)
    user = (
        f"{context}\nTarget unit: {unit}\n\n{pages_text}\n\n"
        'Return JSON: {"found": <true|false>, "value": <number or null>, "page_index": <the PAGE '
        'number shown above that this came from, or null>, "table_index": <the TABLE number on '
        'that page this came from, or null if it came from plain text>, "year": <int or null>, '
        '"citation": "<exact phrase stating the figure>", "is_direct_figure": <true|false>, '
        '"derivation_basis": "<if not direct, the basis; else empty>", "matched_metric_label": '
        '"<in your own words, what this number actually measures, per the page>", '
        '"matched_geography": "<the geography this page actually states the figure for, or empty '
        'if unstated>", "matched_unit": "<the unit the page actually states, or empty>"}'
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
        attempt["rejection_reason"] = "extraction_failed"
        return None, attempt
    try:
        value = float(data.get("value"))
    except (TypeError, ValueError):
        attempt["rejection_reason"] = "extraction_failed"
        return None, attempt

    page_idx = data.get("page_index")
    if not isinstance(page_idx, int) or page_idx not in candidate_idx:
        # Never accept a page not actually in the shortlist we gave it.
        attempt["rejection_reason"] = "extraction_failed"
        return None, attempt

    page = pages[page_idx]
    table_idx = data.get("table_index")
    if not (isinstance(table_idx, int) and 0 <= table_idx < len(page.tables)):
        # An invalid/unresolvable table pointer only drops the table
        # provenance -- the value is still anchored to a real, shortlisted
        # page and its own text-traceability check below is unaffected.
        table_idx = None

    location_ref = f"page {page_idx + 1}" + (f", table {table_idx + 1}" if table_idx is not None else "")
    doc_title = document_title or pdf_url.rsplit("/", 1)[-1]
    is_direct_figure = bool(data.get("is_direct_figure", True))

    rejection = fact_validator.validate_candidate(
        requested_unit=unit, requested_context=context, requested_geography=geography,
        matched_unit=str(data.get("matched_unit") or ""),
        matched_geography=str(data.get("matched_geography") or ""),
        matched_metric_label=str(data.get("matched_metric_label") or ""),
        evidence_text=page.combined_text[:2000], is_direct_figure=is_direct_figure,
    )
    if rejection:
        # A real, verified PDF page was found and read, but the number on it
        # answers a different question than the one asked (`fact_validator.py`,
        # post-Phase-12 addendum) -- reject the candidate outright, never
        # silently accept it as a weaker tier.
        attempt["rejection_reason"] = rejection
        return None, attempt

    fact = evidence.build_fact(
        value=value, unit=unit, year=data.get("year"), source_name=source["source_name"],
        source_url=pdf_url, citation=str(data.get("citation") or "").strip(),
        evidence_text=page.combined_text[:2000],
        is_direct_figure=bool(data.get("is_direct_figure", True)),
        derivation_basis=str(data.get("derivation_basis") or "").strip(),
        extra={
            "page_number": page_idx + 1, "table_index": table_idx,
            "document_title": doc_title, "location_ref": location_ref,
        },
    )
    attempt.update({
        "found": True, "value": fact["value"], "unit": fact["unit"], "year": fact.get("year"),
        "source_url": pdf_url, "page_number": page_idx + 1, "table_index": table_idx,
        "citation": fact.get("citation", ""), "evidence_type": fact["evidence_type"],
    })
    return fact, attempt
