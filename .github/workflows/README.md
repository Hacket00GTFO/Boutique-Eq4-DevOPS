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
- `ci-docker-ecr.yaml` builds and pushes service images to Amazon ECR after a
  relevant push to `main`.
- `cd-helm-eks.yaml` deploys those images to Amazon EKS after the ECR workflow
  succeeds. It can also be started manually for an existing image tag.
- `deploy-aws-prod.yaml` is a historical ECS deployment. It runs only when the
  `ENABLE_ECS_DEPLOY` repository variable is `true` and a pull request from
  `development` is merged into `main`.

The ECR, EKS, and data-layer workflows use the `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN` repository secrets from the AWS
Academy Learner Lab session. Refresh them when the session changes. The data
layer also uses `TF_STATE_BUCKET`.

For the full Learner Lab route with Terraform, Ansible, Vault, and Helm, follow
the [deployment checklist](../../docs/learner-lab-deployment.md). Install and
initialize Vault before enabling the `ENABLE_VAULT_LAB` repository variable.
