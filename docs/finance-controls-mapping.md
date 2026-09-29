# Mapping the firewall's controls to internal-control objectives

This maps mechanisms that already exist in this repo (plus one addition, `require_role_separation`,
made for this work — see `docs/decisions.md`) to the objectives an internal-controls or AP audit
review usually tests for. It exists because "we built an approval queue" and "we built a segregation-of-duties
control" are different claims, and a reviewer from a bank or finance GCC will ask which one this is.

**This is not a compliance claim.** Nothing here is certified against SOX, SOC 2, PCI-DSS or any other
framework, and it could not be: those require organizational controls (background-checked staff in
actually-distinct roles, an audit cadence, training, incident response) that a single-repo demo cannot
have. What follows is a mapping of software mechanisms to the *objectives* those frameworks share, tested
the same way the rest of this project is tested — against a red-team suite and unit tests, with the
result reported honestly rather than asserted.

| Objective | What it means | Mechanism | Where |
|---|---|---|---|
| **Authorization** | A transaction proceeds only with approval from someone with the authority to give it | Default-deny policy: a tool call is refused unless some rule names the caller's role, with per-argument constraints | `gateway/actions/policy.py` |
| **Segregation of duties (baseline)** | The person who requests an action is not the one who approves it | The approver may not be the requester (checked by identity, case-insensitive) | `gateway/actions/approvals.py::decide` |
| **Segregation of duties (auditor-grade)** | The approving *function* is distinct from the requesting function, not just a different individual doing the same job | `require_role_separation: true` refuses an approver whose *role* matches the requester's role, even when they are different people | `gateway/actions/approvals.py`, `gateway/actions/policy.py` |
| **Validity** | A recorded instruction reflects something a real, authorized party actually said, not text that merely claims to | Taint tracking denies or flags a write-tool argument that was copied from untrusted content (a retrieved document, an uploaded file) without corroboration from a trusted source — this is the mechanical answer to a poisoned "CFO approved" note or a fake policy update embedded in text the agent reads | `gateway/actions/taint.py` |
| **Completeness / audit trail** | Every decision is recorded, not just the ones that succeeded | Every `authorize()` call is appended to a JSONL audit log (PII-redacted) regardless of outcome | `gateway/actions/firewall.py::_audit` |
| **Safeguarding of assets** | A single compromised session cannot cause unbounded damage | Per-session write budget (`max_per_session`); the approval queue itself is capped (200 pending) so it cannot be exhausted as a denial-of-service on legitimate approvals | `gateway/actions/firewall.py`, `gateway/actions/approvals.py` |
| **Control testing** | The controls above actually hold against realistic attack framing, not just clean inputs | `finance_*` categories in `redteam/promptfoo/tests.yaml` (27 scenarios: vendor-bank-change/BEC fraud, threshold evasion, segregation-of-duties bypass requests, fake-authority notes, data over-reach, encoded and multilingual variants) | `redteam/promptfoo/tests.yaml`, `gateway/adapters/stub_ops_agent.py` |

## What this does not cover

- **The tools themselves don't exist yet.** `hold_payment` and `release_payment` are planned
  operations-assistant tools (see `docs/decisions.md`); nothing here is wired to a real payment system.
  `require_role_separation` is tested against a stand-in tool (`tests/test_action_firewall.py`) so the
  mechanism is ready the day those tools land, but there is no policy entry for them yet — their argument
  shape isn't settled.
- **"Role" here is still employee/manager/admin**, not a real finance org chart (AP clerk, controller,
  treasury). Auditor-grade segregation of duties in a real deployment means these roles map onto actual,
  distinct job functions with their own hiring/training controls — software can enforce that two *labels*
  differ, not that the organization behind them is actually separated.
- **No amounts, thresholds or approval limits are enforced here.** The split-purchase and
  threshold-evasion red-team scenarios test whether the *text pipeline* catches an attempt to talk the
  agent into evading a threshold; they do not test an actual dollar-amount policy check, because there is
  no real payment tool with a real amount argument to check yet.
- **This is not fraud detection.** Nothing here scores a transaction as suspicious based on its own
  attributes (duplicate invoice numbers, Benford's-law deviation, split purchases under a real threshold);
  that kind of anomaly detection belongs in operations-performance (P1), against real transaction data,
  and is out of scope for a text/action-firewall gateway.
