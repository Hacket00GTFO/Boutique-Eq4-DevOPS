"""Recreate owned Learner Lab infrastructure; never destroy or replace resources."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from prepare_vault_kms import ACCOUNT, PreparationError, aws, prepare

STATE_BUCKET = f'boutique-eq4-tfstate-{ACCOUNT}'
TRANSFER_BUCKET = f'boutique-vault-ssm-{ACCOUNT}'
ROOT = Path(__file__).resolve().parents[2]


def command(args, timeout=1800):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        # No application settings are supplied to Terraform, but still keep error output bounded.
        combined = __import__('re').sub(r'\x1b\[[0-9;]*m', '', result.stdout + '\n' + result.stderr)
        errors = [line.strip() for line in combined.splitlines()
                  if 'Error:' in line or 'AccessDenied' in line or 'Unauthorized' in line]
        print('\n'.join(errors[:8]), flush=True)
        raise PreparationError('Infrastructure command failed; state and existing resources were retained.')
    return result.stdout


def bucket(name, component, versioned):
    buckets = aws('s3api', 'list-buckets').get('Buckets', [])
    if name not in [item['Name'] for item in buckets]:
        aws('s3api', 'create-bucket', '--bucket', name)
        aws('s3api', 'put-bucket-tagging', '--bucket', name, '--tagging', json.dumps({'TagSet': [
            {'Key': 'Project', 'Value': 'boutique-eq4'}, {'Key': 'Component', 'Value': component}]}))
    aws('s3api', 'head-bucket', '--bucket', name, '--expected-bucket-owner', ACCOUNT)
    tags = aws('s3api', 'get-bucket-tagging', '--bucket', name).get('TagSet', [])
    if {'Key': 'Component', 'Value': component} not in tags:
        raise PreparationError('Refusing to reconfigure an unrelated bucket.')
    aws('s3api', 'put-public-access-block', '--bucket', name, '--public-access-block-configuration', json.dumps({
        'BlockPublicAcls': True, 'IgnorePublicAcls': True, 'BlockPublicPolicy': True, 'RestrictPublicBuckets': True}))
    aws('s3api', 'put-bucket-encryption', '--bucket', name, '--server-side-encryption-configuration', json.dumps({
        'Rules': [{'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]}))
    if versioned:
        aws('s3api', 'put-bucket-versioning', '--bucket', name, '--versioning-configuration', 'Status=Enabled')
    elif aws('s3api', 'get-bucket-versioning', '--bucket', name).get('Status'):
        raise PreparationError('The Ansible transfer bucket must not retain historical versions.')
    else:
        aws('s3api', 'put-bucket-lifecycle-configuration', '--bucket', name,
            '--lifecycle-configuration', json.dumps({'Rules': [{'ID': 'expire-ssm-transfers', 'Status': 'Enabled',
                'Filter': {'Prefix': ''}, 'Expiration': {'Days': 1}, 'AbortIncompleteMultipartUpload': {'DaysAfterInitiation': 1}}]}))
    aws('s3api', 'put-bucket-policy', '--bucket', name, '--policy', json.dumps({
        'Version': '2012-10-17', 'Statement': [{'Sid': 'RequireTLS', 'Effect': 'Deny', 'Principal': '*',
            'Action': 's3:*', 'Resource': [f'arn:aws:s3:::{name}', f'arn:aws:s3:::{name}/*'],
            'Condition': {'Bool': {'aws:SecureTransport': 'false'}}}]}))


def tf(stack, *args, timeout=1800):
    return command(['terraform', '-chdir=terraform/aws/' + stack, *args], timeout)


def check_plan(plan, key):
    allowed = {'aws_vpc', 'aws_internet_gateway', 'aws_subnet', 'aws_route_table', 'aws_route_table_association',
               'aws_eks_cluster', 'aws_launch_template', 'aws_eks_node_group', 'aws_ecr_repository',
               'aws_instance', 'aws_security_group', 'aws_kms_key'}
    additions = 0
    for change in plan.get('resource_changes', []):
        actions = change.get('change', {}).get('actions', [])
        if change.get('mode') == 'data' or actions == ['no-op']:
            continue
        if change.get('type') not in allowed or 'delete' in actions:
            raise PreparationError('The infrastructure plan includes a replacement, deletion or unexpected resource.')
        if actions == ['create']:
            additions += 1
        elif actions == ['update'] and change.get('type') == 'aws_kms_key':
            if change['change'].get('before', {}).get('arn') != key:
                raise PreparationError('The plan would modify an unexpected KMS key.')
        else:
            raise PreparationError('Existing infrastructure changed; review the plan before proceeding.')
    return additions


def apply(stack, key):
    plan_path = str(ROOT / f'.vault-lab/{stack}-rebuild.tfplan')
    tf(stack, 'plan', '-input=false', '-no-color', '-out=' + plan_path)
    plan = json.loads(tf(stack, 'show', '-json', plan_path))
    additions = check_plan(plan, key)
    print(f'PASS: {stack} plan reviewed: {additions} additions, no deletions or replacements.', flush=True)
    tf(stack, 'apply', '-input=false', '-no-color', plan_path)
    return {name: value['value'] for name, value in json.loads(tf(stack, 'output', '-json')).items()}


def main():
    os.umask(0o077)
    key = prepare()
    (ROOT / '.vault-lab').mkdir(mode=0o700, exist_ok=True)
    print('Preparing private state and SSM buckets.', flush=True)
    bucket(STATE_BUCKET, 'terraform-state', True)
    bucket(TRANSFER_BUCKET, 'boutique-vault-ssm', False)
    for stack in ('k8s', 'vault'):
        tf(stack, 'init', '-input=false', '-reconfigure', '-backend-config=bucket=' + STATE_BUCKET,
           '-backend-config=use_lockfile=true')
    print('Planning and provisioning EKS/ECR with the existing Lab roles.', flush=True)
    cluster = apply('k8s', key)
    variables = {'vpc_id': cluster['vpc_id'], 'subnet_id': cluster['vault_subnet_id'],
                 'admin_cidrs': ['127.0.0.1/32'], 'enable_auto_unseal': True}
    (ROOT / 'terraform/aws/vault/terraform.tfvars.json').write_text(json.dumps(variables))
    state = tf('vault', 'state', 'list')
    if 'aws_kms_key.vault[0]' not in state.splitlines():
        tf('vault', 'import', '-input=false', 'aws_kms_key.vault[0]', key)
    print('Planning and provisioning the independent Vault instance using the persistent KMS key.', flush=True)
    vault = apply('vault', key)
    instance = vault['instance_id']
    for _ in range(60):
        response = aws('ssm', 'describe-instance-information', '--filters', f'Key=InstanceIds,Values={instance}')
        if any(item.get('PingStatus') == 'Online' for item in response.get('InstanceInformationList', [])):
            break
        time.sleep(5)
    else:
        raise PreparationError('The created Vault instance did not become available through SSM.')
    print('Installing the EBS CSI add-on on the dedicated LabRole storage node.', flush=True)
    version = aws('eks', 'describe-addon-versions', '--addon-name', 'aws-ebs-csi-driver',
                  '--kubernetes-version', '1.35')['addons'][0]['addonVersions'][0]['addonVersion']
    config = (ROOT / 'kubernetes-manifests/aws-ebs-csi-addon.json').read_text()
    addons = aws('eks', 'list-addons', '--cluster-name', cluster['cluster_name']).get('addons', [])
    if 'aws-ebs-csi-driver' not in addons:
        aws('eks', 'create-addon', '--cluster-name', cluster['cluster_name'], '--addon-name', 'aws-ebs-csi-driver',
            '--addon-version', version, '--configuration-values', config)
    command(['aws', 'eks', 'wait', 'addon-active', '--cluster-name', cluster['cluster_name'],
             '--addon-name', 'aws-ebs-csi-driver', '--region', 'us-east-1'], timeout=900)
    command(['aws', 'eks', 'update-kubeconfig', '--name', cluster['cluster_name'], '--region', 'us-east-1'])
    command(['kubectl', 'wait', '--for=condition=Ready', 'nodes', '--all', '--timeout=300s'])
    command(['kubectl', 'apply', '-f', 'kubernetes-manifests/aws-ebs-gp3-storageclass.yaml'])
    report = {'account': ACCOUNT, 'region': 'us-east-1', 'cluster_name': cluster['cluster_name'],
              'vault_instance_id': instance, 'vault_kms_key_arn': key, 'state_bucket': STATE_BUCKET,
              'ssm_bucket': TRANSFER_BUCKET, 'vault_initialized': False}
    (ROOT / '.vault-lab/infrastructure.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)
    print('PASS: infrastructure and persistent storage ready. Vault initialization and data restoration remain required.')


if __name__ == '__main__':
    try:
        main()
    except PreparationError as error:
        sys.exit('Infrastructure reconstruction stopped: ' + str(error))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit('Infrastructure reconstruction stopped; private responses suppressed and state retained.')
