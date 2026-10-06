"""Phase 6 simulation: replay the intervention policy over the held-out cases and load it into
analytics.interventions as is_simulated = TRUE rows.

Policy (config/interventions.yaml): act on the top `capacity_pct` of the held-out window (last 20%
of cases by start time -- not seen in training) by deployed-model breach probability, applying
`default_type`. breached_after is the case's actual outcome from the data. Idempotent: replaces
previous simulated rows, never touches logged (is_simulated = FALSE) rows.

Run: python -m scripts.simulate_interventions [--dry-run]   (needs analytics.interventions:
python -m scripts.setup_database applies sql/schema/005_create_interventions.sql)
"""
import os
import sys

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from src.analytics.sla_analysis import evaluate_sla, load_sla_targets
from src.api.db import load_cases
from src.ml.features import build_features
from src.ml.predict import load_model, predict_sla_risk
from src.roi.ledger import load_policy, roi_summary


def simulated_rows(policy: dict) -> pd.DataFrame:
    evaluated = evaluate_sla(load_cases(), load_sla_targets()).sort_values("start_time").reset_index(drop=True)
    X, _ = build_features(evaluated)
    bundle = load_model()
    # bundle["model"] is what the API serves (the calibrated wrapper); "uncalibrated_model" is the raw forest.
    assert type(bundle["model"]).__name__ == "HeldOutCalibratedClassifier", "expected the served, held-out-calibrated model"
    evaluated["risk"] = predict_sla_risk(X, bundle)["breach_probability"].values
    window = evaluated.iloc[int(len(evaluated) * 0.8):]
    n_act = max(1, int(len(window) * policy["capacity_pct"]))
    top = window.nlargest(n_act, "risk")
    t = policy["default_type"]
    return pd.DataFrame({
        "case_id": top["case_id"].values, "intervention_type": t,
        "risk_at_intervention": top["risk"].round(4).values, "cost": policy["types"][t]["cost"],
        "breached_after": top["sla_breach"].astype(bool).values, "is_simulated": True,
        "notes": "SIMULATION: policy replay over held-out window",
    })


def main(dry_run: bool = False) -> int:
    load_dotenv()
    policy = load_policy()
    rows = simulated_rows(policy)
    print(f"{len(rows)} simulated interventions ({policy['default_type']}, top "
          f"{policy['capacity_pct']:.0%} of the held-out window)")
    print(roi_summary(rows.to_dict(orient="records"), policy)["simulated"])
    if dry_run:
        return 0
    url = (f"postgresql+psycopg2://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}@{os.environ['DB_HOST']}:"
           f"{os.environ['DB_PORT']}/{os.environ['DB_NAME']}?sslmode={os.environ.get('DB_SSLMODE', 'prefer')}")
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM analytics.interventions WHERE is_simulated"))
    rows.to_sql("interventions", engine, schema="analytics", if_exists="append", index=False)
    print("loaded into analytics.interventions")
    return 0


if __name__ == "__main__":
    sys.exit(main("--dry-run" in sys.argv))
