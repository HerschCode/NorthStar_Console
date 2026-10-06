# Security policy

This repository is infrastructure-as-code for a portfolio project, and it is also written to be read as a worked example of IAM hardening. If you find a weakness in the design (for example a way for the CI identities to escalate, or a case a policy rule misses), I would like to hear about it.

**Please report it privately** through GitHub's "Report a vulnerability" (Security tab) rather than a public issue.

What is in scope: the Terraform, the policies, the CI workflows and the identity design described in `docs/cloud-security.md`.

What is out of scope: the application code (those are separate repositories) and denial-of-service against a running deployment.

There are no secrets in this repository by design; if you find one, that is a bug and I want to know.
