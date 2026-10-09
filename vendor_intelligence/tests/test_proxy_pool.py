from vendor_intel.clients.proxy_pool import (
    ProxyEntry,
    ProxyPipelineResult,
    _normalize_proxy_url,
    _parse_proxifly_rows,
    merge_proxy_lists,
)


def test_normalize_proxy_url():
    assert _normalize_proxy_url("http://1.2.3.4:8080") == "http://1.2.3.4:8080"
    assert _normalize_proxy_url("1.2.3.4:8080") == "http://1.2.3.4:8080"
    assert _normalize_proxy_url("socks5://1.2.3.4:1080") == "socks5h://1.2.3.4:1080"
    assert _normalize_proxy_url("socks4://1.2.3.4:1080") is None


def test_parse_proxifly_rows():
    rows = [
        {
            "proxy": "http://140.227.61.201:3128",
            "protocol": "http",
            "ip": "140.227.61.201",
            "port": 3128,
        }
    ]
    out = _parse_proxifly_rows(rows)
    assert len(out) == 1
    assert out[0].source == "proxifly"
    assert out[0].url == "http://140.227.61.201:3128"


def test_pipeline_result_best_prefers_ddgs():
    entries = [
        ProxyEntry("http://1.1.1.1:80", "a", ddgs_ok=False),
        ProxyEntry("http://2.2.2.2:80", "b", ddgs_ok=True),
    ]
    r = ProxyPipelineResult(entries=entries)
    assert r.best is not None
    assert r.best.url == "http://2.2.2.2:80"


def test_merge_dedupes():
    a = [ProxyEntry("http://1.1.1.1:80", "a")]
    b = [ProxyEntry("http://1.1.1.1:80", "b"), ProxyEntry("http://2.2.2.2:80", "b")]
    merged = merge_proxy_lists(a, b, shuffle=False)
    assert len(merged) == 2
