import numpy as np
import pandas as pd

from scripts.external_validation import benford_stats, reversed_proxy


def test_benford_stats_accepts_benford_data_and_rejects_uniform():
    rng = np.random.default_rng(0)
    benford = 10 ** rng.uniform(0, 5, 20000)      # log-uniform -> Benford
    uniform = rng.uniform(100, 999, 20000)        # leading digits uniform over 1-9
    assert benford_stats(benford)["chi2_p"] > 0.001 and benford_stats(benford)["mad"] < 0.006
    assert benford_stats(uniform)["chi2_p"] < 1e-6 and benford_stats(uniform)["mad"] > 0.03


def test_reversed_proxy_matches_same_customer_amount_within_window():
    t = pd.Timestamp("2011-01-01")
    ev = pd.DataFrame({"case_id": ["1", "2"], "supplier_id": ["a", "b"], "timestamp": [t, t], "net_worth_eur": [100.0, 100.0]})
    credit = pd.DataFrame({"Invoice": ["C9"], "cust": [1.0], "ts": [t + pd.Timedelta(days=5)], "amount": [-100.0]})
    credit["cust"] = "a"
    # reversed_proxy casts cust via int(): use numeric-string customer ids instead
    ev["supplier_id"] = ["1", "2"]; credit["cust"] = 1
    assert reversed_proxy({"1", "2"}, ev, credit) == {"1"}
