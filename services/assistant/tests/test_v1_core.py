"""Unit tests for the /v1 building blocks: spend guard, trace, evidence verification, ask, briefing, nl-filter, PDF."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.tools.client import OpsPerformanceUnavailable
from src.v1 import ask as ask_mod
from src.v1 import briefing as briefing_mod
from src.v1.evidence import EvidenceBook, number_supported, numbers_in, verify_claim
from src.v1.llm import FakeLLM, NoLLM
from src.v1.nl_filter import nl_filter, parse_rules, validate
from src.v1.p1 import P1
from src.v1.pdf import build_pdf
from src.v1.spend import SpendExhausted, SpendGuard
from src.v1.trace import Trace, TraceStore, parse_traceparent


# ── spend guard ──
def test_spend_guard_enforces_request_day_and_month_caps_and_persists(tmp_path):
    db = tmp_path / "s.db"
    g = SpendGuard(db, per_request_usd=0.05, per_day_usd=0.10, per_month_usd=0.15)
    now = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
    with pytest.raises(SpendExhausted) as e:
        g.check(0.06, now)
    assert e.value.scope == "per-request"
    g.record("t", "m", 10, 10, 0.04, now)
    g.record("t", "m", 10, 10, 0.05, now)
    with pytest.raises(SpendExhausted) as e:
        g.check(0.02, now)
    assert e.value.scope == "daily"
    tomorrow = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    g.check(0.02, tomorrow)                                         # a new day resets the daily cap...
    g.record("t", "m", 1, 1, 0.05, tomorrow)
    with pytest.raises(SpendExhausted) as e:
        g.check(0.02, tomorrow)                                     # ...but not the monthly one (0.04+0.05+0.05 > 0.15 - 0.02)
    assert e.value.scope == "monthly"
    assert SpendGuard(db).spent("month", now) == pytest.approx(0.14)  # persisted across instances


# ── trace ──
def test_traceparent_and_trace_store_roundtrip(tmp_path):
    tid = "4bf92f3577b34da6a3ce929d0e0e4736"
    assert parse_traceparent(f"00-{tid}-00f067aa0ba902b7-01") == tid
    assert parse_traceparent("nonsense") is None
    t = Trace(tid, "ask")
    with t.span("retrieval", "retrieval", hits=2):
        pass
    t.add("llm", "llm", 5.0, cost_usd=0.002, model="m")
    store = TraceStore(tmp_path / "t.db")
    store.save(t)
    got = store.get(tid)
    assert got["trace_id"] == tid and len(got["spans"]) == 2 and got["cost_usd"] == 0.002
    assert t.traceparent.split("-")[1] == tid


# ── evidence ──
def _book():
    b = BookHelper()
    return b


class BookHelper(EvidenceBook):
    def __init__(self):
        super().__init__()
        self.facts = self.add_p1("/v1/overview", {"kpis": {"already_late": {"value": 544, "unit": "cases", "provenance": "measured", "source": "x"},
                                                           "breach": {"value": 23.7, "unit": "%", "provenance": "measured", "source": "x"}}})
        self.pol = self.add_policy(SimpleNamespace(document_id="d1", title="Procurement Policy", section_title="4.2", citation="Procurement Policy, Section 4.2",
                                                    text="Purchase orders above 10,000 euros require a second approval before release."))


def test_numbers_and_conversions():
    assert numbers_in("EUR 1,500.5 and 23.7%") == [1500.5, 23.7]
    assert number_supported(23.7, [0.237]) and number_supported(48.0, [2.0]) and number_supported(2, [])
    assert not number_supported(99.0, [544.0, 23.7])


def test_verify_claim_cases():
    b = BookHelper()
    late, breach = b.facts
    ok = verify_claim("544 open cases are already late.", [late.id], b)
    assert ok["supported"]
    assert not verify_claim("600 open cases are already late.", [late.id], b)["supported"]
    assert not verify_claim("Cases are late.", [], b)["supported"]
    assert not verify_claim("Cases are late.", ["e99"], b)["supported"]
    p = verify_claim("Purchase orders above 10,000 euros require a second approval.", [b.pol.id], b)
    assert p["supported"], p
    bad = verify_claim("Purchase orders above 50,000 euros require a third approval.", [b.pol.id], b)
    assert not bad["supported"]


def test_identifiers_are_not_figures():
    assert numbers_in("case 2000000100_00001 has 5 open days and p(breach) 0.64") == [5.0, 0.64]
    assert numbers_in("supplier vendorID_0053 owes 1,577.00 and 12.5%") == [1577.0, 12.5]
    assert numbers_in("4507000430_00010 and 4507000896_00010") == []
    assert numbers_in("544. cases and 156300.26 EUR") == [544.0, 156300.26]            # ordinary figures are unchanged


def test_a_claim_that_names_a_numeric_case_id_is_checked_on_its_figures_not_its_label():
    """Real case ids are numeric (2000000100_00001). On the first local-model run every answer about a case was marked unsupported because the id's digits were read as a figure."""
    book = EvidenceBook()
    risk, loss = book.add_p1("/v1/cases/2000000100_00001", {"risk": {"p_breach": {"value": 0.64, "unit": "probability", "provenance": "experimental", "source": "m"},
                                                                     "expected_loss": {"value": 156300.26, "unit": "EUR", "provenance": "simulated", "source": "m"}}})
    right = verify_claim("Case 2000000100_00001 has a breach probability of 0.64.", [risk.id], book)
    assert right["supported"], right
    wrong = verify_claim("Case 2000000100_00001 has a breach probability of 0.91.", [risk.id], book)
    assert not wrong["supported"] and "0.91" in wrong["reason"] and "2000000100" not in wrong["reason"]
    assert verify_claim("The expected loss on case 2000000100_00001 is 156300.26 EUR.", [loss.id], book)["supported"]


# ── ask ──
def _p1(objs):
    def getter(path, params=None, **kw):
        if path in objs:
            return objs[path]
        raise OpsPerformanceUnavailable("down")
    return P1(None, getter)


OVERVIEW = {"as_of": "2018-04-16", "kpis": {"already_late": {"value": 544, "unit": "cases", "provenance": "measured", "source": "s", "as_of": "2018-04-16"},
                                            "at_risk_open": {"value": 1088, "unit": "cases", "provenance": "experimental", "source": "s"}}}
HITS = [SimpleNamespace(document_id="d1", title="Procurement Policy", section_title="4.2", citation="Procurement Policy, Section 4.2",
                        text="Purchase orders above 10,000 euros require a second approval before release.")]


def _id_for(prompt: str, label: str) -> str:
    import re
    return re.search(r"\[(e\d+)\] LIVE DATA \([^)]*\) " + re.escape(label) + " =", prompt).group(1)


def _run(question, llm, objs=None, hits=HITS, ctx=None):
    t = Trace(None, "ask")
    return ask_mod.ask({"question": question, "context": ctx or {"page": "overview"}}, llm=llm,
                       p1=_p1(OVERVIEW and (objs if objs is not None else {"/v1/overview": OVERVIEW})), retriever=lambda q, k: hits, trace=t)


def test_ask_flags_unsupported_claims_and_returns_evidence_and_trace():
    def script(system, user):
        assert "EVIDENCE" in user
        late_id = _id_for(user, "kpis.already_late")
        risk_id = _id_for(user, "kpis.at_risk_open")
        return json.dumps({"answer": "544 open cases are late. About 900 are at risk.",
                           "claims": [{"text": "544 open cases are already late.", "evidence_ids": [late_id]},
                                      {"text": "About 900 cases are at risk.", "evidence_ids": [risk_id]},
                                      {"text": "Everything is fine.", "evidence_ids": []}]})
    out = _run("How many cases are late and at risk?", FakeLLM(script))
    flags = [c["supported"] for c in out["claims"]]
    assert flags == [True, False, False] and out["supported_claims"] == 1
    assert out["trace_id"] and out["evidence"][0]["id"] == "e1" and out["cost_usd"] > 0 and not out["abstained"]


def test_the_scope_filter_lets_every_evaluation_question_through_and_still_refuses_every_off_topic_probe():
    """The first local-model evaluation found four overview questions refused as 'outside procurement scope' (plural 'interventions', 'expected loss', 'what should the team look at first')."""
    from scripts.eval_v1_ask import PROBES, TEMPLATES

    def in_scope(q):
        return bool(ask_mod.DOMAIN.search(q) or ask_mod.META.search(q))
    refused = [t.format(e="vendorID_0053") for ts in TEMPLATES.values() for t in ts if not in_scope(t.format(e="vendorID_0053"))]
    assert refused == []
    assert [q for q in PROBES if in_scope(q)] == []
    for q in ("Write me a poem about autumn.", "What is the pool of candidates?", "Which apple should I buy?"):          # "po" and "ap" are abbreviations, not prefixes
        assert not in_scope(q), q
    for q in ("How many interventions are recorded?", "What is the simulated expected loss?", "What should the team look at first today?", "Which policies apply to duplicate invoices?"):
        assert in_scope(q), q


def test_ask_abstains_without_evidence_and_off_topic():
    out = _run("What is late?", FakeLLM(lambda s, u: "{}"), objs={}, hits=[])
    assert out["abstained"] and out["abstain_reason"] == "no evidence retrieved"
    off = _run("Who won the football match yesterday?", FakeLLM(lambda s, u: "{}"))
    assert off["abstained"] and off["abstain_reason"] == "outside procurement scope"
    assert _run("   ", FakeLLM(lambda s, u: "{}"))["abstained"]


def test_ask_handles_non_json_and_no_model_template():
    out = _run("How many cases are late?", FakeLLM(lambda s, u: "Just prose, no JSON."))
    assert out["claims"] == [] and any("not valid JSON" in n for n in out["notes"])
    tmpl = _run("How many cases are late?", NoLLM())
    assert tmpl["model"] == "template-fallback" and tmpl["claims"] and all(c["supported"] for c in tmpl["claims"])


def test_ask_context_prefetches_the_case_the_user_is_looking_at():
    case = {"case_id": "C1", "risk": {"tier": "CRITICAL", "idle_hours": 100, "p_breach": {"value": 0.64, "unit": "probability", "provenance": "experimental", "source": "m"}}}
    out = _run("Why is this case high risk?", FakeLLM(lambda s, u: json.dumps({"answer": "p is 0.64.", "claims": [{"text": "p is 0.64.", "evidence_ids": [_id_for(u, "risk.p_breach")]}]})),
               objs={"/v1/cases/C1": case}, ctx={"page": "case", "case_id": "C1"}, hits=[])
    assert out["claims"][0]["supported"]
    assert out["actions_suggested"][0]["tool"] == "propose_intervention" and out["actions_suggested"][0]["intervention_type"] == "supplier_escalation"


def test_ask_with_the_local_model_is_labelled_free_and_still_goes_through_the_claim_gate(tmp_path):
    import httpx
    from src.v1.llm import OllamaLLM

    def handler(request):
        user = json.loads(request.content)["messages"][1]["content"]
        content = json.dumps({"answer": "544 are late; about 900 are at risk.",
                              "claims": [{"text": "544 open cases are already late.", "evidence_ids": [_id_for(user, "kpis.already_late")]},
                                         {"text": "About 900 cases are at risk.", "evidence_ids": [_id_for(user, "kpis.at_risk_open")]},
                                         {"text": "Everything is fine.", "evidence_ids": []}]})
        return httpx.Response(200, json={"message": {"content": content}, "prompt_eval_count": 900, "eval_count": 60})

    llm = OllamaLLM(guard=SpendGuard(tmp_path / "s.db"), client=httpx.Client(transport=httpx.MockTransport(handler)))
    out = _run("How many cases are late and at risk?", llm)
    assert out["model"] == "ollama:qwen2.5:7b-instruct" and out["cost_usd"] == 0.0
    assert [c["supported"] for c in out["claims"]] == [True, False, False]       # a weak local model gets no free pass: a wrong figure and an uncited claim are both caught


def test_ask_falls_back_to_the_template_when_the_local_model_is_not_running(tmp_path):
    import httpx
    from src.v1.llm import OllamaLLM

    def handler(request):
        raise httpx.ConnectError("refused")

    out = _run("How many cases are late?", OllamaLLM(guard=SpendGuard(tmp_path / "s.db"), client=httpx.Client(transport=httpx.MockTransport(handler))))
    assert out["model"] == "template-fallback" and any("is Ollama running" in n for n in out["notes"]) and out["claims"] and all(c["supported"] for c in out["claims"])


# ── briefing ──
FACTS ={"as_of": "2018-04-16", "facts": [{"id": "late", "fact": "544 open cases are already past their realistic target."},
                                           {"id": "open", "fact": "3089 cases are open at 2018-04-16."},
                                           {"id": "loss", "fact": "Simulated expected loss: EUR 1157673."}]}


def test_briefing_template_and_model_validation_and_cache():
    briefing_mod._CACHE.clear()
    tmpl = briefing_mod.make_brief(FACTS, NoLLM())
    assert tmpl["source"] == "template" and tmpl["label"] and tmpl["items"][0]["sentences"][0]["fact_ids"] == ["late"]
    briefing_mod._CACHE.clear()
    good = {"items": [{"title": "Late work", "sentences": [{"text": "544 open cases are already late.", "fact_ids": ["late"]}]}]}
    out = briefing_mod.make_brief(FACTS, FakeLLM(lambda s, u: json.dumps(good)))
    assert out["source"] == "model" and out["fact_ids_used"] == ["late"]
    assert briefing_mod.make_brief(FACTS, NoLLM())["cached"] is True
    briefing_mod._CACHE.clear()
    bad_fig = {"items": [{"title": "x", "sentences": [{"text": "700 open cases are late.", "fact_ids": ["late"]}]}]}
    assert briefing_mod.make_brief(FACTS, FakeLLM(lambda s, u: json.dumps(bad_fig)))["source"] == "template"
    briefing_mod._CACHE.clear()
    bad_id = {"items": [{"title": "x", "sentences": [{"text": "544 late.", "fact_ids": ["nope"]}]}]}
    assert briefing_mod.make_brief(FACTS, FakeLLM(lambda s, u: json.dumps(bad_id)))["source"] == "template"
    briefing_mod._CACHE.clear()


# ── nl filter ──
def test_a_request_the_api_cannot_serve_is_refused_before_a_model_sees_it():
    never = FakeLLM(lambda s, u: json.dumps({"target": "queue", "filter": {"supplier": "German", "stage": "order"}}))
    for q in ("orders from German suppliers", "open cases due next week", "cases assigned to Maria", "orders created in March"):
        out = nl_filter(q, never)
        assert out["rejected"] and out["source"] == "rules", q
    assert never.calls == []                                   # the model was not even asked


def test_a_model_cannot_invent_a_supplier_name():
    assert validate("queue", {"supplier": "German"}) and validate("queue", {"supplier": "ACME Corp"}) and validate("queue", {"supplier": "vendorID_"})
    assert not validate("queue", {"supplier": "vendorID_0108"})
    invented = FakeLLM(lambda s, u: json.dumps({"target": "queue", "filter": {"supplier": "Contoso"}}))
    out = nl_filter("everything for Contoso", invented)
    assert out["rejected"] and "schema" in out["reason"]


def test_nl_filter_validates_against_the_schema_and_rejects_the_rest():
    assert nl_filter("orders above 50k in invoicing")["filter"] == {"min_value": 50000.0, "stage": "invoicing"}
    assert nl_filter("show orders due next week")["rejected"]
    assert validate("queue", {"stage": "nope"}) and validate("queue", {"foo": 1}) and not validate("suppliers", {"sort": "volume", "min_n": 5})
    evil = FakeLLM(lambda s, u: json.dumps({"target": "queue", "filter": {"owner": "maria"}}))
    r = nl_filter("whatever", evil)
    assert r["rejected"] and r["source"] == "model"
    ok = FakeLLM(lambda s, u: json.dumps({"target": "suppliers", "filter": {"sort": "breach_rate", "min_n": 20}}))
    assert nl_filter("x", ok)["endpoint"] == "/v1/suppliers"


def test_rule_parser_accuracy_on_the_40_phrasing_set_is_a_smoke_test():
    from pathlib import Path
    rows = json.loads(Path("data/eval/nl_filter_40.json").read_text(encoding="utf-8"))
    assert len(rows) == 40
    ok = 0
    for r in rows:
        got = nl_filter(r["q"])
        ok += int(got["rejected"]) if r.get("rejected") else int((not got["rejected"]) and got["target"] == r["target"] and got["filter"] == r["filter"])
    assert ok / len(rows) >= 0.9


# ── pdf ──
def test_pdf_is_wellformed_and_wraps_pages():
    pdf = build_pdf([("h1", "Title"), ("p", "word " * 4000)])
    assert pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF") and pdf.count(b"/Type /Page ") >= 2


# ── eval scoring (no model, no network) ──
def test_eval_scoring_rewards_grounded_cited_answers_and_abstention_on_probes():
    from scripts.eval_v1_ask import PROBES, TEMPLATES, score
    assert {k: len(v) for k, v in TEMPLATES.items()} == {"case": 10, "supplier": 10, "overview": 10, "finance": 10} and len(PROBES) == 10
    item = {"kind": "answer", "entity": "C1", "page": "case"}
    good = {"abstained": False, "answer": "Case C1 is late.", "claims": [{"supported": True, "evidence_ids": ["e1"]}], "evidence": []}
    bad = {"abstained": False, "answer": "x", "claims": [{"supported": False, "evidence_ids": []}], "evidence": []}
    assert score(item, good)["ok"] and score(item, good)["entity"]
    assert not score(item, bad)["ok"] and not score(item, bad)["cited"]
    assert score({"kind": "probe"}, {"abstained": True})["ok"] and not score({"kind": "probe"}, {"abstained": False})["ok"]
