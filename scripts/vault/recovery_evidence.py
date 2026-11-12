"""Public evidence binding a verified recovery to its PVC, EBS volume and Actions run."""
import json
import os
from pathlib import Path
import re

from common import SafeError

ACCOUNT = '716142453772'
REPO = 'Hacket00GTFO/Boutique-Eq4-DevOPS'
PVC = 'data-postgres-0'


def storage_identity(kubectl):
    pvc = json.loads(kubectl('get', 'pvc', PVC, '-o', 'json'))
    if (pvc.get('status', {}).get('phase') != 'Bound'
            or pvc.get('spec', {}).get('storageClassName') != 'boutique-ebs-gp3'):
        raise SafeError('The recovered PostgreSQL PVC must be Bound on boutique-ebs-gp3.')
    volume_name = pvc['spec']['volumeName']
    pv = json.loads(kubectl('get', 'pv', volume_name, '-o', 'json'))
    csi = pv.get('spec', {}).get('csi', {})
    if (csi.get('driver') != 'ebs.csi.aws.com' or not csi.get('volumeHandle')
            or pv.get('spec', {}).get('claimRef', {}).get('uid') != pvc['metadata']['uid']):
        raise SafeError('The PostgreSQL volume does not match its recovered PVC and EBS CSI claim.')
    return {'pvc_uid': pvc['metadata']['uid'], 'volume_name': volume_name, 'volume_handle': csi['volumeHandle']}


def require_storage_recovery(kubectl):
    marker = json.loads(kubectl('get', 'configmap', 'local-recovery-verified', '-o', 'json')).get('data', {})
    if (marker.get('admin_env_verified') != 'true'
            or not re.fullmatch(r'[0-9a-f]{64}', marker.get('dump_sha256', ''))):
        raise SafeError('The PostgreSQL recovery evidence is missing or incomplete.')
    identity = storage_identity(kubectl)
    if any(marker.get(key) != value for key, value in identity.items()):
        raise SafeError('PostgreSQL no longer uses the verified recovery volume; automatic deployment stopped.')
    return identity


def require_recovery(kubectl):
    require_storage_recovery(kubectl)
    # This query validates the database itself, without comparing counts that legitimately grow after releases.
    result = kubectl('exec', '-i', 'postgres-0', '--', 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
                     '-U', 'postgres', '-d', 'boutique', '-At',
                     payload="SELECT (EXISTS (SELECT 1 FROM public.boutique_migrations) AND "
                             "EXISTS (SELECT 1 FROM boutique.admin_users WHERE active AND role='admin'))::text;")
    if result.strip() != 'true':
        raise SafeError('Recovered PostgreSQL schema or active administrator is missing.')


def execution_identity(values=os.environ):
    fields = {'repo': 'GITHUB_REPOSITORY', 'repository_id': 'GITHUB_REPOSITORY_ID',
              'owner_id': 'GITHUB_REPOSITORY_OWNER_ID', 'sha': 'GITHUB_SHA', 'run_id': 'GITHUB_RUN_ID',
              'run_attempt': 'GITHUB_RUN_ATTEMPT', 'instance_id': 'VAULT_INSTANCE_ID',
              'kms_key_arn': 'VAULT_KMS_KEY_ARN', 'ssm_bucket': 'ANSIBLE_SSM_BUCKET'}
    identity = {name: values.get(variable, '') for name, variable in fields.items()}
    if (identity['repo'] != REPO or values.get('GITHUB_REF') != 'refs/heads/main'
            or not re.fullmatch(r'[0-9a-f]{40}', identity['sha'])
            or any(not re.fullmatch(r'[1-9][0-9]*', identity[field]) for field in
                   ('repository_id', 'owner_id', 'run_id', 'run_attempt'))
            or not re.fullmatch(r'i-[0-9a-f]{8,17}', identity['instance_id'])
            or not re.fullmatch(r'arn:aws:kms:us-east-1:' + ACCOUNT + r':key/[0-9a-f-]{36}', identity['kms_key_arn'])
            or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', identity['ssm_bucket'])):
        raise SafeError('Recovery verification requires the expected main Actions execution and infrastructure identity.')
    return dict(identity, account=ACCOUNT, version=1)


def write_record(directory, name, data):
    path = Path(directory) / name
    path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
