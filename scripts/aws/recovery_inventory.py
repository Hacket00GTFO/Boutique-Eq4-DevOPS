"""Read-only recovery inventory; never opens disks or prints credentials."""
import json
import re
import subprocess
import sys

ACCOUNT = '716142453772'


def query(*args):
    result = subprocess.run(['aws', *args, '--region', 'us-east-1', '--no-cli-pager'],
                            capture_output=True, text=True, timeout=90, check=False)
    if result.returncode:
        error = re.search(r'An error occurred \(([A-Za-z0-9_.-]+)\)', result.stderr)
        return {'unavailable': True, 'error_code': error.group(1) if error else 'request_failed'}
    return json.loads(result.stdout) if result.stdout.strip() else {}


def main():
    identity = query('sts', 'get-caller-identity')
    if identity.get('Account') != ACCOUNT:
        sys.exit('Recovery inventory stopped: authentication must use account ' + ACCOUNT + '.')
    report = {'account': ACCOUNT, 'region': 'us-east-1'}
    clusters = query('eks', 'list-clusters')
    report['eks'] = clusters.get('clusters', clusters)
    instances = query('ec2', 'describe-instances', '--filters', 'Name=tag:Name,Values=boutique-vault-lab')
    report['vault_instances'] = [{'id': item['InstanceId'], 'state': item['State']['Name'],
                                  'volumes': [mapping.get('Ebs', {}).get('VolumeId')
                                              for mapping in item.get('BlockDeviceMappings', [])]}
                                 for reservation in instances.get('Reservations', [])
                                 for item in reservation['Instances']] if not instances.get('unavailable') else instances
    volumes = query('ec2', 'describe-volumes')
    report['ebs_volumes'] = [{key: item.get(key) for key in
                              ('VolumeId', 'State', 'AvailabilityZone', 'Size', 'Encrypted', 'KmsKeyId', 'SnapshotId', 'Tags')}
                             for item in volumes.get('Volumes', [])] if not volumes.get('unavailable') else volumes
    snapshots = query('ec2', 'describe-snapshots', '--owner-ids', 'self')
    report['ebs_snapshots'] = [{key: item.get(key) for key in
                                ('SnapshotId', 'VolumeId', 'State', 'StartTime', 'VolumeSize', 'Encrypted', 'KmsKeyId', 'Tags')}
                               for item in snapshots.get('Snapshots', [])] if not snapshots.get('unavailable') else snapshots
    recycling = query('rbin', 'list-resources', '--resource-type', 'EBS_SNAPSHOT')
    report['recycle_bin_snapshots'] = recycling.get('Resources', recycling)
    vaults = query('backup', 'list-backup-vaults')
    report['aws_backup'] = vaults if vaults.get('unavailable') else []
    for vault in vaults.get('BackupVaultList', []):
        points = query('backup', 'list-recovery-points-by-backup-vault', '--backup-vault-name', vault['BackupVaultName'])
        report['aws_backup'].append({'vault': vault['BackupVaultName'], 'points': [
            {key: item.get(key) for key in ('RecoveryPointArn', 'ResourceType', 'Status', 'CreationDate')}
            for item in points.get('RecoveryPoints', [])] if not points.get('unavailable') else points})
    keys = query('kms', 'list-keys')
    report['kms_keys'] = keys.get('Keys', keys)
    buckets = query('s3api', 'list-buckets')
    report['s3_buckets'] = [item['Name'] for item in buckets.get('Buckets', [])] if not buckets.get('unavailable') else buckets
    try:
        role_name = identity['Arn'].split(':assumed-role/', 1)[1].split('/', 1)[0]
        role_result = query('iam', 'get-role', '--role-name', role_name)
        if role_result.get('unavailable'):
            report['kms_permissions_advisory'] = role_result
            print(json.dumps(report, indent=2))
            return
        role = role_result['Role']['Arn']
        permissions = query('iam', 'simulate-principal-policy', '--policy-source-arn', role,
                            '--action-names', 'kms:CreateKey', 'kms:PutKeyPolicy', 'kms:TagResource',
                            'kms:EnableKeyRotation', '--resource-arns', '*')
        report['kms_permissions_advisory'] = [
            {'action': item['EvalActionName'], 'decision': item['EvalDecision']}
            for item in permissions.get('EvaluationResults', [])] if not permissions.get('unavailable') else permissions
    except (KeyError, IndexError):
        report['kms_permissions_advisory'] = {'unavailable': True}
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit('Recovery inventory stopped; private AWS responses were not logged.')
