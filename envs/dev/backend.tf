terraform {
  backend "gcs" {
    # Bucket and prefix are supplied at init time so nothing project-specific is committed:
    #
    #   terraform init \
    #     -backend-config="bucket=<project-id>-tfstate-dev" \
    #     -backend-config="prefix=northstar/dev"
    #
    # This is the state the CI plan (read) and apply (read/write) identities are allowed to touch.
    # The bootstrap root, which describes those identities, keeps its state in a different bucket.
  }
}
