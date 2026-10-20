"""One-time, backed-up Shamir-to-KMS migration using the existing custodians."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request

from common import DATA_PATH, SafeError, Vault, private_json, validate_values
from recover_root import decrypt_pgp
from resume_lab import aws


def fingerprint(vault):
    values = validate_values(vault.request('GET', DATA_PATH)[1]['data']['data'])
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def write_journal(path, value):
    with path.open('w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())


def snapshot(vault, target):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    private_json(target, {})
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=vault.context))
    request = urllib.request.Request(vault.url + '/v1/sys/storage/raft/snapshot',
                                     headers={'X-Vault-Token': vault.token})
    try:
        with opener.open(request, timeout=60) as response, target.open('wb') as output:
            shutil.copyfileobj(response, output)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        raise SafeError('Snapshot download failed; migration was not started.') from None
    if target.stat().st_size < 32:
        raise SafeError('The snapshot is incomplete; migration was not started.')


def restart(instance, key):
    variables = {'vault_migration_instance': instance, 'vault_kms_key_arn': key}
    try:
        subprocess.run(['ansible-playbook', 'kms-migration.yml', '--limit', instance,
                        '--extra-vars', json.dumps(variables)], check=True, timeout=600,
                       cwd=Path(__file__).resolve().parents[2] / 'ansible')
    except (subprocess.SubprocessError, OSError):
        raise SafeError('KMS configuration did not complete; retain the seal configuration and use --resume.') from None


def wait_status(probe):
    for _ in range(60):
        try:
            return probe.request('GET', 'sys/seal-status')[1]
        except SafeError:
            time.sleep(1)
    raise SafeError('Vault did not expose its TLS listener; retain the KMS configuration and use --resume.')


def wait_ready(vault):
    for _ in range(60):
        try:
            vault.ready()
            return
        except SafeError:
            time.sleep(1)
    raise SafeError('Vault is not ready; preserve the KMS key, configuration and snapshot, then use --resume.')


def migrate(probe, operator, shares, state, journal, restart_server):
    # Resume only the recorded operation. Never restore snapshots or remove a seal automatically.
    status = probe.request('GET', 'sys/seal-status')[1]
    if status.get('type') != 'awskms' or status.get('migration'):
        if state['phase'] == 'prepared' and not status.get('migration'):
            restart_server()
            status = wait_status(probe)
        if not status.get('migration'):
            raise SafeError('Vault did not enter seal migration; preserve configuration and inspect KMS access.')
        for share in shares:
            result = probe.request('PUT', 'sys/unseal', {'key': share, 'migrate': True})[1]
            if not result.get('sealed', True):
                break
    wait_ready(probe)
    status = probe.request('GET', 'sys/seal-status')[1]
    if status.get('type') != 'awskms' or status.get('migration'):
        raise SafeError('Native KMS migration did not complete; do not remove its seal configuration.')
    if fingerprint(operator) != state['fingerprint']:
        raise SafeError('Application values could not be verified after migration; preserve the snapshot.')
    state['phase'] = 'migrated'
    write_journal(journal, state)
    # Prove auto-unseal with an actual second restart, without submitting any share.
    restart_server()
    wait_ready(probe)
    if probe.request('GET', 'sys/seal-status')[1].get('type') != 'awskms':
        raise SafeError('The restart did not retain the KMS seal.')
    if fingerprint(operator) != state['fingerprint']:
        raise SafeError('Application values changed during restart verification; preserve the snapshot.')
    state['phase'] = 'verified'
    write_journal(journal, state)
    print('PASS: native KMS migration completed; application values preserved.')
    print('PASS: restart auto-unsealed Vault without submitting recovery shares.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance-id', required=True)
    parser.add_argument('--key-arn', required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--operator-file', type=Path, default=Path.home() / 'vault-custody/operator.json')
    parser.add_argument('--init-file', type=Path, default=Path.home() / 'vault-custody/init.json')
    args = parser.parse_args()
    if not re.fullmatch(r'i-[0-9a-f]{8,17}', args.instance_id):
        raise SafeError('Supply the exact existing Vault instance ID.')
    if not re.fullmatch(r'arn:aws:kms:us-east-1:[0-9]{12}:key/[0-9a-f-]{36}', args.key_arn):
        raise SafeError('Supply the persistent KMS key ARN in us-east-1.')
    if os.name != 'posix' or not os.environ.get('ANSIBLE_SSM_BUCKET') or not shutil.which('ansible-playbook'):
        raise SafeError('Use the project WSL environment and set ANSIBLE_SSM_BUCKET.')
    if args.operator_file.stat().st_mode & 0o077:
        raise SafeError('The operator file must be private (0600).')
    key = aws('kms', 'describe-key', '--key-id', args.key_arn)['KeyMetadata']
    identity = aws('sts', 'get-caller-identity')
    if (key['Arn'] != args.key_arn or key['AWSAccountId'] != identity['Account']
            or key['KeyState'] != 'Enabled' or key['KeyUsage'] != 'ENCRYPT_DECRYPT'
            or key['KeySpec'] != 'SYMMETRIC_DEFAULT'):
        raise SafeError('The KMS key must be enabled, symmetric and owned by the active Lab account.')
    operator = Vault(json.loads(args.operator_file.read_text())['client_token'])
    probe = Vault()
    status = wait_status(probe)
    if not status.get('initialized'):
        raise SafeError('Vault must already be initialized; this helper never initializes or replaces data.')
    journal = args.snapshot.with_suffix('.migration.json')
    shares = []
    if args.resume:
        state = json.loads(journal.read_text())
        if (state['instance'] != args.instance_id or state['key'] != args.key_arn
                or state['phase'] not in ('prepared', 'migrated', 'verified')
                or args.snapshot.stat().st_size < 32):
            raise SafeError('Resume requires the original migration journal and complete snapshot.')
    else:
        if status.get('type') != 'shamir' or status.get('migration') or (status.get('t'), status.get('n')) != (2, 3):
            raise SafeError('Expected the existing two-of-three Shamir seal; use --resume for a recorded migration.')
    if status.get('type') != 'awskms' or status.get('migration'):
        material = json.loads(args.init_file.read_text())
        for index in (1, 2):
            print(f'Decrypt custodian {index + 1} locally; no share is printed.', flush=True)
            shares.append(decrypt_pgp(base64.b64decode(material['keys_base64'][index], validate=True),
                                      args.init_file.parent / 'gnupg'))
    if not args.resume:
        if status.get('sealed'):
            for share in shares:
                probe.request('PUT', 'sys/unseal', {'key': share})
        wait_ready(probe)
        policies = operator.request('GET', 'auth/token/lookup-self')[1]['data']['policies']
        if 'boutique-operator' not in policies or 'root' in policies:
            raise SafeError('Use the named operator; recover a new operator if its token expired.')
        policy = Path(__file__).resolve().parents[2] / 'infrastructure/vault/policies/boutique-operator.hcl'
        operator.request('PUT', 'sys/policies/acl/boutique-operator', {'policy': policy.read_text()})
        state = {'phase': 'preparing', 'key': args.key_arn, 'instance': args.instance_id,
                 'fingerprint': fingerprint(operator)}
        private_json(journal, state)
        snapshot(operator, args.snapshot)
        state['phase'] = 'prepared'
        write_journal(journal, state)
        print('PASS: private pre-migration Raft snapshot saved: ' + str(args.snapshot))
    migrate(probe, operator, shares, state, journal, lambda: restart(args.instance_id, args.key_arn))


if __name__ == '__main__':
    try:
        main()
    except SafeError as exc:
        sys.exit('KMS migration stopped: ' + str(exc))
    except KeyboardInterrupt:
        sys.exit('KMS migration interrupted; preserve the snapshot and configuration, then use --resume.')
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        sys.exit('KMS migration stopped; retain its snapshot and configuration. No private values were logged.')
