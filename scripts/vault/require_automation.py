"""Mandatory recovery, native KMS and verified TLS gates for the deployment."""
import argparse
import json
import os
import re
import subprocess
import sys
import time

from common import SafeError, Vault, VaultUnavailable
from resume_lab import aws
from tunnel_state import require_process, wait_tunnel

ACCOUNT = '716142453772'


def configuration(values=os.environ):
    if values.get('DATA_RECOVERY_VERIFIED') != 'true':
        raise SafeError('Application data recovery is not verified. Preserve backups and complete recovery before deploying.')
    if values.get('VAULT_AUTOMATION_READY') != 'true':
        raise SafeError('Complete the one-time native KMS, GitHub OIDC and TLS setup before deploying.')
    if not re.fullmatch(r'i-[0-9a-f]{8,17}', values.get('VAULT_INSTANCE_ID', '')):
        raise SafeError('Configure the recovered Vault instance ID.')
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', values.get('VAULT_SSM_BUCKET', '')):
        raise SafeError('Configure the dedicated Ansible SSM bucket.')
    key = values.get('VAULT_KMS_KEY_ARN', '')
    if not re.fullmatch(r'arn:aws:kms:us-east-1:' + ACCOUNT + r':key/[0-9a-f-]{36}', key):
        raise SafeError('Configure the persistent native Vault KMS key in the target account and region.')
    return key


def cloud(values=os.environ, call=aws):
    key = configuration(values)
    if call('sts', 'get-caller-identity')['Account'] != ACCOUNT:
        raise SafeError('The active AWS credentials must belong to account ' + ACCOUNT + '.')
    cluster = call('eks', 'describe-cluster', '--name', 'boutique-eq4-eks')['cluster']
    if cluster['status'] != 'ACTIVE':
        raise SafeError('Restore the EKS cluster and wait for ACTIVE before deploying.')
    metadata = call('kms', 'describe-key', '--key-id', key)['KeyMetadata']
    if (metadata['Arn'] != key or metadata['KeyState'] != 'Enabled'
            or metadata['KeyUsage'] != 'ENCRYPT_DECRYPT' or metadata['KeySpec'] != 'SYMMETRIC_DEFAULT'):
        raise SafeError('The native Vault KMS key must be enabled and usable for symmetric encryption.')
    print('PASS: recovery configuration, AWS account, EKS and native KMS key verified.')


def runtime(vault=None):
    client = vault or Vault()
    client.ready()
    status = client.request('GET', 'sys/seal-status')[1]
    if status.get('type') != 'awskms' or status.get('migration'):
        raise SafeError('Vault must use completed native AWS KMS auto-unseal; Shamir or pending migration cannot deploy.')
    print('PASS: Vault ready through verified TLS with the native AWS KMS seal.')


def await_runtime(vault=None, timeout=120, watchdog=lambda: None, clock=time.monotonic, sleep=time.sleep):
    client = vault or Vault()
    deadline = clock() + timeout
    while True:
        watchdog()
        try:
            runtime(client)
            watchdog()
            return
        except VaultUnavailable:
            if clock() >= deadline:
                raise SafeError('Vault readiness timed out after SSM opened; check native KMS auto-unseal.') from None
            sleep(min(2, max(0, deadline - clock())))


def read_nodes():
    result = subprocess.run(['kubectl', 'get', 'nodes', '-o', 'json'], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise SafeError('Cannot read EKS nodes; verify the active credentials and cluster access.')
    return json.loads(result.stdout)


def cluster(read=read_nodes, call=aws, sleep=time.sleep, attempts=120):
    groups = {'boutique-eq4-eks-nodes', 'boutique-eq4-eks-storage'}
    previous = None
    for attempt in range(attempts):
        nodes = [n for n in read().get('items', [])
                 if n.get('metadata', {}).get('labels', {}).get('eks.amazonaws.com/nodegroup') in groups]
        ready = [n for n in nodes if any(c.get('type') == 'Ready' and c.get('status') == 'True'
                                        for c in n.get('status', {}).get('conditions', []))]
        storage = any(n['metadata']['labels'].get('boutique.dev/node-purpose') == 'storage-controller' for n in ready)
        progress = (len(nodes), len(ready), storage)
        if progress != previous:
            print(f'EKS: registered={len(nodes)}, Ready={len(ready)}, storage Ready={storage}.', flush=True)
            previous = progress
        if len(nodes) >= 3 and len(ready) == len(nodes) and storage:
            break
        if attempt == attempts - 1:
            raise SafeError('The three expected EKS workers and storage node did not become Ready.')
        sleep(5)
    for attempt in range(attempts):
        addon = call('eks', 'describe-addon', '--cluster-name', 'boutique-eq4-eks',
                     '--addon-name', 'aws-ebs-csi-driver')['addon']
        if addon['status'] == 'ACTIVE':
            print('PASS: expected EKS nodes Ready; EBS CSI ACTIVE.')
            return
        if addon['status'] in ('CREATE_FAILED', 'DELETE_FAILED', 'DELETING'):
            raise SafeError('EBS CSI requires recovery; persistent data was retained.')
        if attempt != attempts - 1:
            sleep(5)
    raise SafeError('EBS CSI did not become ACTIVE after the workers recovered.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['configuration', 'cloud', 'cluster', 'runtime'])
    parser.add_argument('--tunnel-pid', type=int)
    parser.add_argument('--ready-file')
    try:
        args = parser.parse_args()
        if args.phase == 'runtime' and (args.tunnel_pid or args.ready_file):
            if not args.tunnel_pid or not args.ready_file:
                raise SafeError('Supply both the SSM supervisor PID and its readiness file.')
            wait_tunnel(args.ready_file, args.tunnel_pid)
            await_runtime(watchdog=lambda: require_process(args.tunnel_pid))
        else:
            {'configuration': configuration, 'cloud': cloud, 'cluster': cluster, 'runtime': runtime}[args.phase]()
    except SafeError as exc:
        sys.exit('Deployment prerequisite failed: ' + str(exc))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit('Deployment prerequisite failed; private values and raw responses were not logged.')
