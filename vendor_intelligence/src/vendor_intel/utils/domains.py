from urllib.parse import urlparse


def fix_mojibake(text: str) -> str:
    """Repair UTF-8 text that was mis-decoded as Latin-1 (GalÃ¨nica -> Galènica,
    EspaÃ±a -> España, AromÃ¡tica -> Aromática). No-op when no mojibake markers."""
    if not text or not any(m in text for m in ("Ã", "Â", "â€")):
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return text if "�" in repaired else repaired


def domain_from_url(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def normalize_name(name: str) -> str:
    n = fix_mojibake(name).strip()
    for suffix in (" Inc.", " Inc", " Ltd.", " Ltd", " LLC", " GmbH", " Pvt Ltd", " Corporation", " Corp.", " Corp"):
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
    return n.strip()


def company_dedupe_key(name: str) -> str:
    """Stable merge key — collapses Dr. Reddy's / Dr Reddys Laboratories, etc."""
    import re

    raw = normalize_name(name).lower()
    compact = re.sub(r"[^a-z0-9]", "", raw)
    aliases = {
        "drreddys": "drreddyslaboratories",
        "drreddy": "drreddyslaboratories",
        "drreddylaboratories": "drreddyslaboratories",
        "drreddyslaboratories": "drreddyslaboratories",
        "drreddyslaboratory": "drreddyslaboratories",
        "drreddyslabs": "drreddyslaboratories",
        "drreddyslab": "drreddyslaboratories",
        "sunpharma": "sunpharmaceuticalindustries",
        "sunpharmaceutical": "sunpharmaceuticalindustries",
        "zyduscadila": "cadilahealthcare",
    }
    return aliases.get(compact, raw)
