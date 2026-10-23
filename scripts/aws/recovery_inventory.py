"""Read-only recovery inventory; never opens disks or prints credentials."""
import json
import base64
import re
import subprocess
import sys

ACCOUNT = '716142453772'


def application_inventory(execute):
    namespace = 'boutique-vault-lab'
    response = execute(['kubectl', 'get', 'namespace', namespace, '--ignore-not-found', '-o', 'json', '--request-timeout=30s'])
    if response.get('unavailable'):
        return response
    if not response['output'].strip():
        return {'namespace_present': False}
    result = {'namespace_present': True}
    for kind, name in (('configmap', 'local-recovery-verified'), ('pvc', 'data-postgres-0')):
        response = execute(['kubectl', '-n', namespace, 'get', kind, name, '--ignore-not-found', '-o', 'json', '--request-timeout=30s'])
        if response.get('unavailable'):
            result[kind] = response
            continue
        item = json.loads(response['output']) if response['output'].strip() else {}
        if kind == 'configmap':
            result['recovery'] = {key: item.get('data', {}).get(key) for key in
                ('admin_env_verified', 'dump_sha256', 'pvc_uid', 'volume_name', 'volume_handle')}
        else:
            result['pvc'] = {'uid': item.get('metadata', {}).get('uid'), 'phase': item.get('status', {}).get('phase'),
                             'storage_class': item.get('spec', {}).get('storageClassName'),
                             'volume_name': item.get('spec', {}).get('volumeName')}
    volume = result.get('pvc', {}).get('volume_name')
    if volume:
        response = execute(['kubectl', 'get', 'pv', volume, '-o', 'json', '--request-timeout=30s'])
        if response.get('unavailable'):
            result['pv'] = response
        else:
            spec = json.loads(response['output']).get('spec', {})
            result['pv'] = {'claim_uid': spec.get('claimRef', {}).get('uid'),
                            'driver': spec.get('csi', {}).get('driver'), 'volume_handle': spec.get('csi', {}).get('volumeHandle')}
    response = execute(['kubectl', '-n', namespace, 'get', 'pods', '-o', 'json', '--request-timeout=30s'])
    result['pods'] = response if response.get('unavailable') else [
        {'name': item['metadata']['name'], 'phase': item.get('status', {}).get('phase'), 'node': item.get('spec', {}).get('nodeName')}
        for item in json.loads(response['output']).get('items', [])]
    return result


def kubernetes_inventory(name, roles=()):
    def execute(args):
        result = subprocess.run(args, capture_output=True, text=True, timeout=45, check=False)
        if result.returncode:
            code = 'KubernetesAPIUnavailable'
            if 'Forbidden' in result.stderr:
                code = 'KubernetesForbidden'
            elif 'Unauthorized' in result.stderr or 'provide credentials' in result.stderr:
                code = 'KubernetesUnauthorized'
            return {'unavailable': True, 'error_code': code}
        return {'output': result.stdout}
    context = execute(['aws', 'eks', 'update-kubeconfig', '--name', name, '--region', 'us-east-1', '--no-cli-pager'])
    if context.get('unavailable'):
        return context
    response = execute(['kubectl', 'get', 'nodes', '-o', 'json', '--request-timeout=30s'])
    if response.get('unavailable'):
        return response
    nodes = []
    for item in json.loads(response['output']).get('items', []):
        ready = next((value for value in item.get('status', {}).get('conditions', []) if value.get('type') == 'Ready'), {})
        nodes.append({'name': item['metadata']['name'], 'purpose': item['metadata'].get('labels', {}).get('boutique.dev/node-purpose'),
                      'ready': ready.get('status'), 'reason': ready.get('reason'), 'message': ready.get('message', '')[:300]})
    response = execute(['kubectl', '-n', 'kube-system', 'get', 'pods', '-o', 'json', '--request-timeout=30s'])
    if response.get('unavailable'):
        return {'nodes': nodes, 'system_pods': response}
    pods = [{'name': item['metadata']['name'], 'phase': item.get('status', {}).get('phase'),
             'node': item.get('spec', {}).get('nodeName'),
             'waiting_reasons': [container.get('state', {}).get('waiting', {}).get('reason')
                                 for container in item.get('status', {}).get('containerStatuses', [])
                                 if container.get('state', {}).get('waiting')]}
            for item in json.loads(response['output']).get('items', [])]
    response = execute(['kubectl', '-n', 'kube-system', 'get', 'configmap', 'aws-auth', '-o', 'json', '--request-timeout=30s'])
    auth = response if response.get('unavailable') else {
        'present': True,
        'expected_roles_mapped': {role: role in json.loads(response['output']).get('data', {}).get('mapRoles', '')
                                  for role in roles}}
    return {'nodes': nodes, 'system_pods': pods, 'node_auth_mapping': auth,
            'application': application_inventory(execute)}


def query(*args):
    result = subprocess.run(['aws', *args, '--region', 'us-east-1', '--no-cli-pager'],
                            capture_output=True, text=True, timeout=90, check=False)
    if result.returncode:
        error = re.search(r'An error occurred \(([A-Za-z0-9_.-]+)\)', result.stderr)
        failure = {'unavailable': True, 'error_code': error.group(1) if error else 'request_failed'}
        if 'voc-cancel-cred' in result.stderr:
            failure['session_cancelled'] = True
        return failure
    return json.loads(result.stdout) if result.stdout.strip() else {}


def finish(report):
    print(json.dumps(report, indent=2))
    eks = report.get('eks', {})
    if isinstance(eks, dict) and eks.get('unavailable'):
        if eks.get('session_cancelled'):
            sys.exit('Recovery inventory blocked: start the Learner Lab and update all three AWS repository secrets.')
        sys.exit('Recovery inventory incomplete: EKS access was denied or unavailable. Check the active Lab session and permissions.')


def main():
    identity = query('sts', 'get-caller-identity')
    if identity.get('Account') != ACCOUNT:
        sys.exit('Recovery inventory stopped: authentication must use account ' + ACCOUNT + '.')
    report = {'account': ACCOUNT, 'region': 'us-east-1'}
    clusters = query('eks', 'list-clusters')
    report['eks'] = clusters.get('clusters', clusters)
    report['eks_details'] = []
    for name in clusters.get('clusters', []):
        result = query('eks', 'describe-cluster', '--name', name)
        cluster = result.get('cluster', {})
        detail = {field: cluster.get(field) for field in ('name', 'status', 'version', 'health', 'accessConfig')}
        groups = query('eks', 'list-nodegroups', '--cluster-name', name)
        detail['nodegroups'] = []
        for group in groups.get('nodegroups', []):
            result = query('eks', 'describe-nodegroup', '--cluster-name', name, '--nodegroup-name', group)
            node = result.get('nodegroup', {})
            detail['nodegroups'].append({field: node.get(field) for field in
                                        ('nodegroupName', 'status', 'nodeRole', 'health', 'scalingConfig', 'resources')}
                                       if not result.get('unavailable') else result)
            ids = []
            for group_info in node.get('resources', {}).get('autoScalingGroups', []):
                scaling = query('autoscaling', 'describe-auto-scaling-groups', '--auto-scaling-group-names', group_info['name'])
                ids += [instance['InstanceId'] for item in scaling.get('AutoScalingGroups', []) for instance in item.get('Instances', [])]
            if ids:
                machines = query('ec2', 'describe-instances', '--instance-ids', *ids)
                detail['nodegroups'][-1]['instances'] = [{field: machine.get(field) for field in
                    ('InstanceId', 'State', 'PrivateIpAddress', 'PublicIpAddress', 'IamInstanceProfile', 'ImageId')}
                    for reservation in machines.get('Reservations', []) for machine in reservation['Instances']]
                managed = query('ssm', 'describe-instance-information', '--filters', 'Key=InstanceIds,Values=' + ','.join(ids))
                detail['nodegroups'][-1]['ssm'] = [{field: machine.get(field) for field in ('InstanceId', 'PingStatus', 'PlatformName')}
                                                  for machine in managed.get('InstanceInformationList', [])]
                if group == 'boutique-eq4-eks-storage':
                    machine = next((machine for reservation in machines.get('Reservations', [])
                                    for machine in reservation['Instances']), None)
                    if machine:
                        image = query('ec2', 'describe-images', '--image-ids', machine['ImageId'])
                        detail['nodegroups'][-1]['image'] = [{field: entry.get(field) for field in
                            ('ImageId', 'Name', 'Description', 'OwnerId', 'Architecture', 'CreationDate')}
                            for entry in image.get('Images', [])]
                        userdata = query('ec2', 'describe-instance-attribute', '--instance-id', machine['InstanceId'], '--attribute', 'userData')
                        text = base64.b64decode(userdata.get('UserData', {}).get('Value', '')).decode('utf-8', errors='replace')
                        detail['nodegroups'][-1]['bootstrap'] = {
                            'userdata_present': bool(text), 'node_config_present': 'application/node.eks.aws' in text,
                            'cluster_name_present': name in text, 'current_endpoint_present': cluster.get('endpoint', '') in text}
                        console = query('ec2', 'get-console-output', '--instance-id', machine['InstanceId'], '--latest')
                        content = console.get('Output', '')
                        try:
                            content = base64.b64decode(content, validate=True).decode('utf-8', errors='replace')
                        except ValueError:
                            pass
                        lines = [re.sub(r'[A-Za-z0-9+/=_-]{80,}|(?:AKIA|ASIA)[A-Z0-9]{16}', '[redacted]', line)[:300]
                                 for line in content.splitlines() if re.search('nodeadm|kubelet|cloud-init|failed|error', line, re.I)]
                        detail['nodegroups'][-1]['boot_diagnostics'] = lines[-35:] if not console.get('unavailable') else console
        detail['addons'] = []
        addons = query('eks', 'list-addons', '--cluster-name', name)
        for addon in addons.get('addons', []):
            result = query('eks', 'describe-addon', '--cluster-name', name, '--addon-name', addon)
            item = result.get('addon', {})
            detail['addons'].append({field: item.get(field) for field in
                                    ('addonName', 'addonVersion', 'status', 'health', 'configurationValues')}
                                   if not result.get('unavailable') else result)
        if name == 'boutique-eq4-eks' and cluster.get('status') == 'ACTIVE':
            detail['kubernetes'] = kubernetes_inventory(name, [item['nodeRole'] for item in detail['nodegroups'] if item.get('nodeRole')])
        report['eks_details'].append(detail)
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
    recycling = query('ec2', 'list-snapshots-in-recycle-bin')
    report['recycle_bin_snapshots'] = recycling
    report['recycle_bin_volumes'] = query('ec2', 'list-volumes-in-recycle-bin')
    vaults = query('backup', 'list-backup-vaults')
    report['aws_backup'] = vaults if vaults.get('unavailable') else []
    for vault in vaults.get('BackupVaultList', []):
        points = query('backup', 'list-recovery-points-by-backup-vault', '--backup-vault-name', vault['BackupVaultName'])
        report['aws_backup'].append({'vault': vault['BackupVaultName'], 'points': [
            {key: item.get(key) for key in ('RecoveryPointArn', 'ResourceType', 'Status', 'CreationDate')}
            for item in points.get('RecoveryPoints', [])] if not points.get('unavailable') else points})
    keys = query('kms', 'list-keys')
    report['kms_keys'] = keys.get('Keys', keys)
    report['kms_key_metadata'] = []
    for key in keys.get('Keys', []):
        metadata = query('kms', 'describe-key', '--key-id', key['KeyId'])
        report['kms_key_metadata'].append({field: metadata.get('KeyMetadata', {}).get(field)
                                           for field in ('Arn', 'KeyManager', 'KeyState', 'KeyUsage')}
                                          if not metadata.get('unavailable') else metadata)
    buckets = query('s3api', 'list-buckets')
    report['s3_buckets'] = [item['Name'] for item in buckets.get('Buckets', [])] if not buckets.get('unavailable') else buckets
    try:
        role_name = identity['Arn'].split(':assumed-role/', 1)[1].split('/', 1)[0]
        role_result = query('iam', 'get-role', '--role-name', role_name)
        if role_result.get('unavailable'):
            report['kms_permissions_advisory'] = role_result
            finish(report)
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
    finish(report)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit('Recovery inventory stopped; private AWS responses were not logged.')
