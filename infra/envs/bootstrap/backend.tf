terraform {
  backend "gcs" {
    # Bucket and prefix are supplied at init time so nothing project-specific is committed:
    #
    #   terraform init \
    #     -backend-config="bucket=<project-id>-tfstate-bootstrap" \
    #     -backend-config="prefix=northstar/bootstrap"
    #
    # This bucket is separate from the dev one on purpose: only a human ever touches it. The CI
    # identities that this root creates are never granted access to it, so a compromised pipeline
    # cannot read or rewrite the state that describes its own permissions.
  }
}
