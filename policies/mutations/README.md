# Policy mutation tests

Each `*.tfmutation` file here is a **deliberately bad change**: the kind of mistake a tired
engineer really makes ("just give the pipeline editor so it works", "make the service public to
debug it"). `scripts/policy-mutation-tests.sh` drops each one into a throw-away copy of the
repository, runs a real `terraform plan` (offline, no credentials) and feeds the resulting JSON to
conftest. The gate **must fail**, with the rule the file names.

Why this exists on top of the Rego unit tests in `policies/tests/`: the unit tests use fixtures I
wrote by hand, so they only prove the rules match *my assumptions* about the plan JSON. A mutation
test proves the rules match what Terraform and the Google provider really emit, and it keeps
proving it after a provider upgrade changes the shape of a plan. A policy that silently stops
matching is worse than no policy, because it looks like protection.

## File format

```hcl
# root: dev                # which root module to plan: bootstrap | dev
# expect: RUN-001          # rule IDs that must appear, comma separated
#   (or)
# expect: plan-fails       # the change must be rejected by Terraform itself (a module guard)
# what: one line on the mistake being simulated
<Terraform>
```

* `NAME.tfmutation` is added to the root module as a new file.
* `NAME.override.tfmutation` is added as a Terraform *override* file, so it can change arguments of
  an existing `module` call (for example open `run_assistant` to `allUsers`).
* Mutations must be self-contained: use `var.project_id`, `var.project_number`, `var.region` and
  literals. They are never applied; they only need to plan.

Files use the `.tfmutation` extension so `terraform fmt`, tflint, checkov and trivy do not treat
these intentionally insecure snippets as real configuration.
