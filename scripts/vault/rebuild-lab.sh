#!/usr/bin/env bash
# Deliberate destructive rehearsal, scoped to the independent Vault state.
set -euo pipefail
[[ "${1:-}" == --confirm-destroy-vault-lab ]] || { echo 'Requires --confirm-destroy-vault-lab'; exit 1; }
[[ -n "${ANSIBLE_SSM_BUCKET:-}" ]] || { echo 'Set ANSIBLE_SSM_BUCKET'; exit 1; }
[[ -z "${2:-}" || "${2:-}" == --destroy-app-data ]] || { echo 'Unknown option'; exit 1; }
terraform -chdir=terraform/aws/vault init -input=false -backend-config="bucket=${TF_STATE_BUCKET:?Set TF_STATE_BUCKET}"
mapfile -t managed < <(terraform -chdir=terraform/aws/vault state list | grep -v '^data\.' || true)
for resource in "${managed[@]}"; do
  [[ "$resource" == aws_instance.vault || "$resource" == aws_security_group.vault ]] || { echo 'Unexpected resource in Vault state; aborting'; exit 1; }
done
if [[ "${2:-}" == --destroy-app-data ]]; then
  context="$(kubectl config current-context)"
  [[ "$context" == arn:aws:eks:us-east-1:*:cluster/boutique-eq4-eks ]] || { echo 'Expected lab EKS context'; exit 1; }
  kubectl delete namespace boutique-vault-lab --ignore-not-found --wait=true --timeout=5m
  kubectl create namespace boutique-vault-lab
fi
terraform -chdir=terraform/aws/vault destroy -input=false -auto-approve
terraform -chdir=terraform/aws/vault apply -input=false -auto-approve
instance="$(terraform -chdir=terraform/aws/vault output -raw instance_id)"
for attempt in {1..60}; do
  status="$(aws ssm describe-instance-information --region us-east-1 --filters "Key=InstanceIds,Values=$instance" --query 'InstanceInformationList[0].PingStatus' --output text)"
  [[ "$status" == Online ]] && break
  [[ "$attempt" -lt 60 ]] || { echo 'SSM did not come online; check instance profile and HTTPS egress'; exit 1; }
  sleep 5
done
mkdir -p .vault-lab/evidence
(cd ansible && ansible-playbook site.yml --limit "$instance") | tee .vault-lab/evidence/rebuild-install.log
(cd ansible && ansible-playbook site.yml --limit "$instance") | tee .vault-lab/evidence/rebuild-repeat.log
python scripts/vault/check_idempotence.py .vault-lab/evidence/rebuild-repeat.log
echo 'Infrastructure/configuration recreated. Complete PGP init/unseal (or restore snapshot), configure/seed, update CI instance/certificate/token, then deploy the built SHA.'
echo 'If application PVCs were retained, restore original Vault data before CD; do not generate different database passwords.'
