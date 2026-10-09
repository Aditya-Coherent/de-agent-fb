from vendor_intel.clients.duckduckgo import (
    _dedupe_key,
    configured_ddgs_backends,
    ddgs_backend_param,
)


def test_ddgs_backend_param_default(monkeypatch):
    monkeypatch.delenv("DDGS_BACKENDS", raising=False)
    param = ddgs_backend_param()
    assert "startpage" not in param
    assert "duckduckgo" in param


def test_ddgs_backend_strips_startpage(monkeypatch):
    monkeypatch.setenv("DDGS_BACKENDS", "startpage,bing,duckduckgo")
    param = ddgs_backend_param()
    assert "startpage" not in param
    assert "bing" in param
    assert "duckduckgo" in param


def test_ddgs_backend_param_passthrough(monkeypatch):
    monkeypatch.setenv("DDGS_BACKENDS", "bing,brave,google")
    assert ddgs_backend_param() == "bing,brave,google"
    assert configured_ddgs_backends() == ["bing", "brave", "google"]


def test_configured_backends_auto(monkeypatch):
    monkeypatch.setenv("DDGS_BACKENDS", "auto")
    names = configured_ddgs_backends()
    assert "startpage" not in names
    assert "duckduckgo" in names


def test_dedupe_key():
    assert _dedupe_key("https://Example.com/path#x") == "https://example.com/path"
