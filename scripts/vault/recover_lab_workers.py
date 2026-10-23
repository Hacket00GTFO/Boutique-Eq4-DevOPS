"""Recover failed, unregistered Lab workers while retaining verified application storage."""
import os
from pathlib import Path
import re
import subprocess
import sys

from common import SafeError
from deploy import kubectl
from recovery_evidence import ACCOUNT, require_storage_recovery
from require_automation import read_nodes
from resume_lab import aws

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'aws'))
from inspect_worker import main as inspect_worker, PreparationError

CLUSTER = 'boutique-eq4-eks'


def owned_workers(call):
    if call('sts', 'get-caller-identity')['Account'] != ACCOUNT:
        raise SafeError('Worker recovery requires the expected Lab account.')
    cluster = call('eks', 'describe-cluster', '--name', CLUSTER)['cluster']
    if cluster['status'] != 'ACTIVE':
        raise SafeError('Recover workers only for the active existing cluster.')
    ids, storage = set(), set()
    for suffix, count in (('nodes', 2), ('storage', 1)):
        name = CLUSTER + '-' + suffix
        group = call('eks', 'describe-nodegroup', '--cluster-name', CLUSTER, '--nodegroup-name', name)['nodegroup']
        role = group['nodeRole']
        if (group['status'] != 'ACTIVE' or group['nodegroupName'] != name
                or group.get('clusterName') != CLUSTER
                or (suffix == 'storage' and role != f'arn:aws:iam::{ACCOUNT}:role/LabRole')
                or (suffix == 'nodes' and not re.fullmatch(f'arn:aws:iam::{ACCOUNT}:role/.*-LabEksNodeRole-.*', role))):
            raise SafeError('The expected node group or worker role changed; no reboot requested.')
        groups = group['resources']['autoScalingGroups']
        if len(groups) != 1:
            raise SafeError('The expected node group has ambiguous backing instances.')
        backing = call('autoscaling', 'describe-auto-scaling-groups', '--auto-scaling-group-names', groups[0]['name'])
        entries = backing.get('AutoScalingGroups', [])
        if len(entries) != 1 or entries[0]['AutoScalingGroupName'] != groups[0]['name']:
            raise SafeError('The worker Auto Scaling group did not match the expected group.')
        instances = entries[0]['Instances']
        found = {item['InstanceId'] for item in instances}
        if (len(instances) != count or len(found) != count or ids & found
                or any(item.get('LifecycleState') != 'InService' for item in instances)):
            raise SafeError('Recover only the two application workers and one storage worker already in service.')
        ids.update(found)
        if suffix == 'storage':
            storage = found
    response = call('ec2', 'describe-instances', '--instance-ids', *sorted(ids))
    machines = [item for reservation in response['Reservations'] for item in reservation['Instances']]
    if (len(machines) != 3 or {item['InstanceId'] for item in machines} != ids
            or any(item['State']['Name'] != 'running'
                   or item['VpcId'] != cluster['resourcesVpcConfig']['vpcId'] for item in machines)):
        raise SafeError('Worker instance identity, network or state changed; no reboot requested.')
    return ids, storage


def recover(values=os.environ, read=read_nodes, call=aws, query=kubectl, inspect=None):
    if (values.get('GITHUB_REF') != 'refs/heads/main'
            or values.get('GITHUB_EVENT_NAME') not in ('push', 'workflow_dispatch')
            or not re.fullmatch(r'i-[0-9a-f]{8,17}', values.get('VAULT_INSTANCE_ID', ''))
            or any(values.get(name) != 'true' for name in ('DATA_RECOVERY_VERIFIED', 'VAULT_AUTOMATION_READY'))):
        raise SafeError('Lab worker recovery requires an activated main deployment with verified data.')
    if read().get('items'):
        print('EKS workers are registered; automatic recovery will not reboot any worker.')
        return
    # This check uses only Kubernetes metadata, so the original DB can remain offline.
    # No Secret, PVC, volume, namespace or recovery record is written by this helper.
    require_storage_recovery(query)
    ids, storage = owned_workers(call)
    if values['VAULT_INSTANCE_ID'] in ids:
        raise SafeError('The Vault instance cannot be a worker recovery target.')
    report = (inspect or (lambda: inspect_worker(emit=False)))()
    if report.get('instance') not in storage:
        raise SafeError('The inspected storage worker changed; no reboot requested.')
    startup = report.get('startup', {})
    if startup.get('nodeadm-config', {}).get('state') != 'failed':
        print('Worker bootstrap is not in the known failed state; waiting for normal EKS registration.')
        return
    if not startup.get('current_ec2_permission', {}).get('describe_allowed'):
        raise SafeError('Worker bootstrap is failed and its current AWS permission is denied; start the Lab before retrying.')
    if any(startup.get(unit, {}).get('state') not in ('inactive', 'failed')
           for unit in ('nodeadm-run', 'kubelet', 'containerd')):
        raise SafeError('The inspected worker already has active services; automatic reboot stopped.')
    # Revalidate ownership, persistent storage and registration after the SSM probe.
    if owned_workers(call) != (ids, storage):
        raise SafeError('Worker membership changed during inspection; no reboot requested.')
    require_storage_recovery(query)
    if read().get('items'):
        print('A worker registered during inspection; no reboot requested.')
        return
    call('ec2', 'reboot-instances', '--instance-ids', *sorted(ids))
    print('PASS: one reboot requested for the three owned unregistered workers; verified PostgreSQL storage and Vault retained.')


if __name__ == '__main__':
    try:
        recover()
    except (SafeError, PreparationError) as error:
        sys.exit('Lab worker recovery stopped: ' + str(error))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit('Lab worker recovery stopped; private command output was suppressed.')
