"""One-time fresh KMS Vault initialization, executed only on its EC2 through SSM."""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time

from bootstrap import configure_base
from common import DATA_PATH, SafeError, Vault, private_json, validate_values
from github_oidc import configure
from migrate_kms import snapshot
from require_automation import runtime


def gpg(args, data=None):
    result = subprocess.run(['gpg', '--batch', '--yes', *args], input=data,
                            capture_output=True, timeout=90, check=False)
    if result.returncode:
        raise SafeError('Public-key custody encryption failed; no private command output logged.')
    return result.stdout


def await_ready(vault):
    for _ in range(30):
        try:
            vault.ready()
            return
        except SafeError:
            time.sleep(2)
    raise SafeError('Vault did not become ready using native KMS and verified TLS.')


def initialize(payload, custody):
    values = validate_values(payload['values'])
    keys = payload['pgp_public_keys']
    if len(keys) != 3 or len(set(keys)) != 3:
        raise SafeError('Three distinct recovery public keys are required.')
    anonymous = Vault()
    if anonymous.request('GET', 'sys/init')[1].get('initialized'):
        raise SafeError('Vault is already initialized; preserve its custody and data before proceeding.')
    seal = anonymous.request('GET', 'sys/seal-status')[1]
    if seal.get('type') != 'awskms' or seal.get('migration'):
        raise SafeError('Initialize only a fresh Vault configured for native AWS KMS.')
    custody = Path(custody)
    custody.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Reserve durable encrypted custody before sending the irreversible init request.
    init_path = custody / 'init.json'
    private_json(init_path, {'phase': 'reserved'})
    with tempfile.TemporaryDirectory(prefix='boutique-pgp-', dir='/run') as directory:
        home = Path(directory)
        recipients = []
        for index, encoded in enumerate(keys):
            raw = base64.b64decode(encoded, validate=True)
            inspected = gpg(['--homedir', directory, '--with-colons', '--import-options', 'show-only', '--import'], raw).decode()
            fingerprints = [line.split(':')[9] for line in inspected.splitlines() if line.startswith('fpr:')]
            if not fingerprints:
                raise SafeError('A recovery public key could not be validated.')
            gpg(['--homedir', directory, '--import'], raw)
            if index in (1, 2):
                recipients += ['--recipient', fingerprints[0]]
        encrypt = ['--homedir', directory, '--trust-model', 'always', *recipients, '--encrypt']
        gpg(encrypt, b'public-key custody self-test')
        _, material = anonymous.request('PUT', 'sys/init', {
            'recovery_shares': 3, 'recovery_threshold': 2, 'recovery_pgp_keys': keys})
        root_token = material.pop('root_token')
        init_path.write_text(json.dumps(material))
        # Only the EC2 process sees the initial root token. Never return it to Actions.
        material['root_token'] = base64.b64encode(gpg(encrypt, root_token.encode())).decode()
        material['root_token_pgp_encrypted'] = True
        init_path.write_text(json.dumps(material))
        root = Vault(root_token)
        await_ready(root)
        configure_base(root)
        root.request('POST', DATA_PATH, {'options': {'cas': 0}, 'data': values})
        configure(root, payload['repo'], payload['repository_id'], payload['owner_id'])
        result = subprocess.run(['systemctl', 'restart', 'vault'], capture_output=True, timeout=60, check=False)
        if result.returncode:
            raise SafeError('Vault restart failed; encrypted custody was retained on EC2.')
        await_ready(root)
        runtime(root)
        if root.request('GET', DATA_PATH)[1]['data']['data'] != values:
            raise SafeError('The local application values changed after the native KMS restart.')
        _, operator = root.request('POST', 'auth/token/create-orphan', {
            'policies': ['boutique-operator'], 'ttl': '15m', 'explicit_max_ttl': '15m', 'renewable': False})
        root.request('POST', 'auth/token/revoke-self')
        material['root_token_revoked'] = True
        init_path.write_text(json.dumps(material))
        backup_operator = Vault(operator['auth']['client_token'])
        snapshot(backup_operator, custody / 'raft.snap')
        backup_operator.request('POST', 'auth/token/revoke-self')
        buffer = __import__('io').BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
            archive.add(init_path, arcname='init.json')
            archive.add(custody / 'raft.snap', arcname='raft.snap')
        encrypted = gpg(encrypt, buffer.getvalue())
        with os.fdopen(os.open(custody / 'vault-custody.tar.gpg', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
            stream.write(encrypted)
        (custody / 'verified.json').write_text(json.dumps({
            'kms_restart_verified': True, 'tls_verified': True, 'oidc_configured': True,
            'root_token_revoked': True, 'admin_source': 'accepted local environment'}))
    print('PASS: native KMS restart, TLS, local admin/settings, restricted OIDC and root-token revocation verified.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', required=True)
    parser.add_argument('--custody', default='/opt/vault/custody')
    try:
        args = parser.parse_args()
        os.umask(0o077)
        initialize(json.loads(Path(args.payload).read_text()), args.custody)
    except SafeError as error:
        sys.exit('Fresh Vault initialization stopped: ' + str(error))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit('Fresh Vault initialization stopped; no private values were logged. Preserve server custody.')
