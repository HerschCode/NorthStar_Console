"""Export a compact summary of llm-security-gateway's committed evaluation reports for the Governance page.

Run: python -m scripts.export_governance_summary ../../0_Project3/llm-security-gateway
Writes reports/governance_summary.json. These are *replays of committed measurements*, not live traffic: the Northstar
shell has no live gateway, so the page labels everything as an evaluation result.
"""
import json
import sys
from pathlib import Path


def main(p3: Path):
    fw = json.loads((p3 / "reports/p3_action_firewall.json").read_text(encoding="utf-8"))
    sec = json.loads((p3 / "reports/p3_security_eval.json").read_text(encoding="utf-8"))
    scenarios = []
    for r in fw["results"]:
        calls = [{"tool": c["tool"], "harmful": c["harmful"], "effect": c["effect"], "stage": c["stage"],
                  "reasons": c.get("reasons", [])[:2], "matches_expect": c["matches_expect"]} for c in r["calls"]]
        scenarios.append({"id": r["id"], "category": r["category"], "title": r["title"], "evasion": r["evasion"],
                          "text_layer_detects": r.get("text_deployed_detects_attack"), "calls": calls})
    s = fw["summary"]
    out = {
        "source": "llm-security-gateway committed reports (replay of measurements, not live traffic)",
        "firewall": {"scenarios": s["scenarios"], "harmful": s["harmful_scenarios"], "benign": s["benign_scenarios"],
                     "harmful_excluding_known_evasions": s["harmful_excluding_known_evasions"],
                     "known_evasion_scenarios": s["known_evasion_scenarios"], "by_category": s["by_category"],
                     "ground_truth_agreement": {"calls": s["ground_truth_agreement"]["calls"],
                                                "match": s["ground_truth_agreement"]["match"],
                                                "mismatches": s["ground_truth_agreement"]["mismatches"]}},
        "text_layers": [{"layer": l["layer"], "detection": l["detection"], "false_positive": l["false_positive"],
                         "latency_ms": l["latency_ms"]} for l in sec["layers"]],
        "corpus_size": sec["corpus_size"], "benign_controls": sec["benign_controls"],
        "scenarios": scenarios,
    }
    Path("reports/governance_summary.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("scenarios", len(scenarios), "layers", len(out["text_layers"]))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
