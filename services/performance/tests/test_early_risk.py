import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.ml import early_risk as er

client = TestClient(app, headers={"X-API-Key": "test-key-do-not-use-in-production"})
META = json.loads((er.MODEL_DIR / er.META_NAME).read_text())


# ---------- featurization ----------

def test_sequence_arrays_match_the_torch_research_code():
    torch = pytest.importorskip("torch")
    from src.ml.sequence_model import ActivityVocab, make_sequence_tensors

    vocab = ActivityVocab(["a", "b", "c"])
    acts = ["a", "b", "zzz", "c", "a"]
    offs = np.array([0.0, 2.5, 30.0, 31.0, 400.0])
    t_acts, t_feats = make_sequence_tensors([acts], [offs], vocab, 4)
    n_acts, n_feats = er.sequence_arrays(acts, offs, vocab.index, 4)
    assert (t_acts[0].numpy() == n_acts).all()
    assert np.allclose(t_feats[0].numpy(), n_feats)


def _cases():
    return pd.DataFrame({
        "case_id": ["a", "b", "c", "d"], "supplier_id": ["S", "S", "S", "T"],
        "category": ["x", "x", "x", "x"],
        "start_time": pd.to_datetime(["2020-01-01", "2020-01-05", "2020-01-20", "2020-01-25"]),
        "end_time": pd.to_datetime(["2020-01-10", "2020-01-30", "2020-02-10", "2020-02-01"]),
        "cycle_time_hours": [216.0, 600.0, 500.0, 100.0]})


def test_supplier_history_is_strictly_causal_and_single_case_version_agrees():
    targets = {"default": 300.0}
    cases = _cases()
    hist = er.supplier_history(cases, targets, prior=0.5)
    # b starts 01-05 but a only ends 01-10 (not yet ended), so neither a nor b has usable history -> prior
    assert hist.iloc[0] == 0.5 and hist.iloc[1] == 0.5          # nothing ended before a or b started
    assert hist.iloc[2] == pytest.approx(0.0)                    # c starts 01-20: 'a' ended (216h < 300 -> no breach); b ends 01-30 -> not yet
    assert hist.iloc[3] == 0.5                                   # supplier T has no history
    for i in range(len(cases)):
        assert er.supplier_history_for_case(cases, cases.iloc[i], targets, 0.5) == pytest.approx(hist.iloc[i])


def test_static_vector_layout():
    meta = {"categories": ["a", "b"]}
    v = er.static_vector(meta, "2020-03-04 12:00", "b", 0.25)
    assert v.tolist() == pytest.approx([12 / 23, 2 / 6, 3 / 12, 0.25, 0.0, 1.0])
    assert er.static_vector(meta, "2020-03-04", None, 0.1)[-2:].tolist() == [0.0, 0.0]   # unknown category: all zeros


# ---------- ONNX artifact ----------

def test_committed_onnx_models_match_pytorch_to_float_precision():
    for k, m in META["models"].items():
        assert m["onnx_vs_torch_max_abs_logit_diff"] < 1e-5, (k, m["onnx_vs_torch_max_abs_logit_diff"])


def test_exporting_a_fresh_ensemble_to_onnx_reproduces_pytorch_outputs(tmp_path):
    torch = pytest.importorskip("torch")
    pytest.importorskip("onnx")
    import onnxruntime as ort
    from scripts.export_early_risk import LogitEnsemble
    from src.ml.sequence_model import SequenceRiskModel

    torch.manual_seed(0)
    k, static_dim = 3, 5
    ens = LogitEnsemble([SequenceRiskModel(12, static_dim, "gru") for _ in range(3)]).eval()
    acts, feats, static = torch.randint(0, 12, (7, k)), torch.randn(7, k, 2), torch.randn(7, static_dim)
    path = tmp_path / "e.onnx"
    torch.onnx.export(ens, (acts[:1], feats[:1], static[:1]), str(path), input_names=["acts", "step_feats", "static"],
                      output_names=["logit"], opset_version=17,
                      dynamic_axes={n: {0: "batch"} for n in ("acts", "step_feats", "static", "logit")})
    got = ort.InferenceSession(str(path)).run(None, {"acts": acts.numpy(), "step_feats": feats.numpy(),
                                                     "static": static.numpy()})[0]
    with torch.no_grad():
        want = ens(acts, feats, static).numpy()
    assert np.max(np.abs(got - want)) < 1e-5 and got.shape == (7,)


def test_serving_path_does_not_import_torch():
    code = ("import sys; import src.api.main; from src.ml.early_risk import EarlyRiskModel; "
            "EarlyRiskModel.load(); assert 'torch' not in sys.modules, 'torch leaked into serving'")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert r.returncode == 0, r.stderr[-500:]


# ---------- scoring and endpoint ----------

CAT = next(iter(META["categories"]))
TARGET = er.target_hours(META["target"]["targets_hours"], CAT)
ACTS = list(META["vocab"])[:6]


def _case_and_events(n_events=5, gap_hours=10.0, case_id="C-TEST"):
    start = pd.Timestamp("2018-06-01 09:00")
    cases = pd.DataFrame({"case_id": [case_id], "supplier_id": ["S1"], "category": [CAT], "start_time": [start],
                          "end_time": [start + pd.Timedelta(hours=TARGET * 2)], "cycle_time_hours": [TARGET * 2]})
    ev = pd.DataFrame({"case_id": case_id, "activity": [ACTS[i % len(ACTS)] for i in range(n_events)],
                       "timestamp": [start + pd.Timedelta(hours=gap_hours * i) for i in range(n_events)]})
    return cases, ev


def _get(cases, ev, url):
    with patch("src.api.routes.load_cases", return_value=cases), patch("src.api.routes.load_case_events", return_value=ev):
        return client.get(url)


def test_endpoint_returns_probability_with_its_own_auc_and_an_early_warning_label():
    cases, ev = _case_and_events()
    r = _get(cases, ev, "/orders/C-TEST/early-risk?k=3")
    assert r.status_code == 200, r.text
    b = r.json()
    assert 0.0 <= b["breach_probability"] <= 1.0 and b["k"] == 3
    m = META["models"]["3"]
    assert b["test_roc_auc"] == pytest.approx(m["test_roc_auc"], abs=1e-4) and b["n_test"] == m["n_test"]
    assert "EARLY-WARNING" in b["warning"] and "p75" in b["target_definition"]


def test_scoring_is_deterministic_and_depends_on_the_prefix():
    cases, ev = _case_and_events()
    a = _get(cases, ev, "/orders/C-TEST/early-risk?k=3").json()["breach_probability"]
    b = _get(cases, ev, "/orders/C-TEST/early-risk?k=3").json()["breach_probability"]
    _, slow = _case_and_events(gap_hours=TARGET / 4)
    c = _get(cases, slow, "/orders/C-TEST/early-risk?k=3").json()["breach_probability"]
    assert a == b and a != c


def test_endpoint_refuses_cases_that_cannot_be_scored():
    cases, ev = _case_and_events(n_events=2)
    assert _get(cases, ev, "/orders/C-TEST/early-risk?k=3").status_code == 409          # too few events yet
    cases, late = _case_and_events(gap_hours=TARGET)                                    # already past target at event 3
    r = _get(cases, late, "/orders/C-TEST/early-risk?k=3")
    assert r.status_code == 409 and "determined" in r.json()["detail"]
    assert _get(cases, ev, "/orders/NOPE/early-risk?k=3").status_code == 404
    assert _get(cases, ev, "/orders/C-TEST/early-risk?k=9").status_code == 422
    assert _get(cases, ev, "/orders/C-TEST/early-risk?k=4").status_code == 422          # only k in {2,3,5} exported


def test_unseen_activity_and_category_do_not_crash():
    cases, ev = _case_and_events()
    ev["activity"] = "A brand new activity"
    cases["category"] = "A brand new category"
    assert _get(cases, ev, "/orders/C-TEST/early-risk?k=2").status_code == 200


def test_lifespan_runs_startup_seed_instead_of_the_deprecated_on_event_hook():
    with patch("src.api.main.seed_pipeline_runs", new=AsyncMock()) as seed:
        with TestClient(app):
            pass
    seed.assert_awaited_once()
