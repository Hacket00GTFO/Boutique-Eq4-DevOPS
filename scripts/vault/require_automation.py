"""Mandatory recovery, native KMS and verified TLS gates for the deployment."""
import argparse
import os
import re
import subprocess
import sys

from common import SafeError, Vault
from resume_lab import aws

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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['configuration', 'cloud', 'runtime'])
    try:
        phase = parser.parse_args().phase
        {'configuration': configuration, 'cloud': cloud, 'runtime': runtime}[phase]()
    except SafeError as exc:
        sys.exit('Deployment prerequisite failed: ' + str(exc))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit('Deployment prerequisite failed; private values and raw responses were not logged.')
