"""Resume existing, explicitly identified Lab instances without creating resources."""
import argparse
import json
import re
import subprocess
import sys
import time

from common import SafeError


def aws(*args, timeout=60):
    result = subprocess.run(['aws', *args, '--region', 'us-east-1', '--no-cli-pager'],
                            capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        if 'voc-cancel-cred' in result.stderr:
            raise SafeError('Learner Lab is inactive; start the Lab and replace all three AWS credentials.')
        if 'ExpiredToken' in result.stderr:
            raise SafeError('AWS credentials expired; replace all three with values from the active Lab.')
        if args[0] == 'eks' and 'ResourceNotFoundException' in result.stderr:
            raise SafeError('An expected EKS resource is missing in us-east-1. Verify the AWS account and existing Terraform state before recreating infrastructure.')
        if args[0] == 'ec2' and 'InvalidInstanceID.NotFound' in result.stderr:
            raise SafeError('An expected EC2 instance is missing. Verify the AWS account and retained Vault data before recreating infrastructure.')
        raise SafeError('AWS preflight failed; verify active Lab credentials and required service permissions.')
    return json.loads(result.stdout) if result.stdout.strip() else {}


def resume(instance, cluster='boutique-eq4-eks', call=aws, sleep=time.sleep):
    if not re.fullmatch(r'i-[0-9a-f]{8,17}', instance):
        raise SafeError('Configure the existing Vault instance ID before deployment.')
    description = call('eks', 'describe-cluster', '--name', cluster)['cluster']
    if description['status'] != 'ACTIVE':
        raise SafeError('The existing EKS cluster must be ACTIVE; initial infrastructure setup is separate.')
    node_ids = set()
    for name in (cluster + '-nodes', cluster + '-storage'):
        group = call('eks', 'describe-nodegroup', '--cluster-name', cluster,
                     '--nodegroup-name', name)['nodegroup']
        asgs = [entry['name'] for entry in group['resources']['autoScalingGroups']]
        if not asgs:
            raise SafeError('An expected EKS node group has no backing Auto Scaling group.')
        backing = call('autoscaling', 'describe-auto-scaling-groups', '--auto-scaling-group-names', *asgs)
        node_ids.update(i['InstanceId'] for g in backing['AutoScalingGroups'] for i in g['Instances'])
    ids = sorted(node_ids | {instance})
    response = call('ec2', 'describe-instances', '--instance-ids', *ids)
    machines = [i for r in response['Reservations'] for i in r['Instances']]
    if {i['InstanceId'] for i in machines} != set(ids):
        raise SafeError('Could not identify every expected existing instance; no start request was sent.')
    vault = next(i for i in machines if i['InstanceId'] == instance)
    if ({t['Key']: t['Value'] for t in vault.get('Tags', [])}.get('Name') != 'boutique-vault-lab'
            or vault['VpcId'] != description['resourcesVpcConfig']['vpcId']):
        raise SafeError('The selected Vault instance does not belong to the expected Lab deployment.')
    if any(i['State']['Name'] not in ('running', 'pending', 'stopped') for i in machines):
        raise SafeError('Instances are stopping or terminating; wait for the Lab to stabilize and retry.')
    stopped = sorted(i['InstanceId'] for i in machines if i['State']['Name'] == 'stopped')
    if stopped:
        call('ec2', 'start-instances', '--instance-ids', *stopped)
    call('ec2', 'wait', 'instance-running', '--instance-ids', *ids, timeout=660)
    for _ in range(60):
        info = call('ssm', 'describe-instance-information', '--filters', 'Key=InstanceIds,Values=' + instance)
        if any(i['InstanceId'] == instance and i['PingStatus'] == 'Online'
               for i in info['InstanceInformationList']):
            print('PASS: existing Lab instances running; Vault SSM agent Online.')
            return
        sleep(10)
    raise SafeError('Vault did not become Online in SSM; verify the active Lab and instance profile.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance-id', required=True)
    try:
        resume(parser.parse_args().instance_id)
    except SafeError as exc:
        sys.exit('Lab resume failed: ' + str(exc))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit('Lab resume failed; no raw AWS response or credentials were logged.')
