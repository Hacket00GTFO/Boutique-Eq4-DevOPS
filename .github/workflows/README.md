# GitHub Actions workflows

This repository deploys to AWS. The inherited Google Cloud staging, cleanup,
and release workflows are not used here.

## Pull requests

- `ci-pr.yaml` runs Go and C# tests for pull requests targeting `main`.
- Other validation workflows check Terraform, Helm, Kustomize, and manifests as
  their path filters allow. Open pull requests do not deploy to staging.

## AWS deployment

- `deploy-aws-backend.yaml` applies the AWS data layer when relevant Terraform
  files change on `main`, or when run manually.
- `ci-docker-ecr.yaml` runs when a same-repository `development` pull request is
  merged into `main`, including documentation-only merges. It validates the
  immutable merge SHA, tests it, then builds and pushes all twelve ECR images.
- Its final job calls `cd-helm-eks.yaml` with that same SHA after every image
  succeeds. CD resumes known Lab instances, applies Ansible, obtains Vault
  secrets through GitHub OIDC, and runs Helm, migration and functional smoke.
  Both workflows also allow manual runs on `main`; direct CD requires a full
  release SHA whose images already exist.
- `deploy-aws-prod.yaml` is a historical ECS deployment. It runs only when the
  `ENABLE_ECS_DEPLOY` repository variable is `true` and a pull request from
  `development` is merged into `main`.

The ECR, EKS, and data-layer workflows use the `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN` repository secrets from the AWS
Academy Learner Lab session. Refresh them when the session changes. The data
layer also uses `TF_STATE_BUCKET`.

For the full Learner Lab route with Terraform, Ansible, Vault, and Helm, follow
the [deployment checklist](../../docs/learner-lab-deployment.md). Install and
initialize Vault, then complete the one-time
[KMS migration and OIDC setup](../../docs/aws-automatic-deployment.md).
`VAULT_AUTOMATION_READY` in the `vault-lab` environment gates CD. CI reads Vault
with a per-job token; no stored `VAULT_CI_TOKEN` or static CA variable is needed.
Keep AWS credentials only as repository secrets, with no stale environment
overrides. The platform Lab must be active; GitHub cannot extend its timer.
