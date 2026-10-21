"""Recover an encrypted root token with a quorum; close the temporary access window."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from common import SafeError, Vault, private_json


def decrypt_pgp(ciphertext, home):
    """Only ciphertext touches disk. GPG prompts on the operator's terminal."""
    with tempfile.NamedTemporaryFile(dir=home.parent, suffix='.gpg') as encrypted:
        encrypted.write(ciphertext)
        encrypted.flush()
        try:
            result = subprocess.run(
                ['gpg', '--homedir', str(home), '--pinentry-mode', 'loopback',
                 '--decrypt', encrypted.name], stdout=subprocess.PIPE, check=True)
        except subprocess.CalledProcessError:
            raise SafeError('GPG could not decrypt; no plaintext was printed.') from None
    value = result.stdout.decode('ascii').strip()
    if not value or any(c in value for c in '\r\n\0'):
        raise SafeError('GPG returned an invalid single-line value.')
    return value


def check_recipient(recipient, home):
    marker = b'boutique-root-recovery-check'
    try:
        result = subprocess.run(
            ['gpg', '--homedir', str(home), '--batch', '--yes', '--trust-model',
             'always', '--recipient-file', str(recipient), '--encrypt'],
            input=marker, stdout=subprocess.PIPE, check=True)
    except subprocess.CalledProcessError:
        raise SafeError('GPG could not encrypt the local recipient check.') from None
    if decrypt_pgp(result.stdout, home) != marker.decode():
        raise SafeError('The recovery recipient failed the local round trip.')


def toggle_access(instance, enabled):
    variables = {'vault_recovery_instance': instance,
                 'vault_recovery_enabled': enabled}
    try:
        subprocess.run(
            ['ansible-playbook', 'root-recovery.yml', '--limit', instance,
             '--extra-vars', json.dumps(variables)],
            cwd=Path(__file__).resolve().parents[2] / 'ansible',
            check=True, timeout=300)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise SafeError('Ansible could not apply the recovery access setting.') from None


def wait_access(vault, enabled):
    expected = 200 if enabled else 403
    for _ in range(30):
        code, _ = vault.request('GET', 'sys/generate-root/attempt', allowed=(200, 403))
        if code == expected:
            return
        time.sleep(0.2)
    raise SafeError('Vault did not apply the expected recovery access setting.')


@contextmanager
def recovery_window(vault, toggle):
    code, _ = vault.request('GET', 'sys/generate-root/attempt', allowed=(200, 403))
    if code != 403:
        raise SafeError('Root recovery is already exposed; run --close-only first.')
    try:
        toggle(True)
        wait_access(vault, True)
        yield
    finally:
        try:
            toggle(False)
            wait_access(vault, False)
        except (SafeError, OSError):
            raise SafeError('Could not verify closure. Run this command with --close-only immediately.') from None
        print('PASS: unauthenticated root recovery is closed.')


def save_result(output, document):
    # The destination was exclusively reserved with mode 0600 before generation.
    with output.open('w') as stream:
        json.dump(document, stream)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def recover(vault, init_file, indices, recipient, output, home, toggle):
    vault.ready()
    _, seal = vault.request('GET', 'sys/seal-status')
    material = json.loads(init_file.read_text())
    if (seal.get('t'), seal.get('n')) != (2, 3):
        raise SafeError('This lab helper requires the expected two-of-three quorum.')
    if len(indices) != 2 or len(set(indices)) != 2 or any(i not in (1, 2, 3) for i in indices):
        raise SafeError('Select two different share numbers from 1, 2 and 3.')
    if len(material.get('keys_base64', [])) != 3:
        raise SafeError('The custody file does not contain three encrypted shares.')

    shares = []
    for index in indices:
        print(f'Decrypting share {index}; use its custodian passphrase if prompted.', flush=True)
        shares.append(decrypt_pgp(base64.b64decode(
            material['keys_base64'][index - 1], validate=True), home))
    print('Checking that the selected recipient can recover the new token.', flush=True)
    check_recipient(recipient, home)
    public_key = recipient.read_bytes()
    private_json(output, {'status': 'pending'})

    with recovery_window(vault, toggle):
        _, attempt = vault.request('GET', 'sys/generate-root/attempt')
        if attempt.get('started'):
            raise SafeError('Another root generation attempt exists; refusing to replace it.')
        _, attempt = vault.request('POST', 'sys/generate-root/attempt',
                                   {'pgp_key': base64.b64encode(public_key).decode()})
        nonce = attempt['nonce']
        complete = False
        try:
            result = {}
            for share in shares:
                _, result = vault.request('POST', 'sys/generate-root/update',
                                          {'key': share, 'nonce': nonce})
                complete = result.get('complete', False)
                if complete:
                    break
            if not complete or not result.get('encoded_token'):
                raise SafeError('Vault did not complete root generation with this quorum.')
            document = {
                'status': 'generated', 'root_token': result['encoded_token'],
                'pgp_fingerprint': attempt.get('pgp_fingerprint', ''),
                'created_at': datetime.now(timezone.utc).isoformat(),
            }
            save_result(output, document)
            print(f'Encrypted root token saved: {output}')
            token = decrypt_pgp(base64.b64decode(document['root_token'], validate=True), home)
            _, lookup = Vault(token=token).request('GET', 'auth/token/lookup-self')
            if 'root' not in lookup.get('data', {}).get('policies', []):
                raise SafeError('The generated token did not validate as root.')
            document['status'] = 'verified'
            save_result(output, document)
        finally:
            if not complete:
                # Cancel only the attempt created by this process, never a different nonce.
                try:
                    _, current = vault.request('GET', 'sys/generate-root/attempt')
                    if current.get('started') and current.get('nonce') == nonce:
                        vault.request('DELETE', 'sys/generate-root/attempt')
                except SafeError:
                    print('WARN: could not confirm cancellation of this recovery attempt.', file=sys.stderr)
    print('PASS: encrypted root token recovered and authorized; TLS verified.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance-id', required=True)
    parser.add_argument('--close-only', action='store_true')
    parser.add_argument('--init-file', type=Path, default=Path.home() / 'vault-custody/init.json')
    parser.add_argument('--shares', type=int, nargs='+', default=[2, 3])
    parser.add_argument('--recipient-pgp', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--gpg-home', type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r'i-[0-9a-f]{8,17}', args.instance_id):
        raise SafeError('Supply one EC2 instance ID, not an inventory pattern.')
    if not os.environ.get('ANSIBLE_SSM_BUCKET') or not shutil.which('ansible-playbook'):
        raise SafeError('Activate the project environment and set ANSIBLE_SSM_BUCKET.')
    vault = Vault()
    toggle = lambda enabled: toggle_access(args.instance_id, enabled)
    if args.close_only:
        toggle(False)
        wait_access(vault, False)
        print('PASS: unauthenticated root recovery is closed.')
        return
    if not args.recipient_pgp or not args.output or not shutil.which('gpg'):
        raise SafeError('Supply --recipient-pgp and --output; GPG must be installed.')
    home = args.gpg_home or args.init_file.parent / 'gnupg'
    recover(vault, args.init_file, args.shares, args.recipient_pgp,
            args.output, home, toggle)


def interrupted(signum, frame):
    raise KeyboardInterrupt


if __name__ == '__main__':
    for signum in (signal.SIGTERM, signal.SIGHUP) if hasattr(signal, 'SIGHUP') else (signal.SIGTERM,):
        signal.signal(signum, interrupted)
    try:
        main()
    except SafeError as exc:
        print('Recovery failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('Recovery interrupted. If closure was not confirmed, run --close-only.', file=sys.stderr)
        sys.exit(130)
    except (OSError, ValueError, KeyError):
        print('Recovery failed. Check custody files, TLS and tools; no private values were logged.', file=sys.stderr)
        sys.exit(1)
