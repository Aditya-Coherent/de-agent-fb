"""Query consolidation (2026-10-21): one paid search per distinct query, fewer queries per vendor and claim,
CMI agents reusing the fleet's queries, and a hard stop when Perplexity runs out of credit. No network."""
from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from me_engine.generation import web_evidence as w  # noqa: E402

from AiAgentsFleet.cmi_report import entities, verify_facts  # noqa: E402
from AiAgentsFleet.cmi_report.groups import FQ, GEOGRAPHY_GROUP, GROUPS  # noqa: E402
from AiAgentsFleet.cmi_report.run_cmi_report import _EVIDENCE_QUERIES  # noqa: E402
from AiAgentsFleet.field_groups import FIELD_GROUPS  # noqa: E402

PPLX = {"results": [{"title": f"T{i}", "url": f"https://s{i}.example/x", "snippet": f"snippet number {i} " * 4}
                    for i in range(10)]}


class _Resp:
    def __init__(self, code, data=None, text=""):
        self.status_code, self._data, self.text = code, data, text

    def json(self):
        return self._data


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "_dotenv_loaded", True)
    monkeypatch.setattr(w, "_CACHE_PATH", tmp_path / "search_cache.db")
    monkeypatch.setattr(w, "_shared", {"client": None, "bucket": None, "prefix": "", "off": True})
    for k in ("WEB_SEARCH_CACHE_DAYS", "WEB_SEARCH_MODE", "WEB_SEARCH_BACKEND", "SEARCH_LOG", "WEB_SEARCH_STRICT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-test")
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    return monkeypatch


def _httpx(monkeypatch, resp, delay=0.0):
    calls = []
    mod = types.ModuleType("httpx")

    def post(url, json, timeout, headers):
        calls.append(json)
        time.sleep(delay)
        return resp
    mod.post = post
    monkeypatch.setitem(sys.modules, "httpx", mod)
    return calls


def test_same_query_with_different_result_counts_is_paid_once(env):
    calls = _httpx(env, _Resp(200, PPLX))
    a = w.search_snippets_with_sources("Aquaculture Feed Market  market size 2025", 5)
    b = w.search_snippets_with_sources("aquaculture feed market market size 2025", 6)   # case/space differ
    assert len(calls) == 1 and len(a) == 5 and len(b) == 6


def test_parallel_identical_queries_pay_once(env):
    calls = _httpx(env, _Resp(200, PPLX), delay=0.05)
    threads = [threading.Thread(target=w.search_snippets_with_sources, args=("same query", 4)) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(calls) == 1


def test_search_log_marks_paid_and_cached(env, tmp_path):
    env.setenv("SEARCH_LOG", str(tmp_path / "s.jsonl"))
    _httpx(env, _Resp(200, PPLX))
    w.search_snippets_with_sources("q", 4)
    w.search_snippets_with_sources("q", 4)
    import json
    rows = [json.loads(x) for x in (tmp_path / "s.jsonl").read_text().splitlines()]
    assert [r["source"] for r in rows] == ["paid", "local"]


def test_quota_exhausted_stops_in_strict_mode(env):
    _httpx(env, _Resp(401, text='{"error":{"type":"insufficient_quota","message":"You exceeded your current quota"}}'))
    env.setenv("WEB_SEARCH_STRICT", "1")
    with pytest.raises(w.SearchQuotaExhausted):
        w.search_snippets_with_sources("q", 4)
    try:                                                  # agents catch Exception: this must get through
        w.search_snippets_with_sources("q2", 4)
    except Exception:  # noqa: BLE001
        pytest.fail("SearchQuotaExhausted was caught by 'except Exception'")
    except w.SearchQuotaExhausted:
        pass


def test_vendor_check_is_two_queries_and_the_first_is_market_independent():
    a = entities.entity_queries("Thermo Fisher Scientific Inc.", "Aquaculture Testing & Diagnostics Market", ["rapid test kits"])
    b = entities.entity_queries("Thermo Fisher Scientific Inc.", "Agricultural Imaging Technology Market", ["drone cameras"])
    assert len(a) == 2 and a[0] == b[0]
    assert "Aquaculture Testing & Diagnostics" in a[1] and "rapid test kits" in a[1]


def test_requirement_claims_search_what_their_regulator_claim_searches():
    fields = {"regulation": {"category_terms": ["aquatic animal health"], "snapshot": [
        {"country": "Egypt", "regulator": "General Authority for Fish Resources Development",
         "requirements": "Kits must be registered. Imports need a permit."}]}}
    cl = verify_facts.claims(fields)
    reg = next(c for c in cl if c["kind"] == "regulator")
    pols = [c for c in cl if c["kind"] == "policy"]
    assert len(pols) == 2
    assert all(verify_facts.queries(p, "aquaculture diagnostics") == verify_facts.queries(reg, "aquaculture diagnostics")
               for p in pols)


def test_a_vendor_move_that_states_no_move_is_not_searched():
    searched = []
    c = {"id": "vendor:0", "kind": "vendor_move", "country": "", "subject": "QIAGEN",
         "text": "QIAGEN N.V.: Supplies MEA laboratories indirectly; no dated MEA aquaculture move is evidenced."}
    llm = types.SimpleNamespace(_cache_path=None)
    r = verify_facts.check_claim(c, "aquaculture diagnostics", llm, lambda qs: searched.append(qs) or [])
    assert searched == [] and r["status"] == "omitted"


def test_cmi_agents_use_the_fleet_query_for_shared_topics():
    fleet = {q for g in FIELD_GROUPS for q in g["queries"]} | {FQ("market_definition", 1)}
    cmi = {g["key"]: g["queries"] for g in GROUPS}
    assert FQ("segmentation_grade_form", 0) in cmi["segments_global"]
    assert FQ("competitive_landscape", 0) in cmi["vendors"]
    assert {FQ("market_size", 0), FQ("market_size", 3)} <= set(cmi["published"])
    assert FQ("regional", 2) in GEOGRAPHY_GROUP["queries"]
    # every chapter's evidence query is one an agent already searched
    searched = fleet | {q for qs in cmi.values() for q in qs}
    assert all(q in searched for qs in _EVIDENCE_QUERIES.values() for q in qs)
