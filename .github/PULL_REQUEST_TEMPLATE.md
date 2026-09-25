## What and why

<!-- One or two sentences. -->

## Checklist

CI covers formatting, validation, module tests, linting, IaC scanning and the policy gate. These are the things CI cannot judge:

- [ ] **IAM:** does this add or widen any grant? If so, is it on the narrowest resource, and is the reasoning in the PR?
- [ ] **Policy:** did I change anything under `policies/`? A new exception or allow-list entry needs its justification written next to it.
- [ ] **Cost:** does this add a resource that bills while idle? (`COST-001` blocks the known ones; anything else deserves a look.)
- [ ] **Secrets:** no secret value, key file or token in the diff, in a plan, or in a comment.
- [ ] **Blast radius:** if this went wrong, what breaks, and how do I roll it back?
