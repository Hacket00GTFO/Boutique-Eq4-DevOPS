"""Prepare the persistent Vault KMS key before provisioning paid EKS resources."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ACCOUNT = '716142453772'
PREFIX = f'arn:aws:kms:us-east-1:{ACCOUNT}:key/'
ALIAS = 'alias/boutique-vault-lab'


class PreparationError(Exception):
    pass


def aws(*args):
    result = subprocess.run(['aws', *args, '--region', 'us-east-1', '--no-cli-pager'],
                            capture_output=True, text=True, timeout=90, check=False)
    if result.returncode:
        match = re.search(r'An error occurred \(([A-Za-z0-9_.-]+)\)', result.stderr)
        code = match.group(1) if match else 'request_failed'
        raise PreparationError(f'{args[0]}:{args[1]} failed ({code}). Private AWS responses were suppressed.')
    return json.loads(result.stdout) if result.stdout.strip() else {}


def prepare(existing=None, call=aws):
    identity = call('sts', 'get-caller-identity')
    if identity.get('Account') != ACCOUNT:
        raise PreparationError('Preparation requires the configured Learner Lab account.')
    # An API call to an actual Lab resource also checks for canceled credentials.
    call('eks', 'list-clusters')
    profile = call('iam', 'get-instance-profile', '--instance-profile-name', 'LabInstanceProfile')
    roles = profile.get('InstanceProfile', {}).get('Roles', [])
    if len(roles) != 1 or roles[0].get('Arn') != f'arn:aws:iam::{ACCOUNT}:role/LabRole':
        raise PreparationError('The existing Vault instance profile must contain only LabRole.')
    if existing:
        if not existing.startswith(PREFIX) or not re.fullmatch(r'[0-9a-f-]{36}', existing[len(PREFIX):]):
            raise PreparationError('The existing key must belong to the target account and region.')
        metadata = call('kms', 'describe-key', '--key-id', existing)['KeyMetadata']
    else:
        try:
            metadata = call('kms', 'describe-key', '--key-id', ALIAS)['KeyMetadata']
        except PreparationError as error:
            if '(NotFoundException)' not in str(error):
                raise
            policy = {'Version': '2012-10-17', 'Statement': [
                {'Sid': 'AccountKeyAdministration', 'Effect': 'Allow',
                 'Principal': {'AWS': f'arn:aws:iam::{ACCOUNT}:root'}, 'Action': 'kms:*', 'Resource': '*'},
                {'Sid': 'VaultInstanceProfileUse', 'Effect': 'Allow',
                 'Principal': {'AWS': roles[0]['Arn']},
                 'Action': ['kms:Encrypt', 'kms:Decrypt', 'kms:DescribeKey'], 'Resource': '*'},
            ]}
            metadata = call('kms', 'create-key', '--description', 'Boutique Learner Lab Vault auto-unseal',
                            '--key-usage', 'ENCRYPT_DECRYPT', '--key-spec', 'SYMMETRIC_DEFAULT',
                            '--policy', json.dumps(policy), '--tags', json.dumps([
                                {'TagKey': 'Project', 'TagValue': 'boutique-eq4'},
                                {'TagKey': 'Component', 'TagValue': 'vault'}]))['KeyMetadata']
            # Record the public ARN before any later request can fail; never delete the key.
            record(metadata['Arn'])
            call('kms', 'create-alias', '--alias-name', ALIAS, '--target-key-id', metadata['Arn'])
    if not (metadata.get('Arn', '').startswith(PREFIX) and metadata.get('KeyManager') == 'CUSTOMER'
            and metadata.get('KeyState') == 'Enabled' and metadata.get('KeyUsage') == 'ENCRYPT_DECRYPT'
            and metadata.get('KeySpec') == 'SYMMETRIC_DEFAULT'):
        raise PreparationError('Vault requires an enabled customer-managed symmetric encryption key.')
    # Never silently replace a key policy on an existing key.
    policy = json.loads(call('kms', 'get-key-policy', '--key-id', metadata['Arn'],
                             '--policy-name', 'default')['Policy'])
    statements = policy.get('Statement', [])
    if not any(item.get('Effect') == 'Allow' and item.get('Principal', {}).get('AWS') == roles[0]['Arn']
               and {'kms:Encrypt', 'kms:Decrypt', 'kms:DescribeKey'} <= set(item.get('Action', []))
               for item in statements):
        raise PreparationError('The KMS policy does not contain the expected Vault instance-profile grant.')
    call('kms', 'enable-key-rotation', '--key-id', metadata['Arn'])
    record(metadata['Arn'])
    print('PASS: persistent customer-managed KMS key and Vault profile policy prepared.')
    print('EKS and Vault infrastructure have not been created by this preparation step.')
    return metadata['Arn']


def record(arn):
    if not arn.startswith(PREFIX):
        raise PreparationError('KMS returned an unexpected account or region.')
    print('Persistent Vault KMS key: ' + arn, flush=True)
    if os.environ.get('GITHUB_OUTPUT'):
        with Path(os.environ['GITHUB_OUTPUT']).open('a') as stream:
            stream.write('key_arn=' + arn + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--existing-key-arn')
    try:
        prepare(parser.parse_args().existing_key_arn)
    except PreparationError as error:
        sys.exit('Vault KMS preparation stopped: ' + str(error))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit('Vault KMS preparation stopped; no private AWS response was logged.')
