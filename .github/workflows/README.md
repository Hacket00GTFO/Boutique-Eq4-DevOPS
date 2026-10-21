# GitHub Actions workflows

This repository deploys to AWS. The inherited Google Cloud staging, cleanup,
and release workflows are not used here.

## Pull requests

- `ci-pr.yaml` runs Go and C# tests for pull requests targeting `development`
  or `main`, using the same reusable `ci-code-tests.yaml` required by release.
- Other validation workflows check Terraform, Helm, Kustomize, and manifests as
  their path filters allow. Open pull requests do not deploy to staging.

## AWS deployment

- `deploy-aws-backend.yaml` applies the AWS data layer when relevant Terraform
  files change on `main`, or when run manually.
- `ci-docker-ecr.yaml` starts on every push to `main`, so the run appears on
  main. It authorizes release only when the exact SHA belongs to a merged
  same-repository `development` PR, including documentation-only merges. It validates the
  immutable merge SHA, tests it, then builds and pushes all twelve ECR images.
- Its final job calls `cd-helm-eks.yaml` with that same SHA after every image
  succeeds. CD resumes known Lab instances, applies Ansible, obtains Vault
  secrets through GitHub OIDC, and runs Helm, migration and functional smoke.
  Both workflows also allow manual runs on `main`; direct CD requires a full
  release SHA whose images already exist.
- `deploy-aws-prod.yaml` is a historical ECS deployment. It requires a manual
  dispatch on `main` and `ENABLE_ECS_DEPLOY=true`.

The ECR, EKS, and data-layer workflows use the `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN` repository secrets from the AWS
Academy Learner Lab session. Refresh them when the session changes. The data
layer also uses `TF_STATE_BUCKET`.

For the full Learner Lab route with Terraform, Ansible, Vault, and Helm, follow
the [deployment checklist](../../docs/learner-lab-deployment.md). Install and
initialize Vault, then complete the one-time
[verified recovery and activation](../../docs/aws-automatic-deployment.md).
`DATA_RECOVERY_VERIFIED` and `VAULT_AUTOMATION_READY` in the `vault-lab`
environment gate publication and CD. Native KMS, OIDC and verified TLS are
mandatory; there is no fallback to direct Kubernetes Secret delivery. CI reads Vault
with a per-job token; no stored `VAULT_CI_TOKEN` or static CA variable is needed.
Keep AWS credentials only as repository secrets, with no stale environment
overrides. The platform Lab must be active; GitHub cannot extend its timer.

Manual operations are explicit: `inventory` on `development` is read-only;
`initialize-local-data` on `main` restores the accepted backup during the initial
setup window; `release` on `main` builds and deploys the current main SHA. Other
recovery operations remain opt-in. See the
[recovery report](../../docs/recovery-2026-10-20.md) for the retained resources.

Release runs use `queue: max` with `cancel-in-progress: false` so up to 100
pending merges can wait instead of replacing the previous pending run.
Bootstrap and CD also share a deployment lock. This is supported by
[GitHub Actions concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
Actionlint 1.7.12 does not yet recognize `queue`; when using that version,
ignore only `unexpected key "queue" for "concurrency" section`, inspect that
each occurrence is `max` with cancellation disabled, and require GitHub's
workflow validation. Do not suppress other syntax or expression diagnostics.
