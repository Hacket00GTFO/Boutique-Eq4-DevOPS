"""Read-only recovery inventory; never opens disks or prints credentials."""
import json
import subprocess
import sys

ACCOUNT = '716142453772'


def query(*args):
    result = subprocess.run(['aws', *args, '--region', 'us-east-1', '--no-cli-pager'],
                            capture_output=True, text=True, timeout=90, check=False)
    if result.returncode:
        return {'unavailable': True}
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
    buckets = query('s3api', 'list-buckets')
    report['s3_buckets'] = [item['Name'] for item in buckets.get('Buckets', [])] if not buckets.get('unavailable') else buckets
    try:
        role_name = identity['Arn'].split(':assumed-role/', 1)[1].split('/', 1)[0]
        role = query('iam', 'get-role', '--role-name', role_name)['Role']['Arn']
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
