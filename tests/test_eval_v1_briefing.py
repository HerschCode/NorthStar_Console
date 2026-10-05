"""The failure classifier of scripts/eval_v1_briefing.py: it must name the same check that src/v1/briefing.py's _valid would fail on."""
import json

from scripts.eval_v1_briefing import classify, variants
from src.v1.briefing import _valid
from src.v1.ask import _parse_json

FACTS = [{"id": "late", "fact": "544 open cases are already past their realistic target."}, {"id": "open", "fact": "3089 cases are open at 2018-04-16."}]


def brief(sentences):
    return json.dumps({"items": [{"title": "t", "sentences": sentences}]})


def test_a_well_formed_cited_brief_is_used():
    text = brief([{"text": "544 cases are late.", "fact_ids": ["late"]}])
    assert classify(text, FACTS) == "used" and _valid(_parse_json(text)["items"], FACTS)


def test_each_way_to_fail_is_named():
    assert classify("Just prose.", FACTS) == "not JSON"
    assert classify(json.dumps({"items": []}), FACTS).startswith("wrong shape")
    assert classify(json.dumps({"items": [{"title": "t", "sentences": []}]}), FACTS).startswith("wrong shape")
    assert classify(brief([{"text": "544 are late.", "fact_ids": []}]), FACTS) == "a sentence cites no fact"
    assert classify(brief([{"text": "544 are late.", "fact_ids": ["nope"]}]), FACTS) == "cites an unknown fact id"
    assert classify(brief([{"text": "900 cases are late.", "fact_ids": ["late"]}]), FACTS) == "a figure is not in the facts it cites"


def test_classifier_and_validator_agree_on_every_case():
    cases = [brief([{"text": "544 cases are late.", "fact_ids": ["late"]}]), brief([{"text": "900 cases are late.", "fact_ids": ["late"]}]), brief([{"text": "544 are late.", "fact_ids": ["x"]}]),
             brief([{"text": "No numbers at all.", "fact_ids": ["open"]}]), "not json"]
    for text in cases:
        parsed = _parse_json(text) or {}
        assert (classify(text, FACTS) == "used") == _valid(parsed.get("items"), FACTS), text


def test_variants_are_the_full_set_and_every_leave_one_out():
    v = variants(FACTS)
    assert [name for name, _ in v] == ["all facts", "without late", "without open"] and [len(fs) for _, fs in v] == [2, 1, 1]
