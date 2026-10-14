"""Shared, acquirer-agnostic URL/asset verification layer -- Phase 1
(`DEMAND_SIDE_EVIDENCE_PLAN.md`).

The LLM (via `evidence_router.propose_*_evidence_plan`) may propose any
candidate source/locator it likes -- this module is the deterministic code
that decides what a URL actually IS before an acquirer commits to a full
download+parse attempt. It replaces each acquirer's own ad-hoc
extension/content-type check (`pdf_acquirer._download_pdf_bytes`,
`tabular_acquirer._download`, `api_acquirer._fetch`, and
`web_acquirer._fetch_page_text`) with ONE shared implementation.

What it verifies, in order:
    1. The URL is at least syntactically fetchable (http/https).
    2. A real HTTP GET, following redirects up to a small capped hop count,
       records the FINAL resolved URL -- never the originally-guessed one.
    3. HTTP status code is 2xx.
    4. What the response body actually IS -- via file-signature ("magic
       bytes") sniffing first, falling back to the `Content-Type` header --
       never trusting the URL's own extension alone. This is what catches an
       HTML "landing page" masquerading as a `.pdf`/`.csv`/`.xlsx` link, or a
       JSON-endpoint claim that actually returns an HTML error page.

`verify_asset` never raises -- same discipline as every acquirer today
(`except Exception: return None`-style): a verification failure is a
normal, expected, logged outcome, not an error state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ExpectedType = Literal["pdf", "csv", "excel", "json", "html"]

_TIMEOUT_SECONDS = 15
_MAX_REDIRECTS = 5
_MAX_DOWNLOAD_BYTES = 20_000_000  # 20MB cap -- mirrors the acquirers' own prior per-type caps
_USER_AGENT = "Mozilla/5.0"

# File-signature ("magic bytes") checks -- authoritative over both the URL's
# own extension and the server's self-reported Content-Type header, per the
# plan's PDF requirement: "never trust the URL's .pdf extension alone."
_PDF_MAGIC = b"%PDF-"
_XLSX_MAGIC = b"PK\x03\x04"  # OOXML (.xlsx) is a zip container
_XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy OLE2 binary (.xls)


@dataclass
class AssetVerification:
    """Result of verifying one candidate URL against one expected type.

    `content` carries the bytes already fetched during verification (when
    accessible) so a calling acquirer never needs a second network round
    trip just to get what this function already downloaded -- an additive
    convenience field, not part of the plan's minimal shape but harmless to
    a caller that ignores it.
    """

    resolved_url: str
    is_accessible: bool
    actual_content_type: str
    matches_expected_type: bool
    http_status: int | None
    rejection_reason: str | None = None
    content: bytes | None = None


def _strip_bom(content: bytes) -> bytes:
    return content[3:] if content[:3] == b"\xef\xbb\xbf" else content


def _looks_like_html(content: bytes) -> bool:
    head = _strip_bom(content)[:1024].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<html" in head[:256]


def _looks_like_json(content: bytes) -> bool:
    stripped = _strip_bom(content).lstrip()
    if not stripped or stripped[:1] not in (b"{", b"["):
        return False
    try:
        import json

        json.loads(stripped.decode("utf-8"))
        return True
    except Exception:  # noqa: BLE001
        return False


def _looks_like_csv(content: bytes, content_type: str) -> bool:
    if "csv" in content_type:
        return True
    try:
        sample = _strip_bom(content)[:2000].decode("utf-8")
    except Exception:  # noqa: BLE001
        return False
    if not sample.strip():
        return False
    first_line = sample.splitlines()[0] if sample.splitlines() else ""
    return ("," in first_line or "\t" in first_line or ";" in first_line) and "<" not in first_line


def sniff_type(content: bytes, content_type: str = "") -> str | None:
    """Best-effort determination of what `content` actually IS, independent
    of the URL extension or the server's own `Content-Type` header. Returns
    one of 'pdf'/'excel'/'html'/'json'/'csv', or None if undetermined."""
    if not content:
        return None
    if content.startswith(_PDF_MAGIC):
        return "pdf"
    if content.startswith(_XLSX_MAGIC) or content.startswith(_XLS_MAGIC):
        return "excel"
    if _looks_like_html(content):
        return "html"
    if _looks_like_json(content):
        return "json"
    if _looks_like_csv(content, content_type):
        return "csv"
    return None


def verify_asset(url: str, expected_type: ExpectedType) -> AssetVerification:
    """Fetch `url` (following redirects, capped at `_MAX_REDIRECTS` hops)
    and determine whether what it actually resolves to matches
    `expected_type`. Never raises."""
    if not url or not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        return AssetVerification(
            resolved_url=url or "", is_accessible=False, actual_content_type="",
            matches_expected_type=False, http_status=None, rejection_reason="malformed_url",
        )

    try:
        import requests

        session = requests.Session()
        session.max_redirects = _MAX_REDIRECTS
        resp = session.get(
            url, timeout=_TIMEOUT_SECONDS, allow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
    except Exception as exc:  # noqa: BLE001
        reason = "redirect_loop" if "TooManyRedirects" in type(exc).__name__ else "download_failed"
        return AssetVerification(
            resolved_url=url, is_accessible=False, actual_content_type="",
            matches_expected_type=False, http_status=None, rejection_reason=reason,
        )

    resolved_url = str(resp.url or url)
    status = resp.status_code
    content_type_header = str(resp.headers.get("content-type", "")).lower()

    if status is None or status >= 400:
        return AssetVerification(
            resolved_url=resolved_url, is_accessible=False, actual_content_type=content_type_header,
            matches_expected_type=False, http_status=status, rejection_reason="download_failed",
        )

    content = resp.content or b""
    if len(content) > _MAX_DOWNLOAD_BYTES:
        return AssetVerification(
            resolved_url=resolved_url, is_accessible=True, actual_content_type=content_type_header,
            matches_expected_type=False, http_status=status, rejection_reason="download_failed",
        )

    sniffed = sniff_type(content, content_type_header)

    if expected_type == "html":
        # A "page" is anything not clearly a binary file type of another kind
        # (a PDF/Excel file at this URL is a different acquirer's job, not a
        # web page -- undetermined content is treated as prose/text, fine).
        matches = sniffed not in ("pdf", "excel", "json")
    else:
        matches = sniffed == expected_type

    rejection_reason = None
    if not matches:
        rejection_reason = "html_landing_page_not_asset" if sniffed == "html" else "wrong_content_type"

    return AssetVerification(
        resolved_url=resolved_url, is_accessible=True, actual_content_type=content_type_header,
        matches_expected_type=matches, http_status=status, rejection_reason=rejection_reason,
        content=content,
    )
