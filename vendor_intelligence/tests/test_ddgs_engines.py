from vendor_intel.clients.ddgs_engines import (
    BLOCKED_TEXT_BACKENDS,
    NEWS_BACKENDS_OFFICIAL,
    normalize_news_backends,
    normalize_text_backends,
)


def test_auto_maps_to_safe_text_without_startpage(monkeypatch):
    monkeypatch.setenv("DDGS_BACKENDS", "auto")
    param, names, dropped = normalize_text_backends()
    assert "startpage" not in param
    assert "startpage" not in names
    assert "duckduckgo" in names
    assert dropped == []


def test_startpage_stripped(monkeypatch):
    monkeypatch.setenv("DDGS_BACKENDS", "startpage,bing")
    param, names, dropped = normalize_text_backends()
    assert "startpage" not in param
    assert "bing" in names
    assert "startpage" in dropped


def test_news_backends_official_only(monkeypatch):
    monkeypatch.setenv("DDGS_NEWS_BACKENDS", "bing,yahoo")
    param, names = normalize_news_backends()
    assert set(names) <= NEWS_BACKENDS_OFFICIAL
    assert param == "bing,yahoo"


def test_news_from_text_list_intersection(monkeypatch):
    monkeypatch.setenv("DDGS_BACKENDS", "bing,brave,mojeek")
    monkeypatch.delenv("DDGS_NEWS_BACKENDS", raising=False)
    param, names = normalize_news_backends()
    assert names == ["bing"]
    assert param == "bing"


def test_blocked_includes_startpage():
    assert "startpage" in BLOCKED_TEXT_BACKENDS
