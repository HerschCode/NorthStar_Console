import numpy as np
import pytest

torch = pytest.importorskip("torch")

from src.ml.sequence_model import (  # noqa: E402
    ActivityVocab, SequenceRiskModel, cpu_latency_ms, fit, make_sequence_tensors, predict_proba)


def test_vocab_maps_unseen_activity_to_unk():
    v = ActivityVocab(["a", "b"])
    assert v.encode(["a", "zzz", "b"]) == [2, 1, 3]
    assert len(v) == 4


def test_tensor_shapes_and_time_features():
    v = ActivityVocab(["a", "b"])
    acts, feats = make_sequence_tensors([["a", "b", "a"]], [np.array([0.0, 2.0, 10.0])], v, 3)
    assert acts.shape == (1, 3) and feats.shape == (1, 3, 2)
    assert feats[0, 0, 0] == 0 and feats[0, 0, 1] == 0            # first step: no elapsed, no delta
    assert feats[0, 2, 1] == pytest.approx(np.log1p(8.0), rel=1e-5)  # delta between events 2 and 3


@pytest.mark.parametrize("cell", ["gru", "lstm"])
def test_forward_shape(cell):
    m = SequenceRiskModel(6, 3, cell)
    out = m(torch.zeros(4, 3, dtype=torch.long), torch.zeros(4, 3, 2), torch.zeros(4, 3))
    assert out.shape == (4,)


def _separable(n, seed):
    """Label is decided by whether activity 'slow' appears -- a pattern a sequence model must find."""
    rng = np.random.default_rng(seed)
    v = ActivityVocab(["fast", "slow", "x"])
    seqs = [list(rng.choice(["fast", "slow", "x"], 3)) for _ in range(n)]
    y = torch.tensor([float("slow" in s) for s in seqs])
    t = [np.array([0.0, 1.0, 2.0])] * n
    acts, feats = make_sequence_tensors(seqs, t, v, 3)
    return v, (acts, feats, torch.zeros(n, 1), y)


def test_training_learns_a_separable_pattern_and_beats_chance():
    from sklearn.metrics import roc_auc_score
    v, tr = _separable(600, 0)
    _, va = _separable(150, 1)
    _, te = _separable(300, 2)
    m = fit(SequenceRiskModel(len(v), 1, "gru"), tr, va, epochs=30, seed=0)
    assert roc_auc_score(te[3].numpy(), predict_proba(m, *te[:3])) > 0.9


def test_training_is_deterministic_for_a_seed():
    v, tr = _separable(200, 0)
    _, va = _separable(60, 1)
    ps = []
    for _ in range(2):
        torch.manual_seed(5)
        m = fit(SequenceRiskModel(len(v), 1, "gru"), tr, va, epochs=3, seed=5)
        ps.append(predict_proba(m, *va[:3]))
    assert np.allclose(ps[0], ps[1])


def test_latency_helper_returns_ordered_percentiles():
    p50, p95 = cpu_latency_ms(lambda: sum(range(100)), n_calls=50, warmup=2)
    assert 0 <= p50 <= p95
