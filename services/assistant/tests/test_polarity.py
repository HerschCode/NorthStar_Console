from src.evaluation.claim_support import answer_supported
from src.evaluation.polarity import polarity_conflicts

EV = ["Rework time is excluded from SLA breach metrics while a case is on hold."]


def test_conflict_detected_one_directionally():
    assert polarity_conflicts("Rework time is included in SLA breach metrics.", EV) == [("included", "excluded")]
    assert polarity_conflicts("Rework time is excluded from SLA breach metrics.", EV) == []   # same side: fine
    assert polarity_conflicts("Hold time counts toward the metrics.", ["Hold time is not shown."]) == []  # evidence never uses the opposite word


def test_gate_enforces_only_when_asked(monkeypatch):
    ans = "Rework time is included in SLA breach metrics while a case is on hold."
    monkeypatch.delenv("GATE_POLARITY", raising=False)
    assert answer_supported(ans, EV, 0.5)[0] is True            # default: unchanged behaviour
    assert answer_supported(ans, EV, 0.5, enforce_polarity=True)[0] is False
    monkeypatch.setenv("GATE_POLARITY", "1")
    assert answer_supported(ans, EV, 0.5)[0] is False
