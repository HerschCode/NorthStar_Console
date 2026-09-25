#!/usr/bin/env python3
"""Generate the identity map for docs/cloud-security.md from the Terraform plans.

Every grant in the tables comes from the plan JSON of the two root modules, so the documentation
cannot drift from the code: CI runs this with --check and fails if the committed document differs
from what the plans say. The prose (why an identity exists, what it can NOT do) is hand-written in
NOTES below, and the script refuses to run if a service account has no entry, so adding an
identity forces a documentation decision.

  scripts/identity-map.py bootstrap.json dev.json                    # print the markdown
  scripts/identity-map.py bootstrap.json dev.json --write FILE       # rewrite the block in FILE
  scripts/identity-map.py bootstrap.json dev.json --check FILE       # exit 1 if FILE is stale

The plan JSONs come from:  scripts/offline-plan.sh <bootstrap|dev> <out.json>
Only the Python standard library is used.
"""
import argparse
import json
import re
import sys
from collections import defaultdict

BEGIN = "<!-- BEGIN_IDENTITY_MAP -->"
END = "<!-- END_IDENTITY_MAP -->"

# account_id -> (why it exists, what it cannot do). Hand-written on purpose.
NOTES = {
    "gateway-sa": (
        "Runtime identity of `llm-security-gateway`, the only public service. Its job is to forward screened requests to the assistant with a Google ID token.",
        "Call `operations-performance`, read any secret or table, or act as another service account. Holds no project-level role.",
    ),
    "assistant-sa": (
        "Runtime identity of `operations-assistant`. Calls the performance API as the agent's tool and reads its own LLM API key.",
        "Reach the database or BigQuery directly, read any other secret, or be invoked by anything except the gateway.",
    ),
    "perf-sa": (
        "Runtime identity of `operations-performance`. Reads its read-only database password and queries the analytics dataset.",
        "Write to BigQuery, read the pipeline's write-capable password, or be invoked by anything except the assistant.",
    ),
    "pipeline-sa": (
        "Runtime identity of the pipeline job. Loads Postgres and BigQuery on a schedule.",
        "Be called from the internet, read the API's password, or invoke any service.",
    ),
    "scheduler-sa": (
        "Lets Cloud Scheduler start the pipeline job.",
        "Do anything other than run that one job.",
    ),
    "gh-deploy-gateway": (
        "Assumed by the gateway repository's deploy workflow, on `main` only, to push an image and roll out a revision.",
        "Change env vars, secrets, IAM, scaling or any other service; deploy from another branch or repository.",
    ),
    "gh-deploy-assistant": (
        "Assumed by the assistant repository's deploy workflow, on `main` only, to push an image and roll out a revision.",
        "Change env vars, secrets, IAM, scaling or any other service; deploy from another branch or repository.",
    ),
    "gh-deploy-perf": (
        "Assumed by the performance repository's deploy workflow, on `main` only, to push an image and roll out a revision of the API and of the pipeline job.",
        "Change env vars, secrets, IAM, scaling or any other service; deploy from another branch or repository.",
    ),
    "gh-tf-plan": (
        "Read-only identity for pull-request plans of `envs/dev`.",
        "Write anything, read a secret's value or table data, or reach the bootstrap state.",
    ),
    "gh-tf-apply": (
        "Applies `envs/dev`. The most powerful identity in the stack.",
        "Touch the budget, the workload identity pool or its own roles (all in the human-only bootstrap root), enable APIs, grant any project role except `roles/bigquery.jobUser`, or be assumed outside the protected GitHub environment.",
    ),
}

# resource type -> (label, attribute that names the target; None = the project)
IAM_TYPES = {
    "google_project_iam_member": ("project", None),
    "google_service_account_iam_binding": ("service account", "service_account_id"),
    "google_service_account_iam_member": ("service account", "service_account_id"),
    "google_cloud_run_v2_service_iam_binding": ("Cloud Run service", "name"),
    "google_cloud_run_v2_service_iam_member": ("Cloud Run service", "name"),
    "google_cloud_run_v2_job_iam_binding": ("Cloud Run job", "name"),
    "google_secret_manager_secret_iam_binding": ("secret", "secret_id"),
    "google_bigquery_dataset_iam_binding": ("BigQuery dataset", "dataset_id"),
    "google_artifact_registry_repository_iam_binding": ("Artifact Registry repository", "repository"),
    "google_storage_bucket_iam_member": ("state bucket", "bucket"),
}


def account_of(email_or_path):
    """'projects/p/serviceAccounts/x@p.iam...' or 'x@p.iam...' -> 'x'."""
    return email_or_path.rsplit("/", 1)[-1].split("@", 1)[0]


def describe_principal(member):
    """Human wording for a principalSet / principal member."""
    m = re.search(r"attribute\.repo_ref/([^/]+/[^/]+)/(refs/.+)$", member)
    if m:
        return f"GitHub Actions in `{m.group(1)}` on `{m.group(2)}`"
    m = re.search(r"attribute\.repo_env/([^/]+/[^/]+)/(.+)$", member)
    if m:
        return f"GitHub Actions in `{m.group(1)}`, environment `{m.group(2)}`"
    m = re.search(r"attribute\.repository/([^/]+/[^/]+)$", member)
    if m:
        return f"GitHub Actions in `{m.group(1)}` (any ref)"
    return f"`{member}`"


class Model:
    def __init__(self):
        self.sas = {}                        # account_id -> {display_name, plane}
        self.grants = defaultdict(set)       # account_id -> {(role, scope, target, conditional)}
        self.invokers = defaultdict(set)     # (kind, name) -> {member}
        self.readers = defaultdict(set)      # secret -> {member}
        self.impersonators = defaultdict(set)  # account_id -> {description}
        self.act_as = defaultdict(set)       # account_id -> {account_id}
        self.attached = defaultdict(set)     # account_id -> {"Cloud Run service `x`"}

    def add_plan(self, plan, plane):
        for rc in plan["resource_changes"]:
            if rc["mode"] != "managed":
                continue
            a = rc["change"].get("after") or {}
            t = rc["type"]

            if t == "google_service_account":
                self.sas[a["account_id"]] = {"display_name": a.get("display_name", ""), "plane": plane}
            elif t in ("google_cloud_run_v2_service", "google_cloud_run_v2_job"):
                tpl = a["template"][0]
                sa = tpl["service_account"] if t.endswith("service") else tpl["template"][0]["service_account"]
                kind = "service" if t.endswith("service") else "job"
                self.attached[account_of(sa)].add(f"Cloud Run {kind} `{a['name']}`")
            elif t == "google_cloud_scheduler_job":
                sa = a["http_target"][0]["oauth_token"][0]["service_account_email"]
                self.attached[account_of(sa)].add(f"Cloud Scheduler job `{a['name']}`")

            if t not in IAM_TYPES:
                continue
            label, attr = IAM_TYPES[t]
            role = a["role"]
            target = "project" if attr is None else a[attr]
            members = [a["member"]] if a.get("member") else list(a.get("members") or [])
            conditional = bool(a.get("condition"))
            short_target = account_of(target) if label == "service account" else target.rsplit("/", 1)[-1]

            for member in members:
                if member.startswith("serviceAccount:"):
                    who = account_of(member.split(":", 1)[1])
                    if label == "service account" and role == "roles/iam.serviceAccountUser":
                        self.act_as[short_target].add(who)
                    else:
                        self.grants[who].add((role, label, short_target, conditional))
                    if label == "Cloud Run service" and role == "roles/run.invoker":
                        self.invokers[("service", short_target)].add(member)
                    if label == "Cloud Run job" and role == "roles/run.invoker":
                        self.invokers[("job", short_target)].add(member)
                    if label == "secret" and role == "roles/secretmanager.secretAccessor":
                        self.readers[short_target].add(member)
                elif member.startswith("principalSet://") and role == "roles/iam.workloadIdentityUser":
                    self.impersonators[short_target].add(describe_principal(member))
                elif member == "allUsers":
                    self.invokers[("service", short_target)].add("allUsers")


def fmt_grant(role, label, target, conditional):
    if label == "project":
        where = "the project"
    elif label == "state bucket":
        where = "the dev Terraform state bucket"  # its real name is per-deployment, not part of the design
    else:
        where = f"{label} `{target}`"
    extra = " *(conditional)*" if conditional else ""
    return f"`{role}` on {where}{extra}"


def render(model):
    missing = sorted(set(model.sas) - set(NOTES))
    if missing:
        sys.exit(f"identity-map: no NOTES entry for: {', '.join(missing)} (describe every service account)")
    stale = sorted(set(NOTES) - set(model.sas))
    if stale:
        sys.exit(f"identity-map: NOTES describes identities that no longer exist: {', '.join(stale)}")

    out = []
    order = ["gateway-sa", "assistant-sa", "perf-sa", "pipeline-sa", "scheduler-sa",
             "gh-deploy-gateway", "gh-deploy-assistant", "gh-deploy-perf", "gh-tf-plan", "gh-tf-apply"]
    order += sorted(set(model.sas) - set(order))

    out.append("### Every service account")
    out.append("")
    out.append("| Identity | Plane | Who can use it | What it can do | Why it exists | What it cannot do |")
    out.append("|---|---|---|---|---|---|")
    for acct in order:
        info = model.sas[acct]
        plane = "workload (`envs/dev`)" if info["plane"] == "dev" else "pipeline (`envs/bootstrap`)"
        users = sorted(model.attached.get(acct, [])) + sorted(model.impersonators.get(acct, []))
        act = sorted(model.act_as.get(acct, []))
        who = "<br>".join(users) if users else "(nothing attaches it)"
        if act:
            who += "<br>*may be attached by:* " + ", ".join(f"`{x}`" for x in act)
        grants = sorted(model.grants.get(acct, []), key=lambda g: (g[1] != "project", g[1], g[2], g[0]))
        can = "<br>".join(fmt_grant(*g) for g in grants) if grants else "nothing beyond what is listed under *who can use it*"
        why, cannot = NOTES[acct]
        out.append(f"| `{acct}` | {plane} | {who} | {can} | {why} | {cannot} |")

    out.append("")
    out.append("### Who can call what")
    out.append("")
    out.append("| Target | Invokers | Note |")
    out.append("|---|---|---|")
    for (kind, name), members in sorted(model.invokers.items()):
        names = sorted("**anyone on the internet** (`allUsers`)" if m == "allUsers" else f"`{account_of(m.split(':', 1)[1])}`" for m in members)
        note = "The only public entry point." if "allUsers" in members else "Anonymous callers receive HTTP 403."
        out.append(f"| Cloud Run {kind} `{name}` | {', '.join(names)} | {note} |")

    out.append("")
    out.append("### Who can read which secret")
    out.append("")
    out.append("| Secret | The one identity that can read it |")
    out.append("|---|---|")
    for secret, members in sorted(model.readers.items()):
        names = ", ".join(f"`{account_of(m.split(':', 1)[1])}`" for m in sorted(members))
        out.append(f"| `{secret}` | {names} |")
    return "\n".join(out)


def splice(text, block):
    if BEGIN not in text or END not in text:
        sys.exit(f"identity-map: {BEGIN} / {END} markers not found in the target file")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n{block}\n{END}{tail}"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("bootstrap_plan")
    p.add_argument("dev_plan")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--write", metavar="FILE")
    g.add_argument("--check", metavar="FILE")
    args = p.parse_args()

    model = Model()
    for path, plane in ((args.bootstrap_plan, "bootstrap"), (args.dev_plan, "dev")):
        with open(path, encoding="utf-8-sig") as fh:
            model.add_plan(json.load(fh), plane)
    block = render(model)

    if args.write or args.check:
        target = args.write or args.check
        with open(target, encoding="utf-8") as fh:
            current = fh.read()
        updated = splice(current, block)
        if args.check:
            if updated != current:
                sys.exit(f"identity-map: {target} is out of date; run scripts/identity-map.py ... --write {target}")
            print(f"identity-map: {target} is up to date")
        else:
            with open(target, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(updated)
            print(f"identity-map: updated {target}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(block)


if __name__ == "__main__":
    main()
