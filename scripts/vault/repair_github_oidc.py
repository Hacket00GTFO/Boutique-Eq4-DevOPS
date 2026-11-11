"""Use a recovered root only to issue a temporary operator; revoke both tokens."""
import argparse
import base64
import json
import os
from pathlib import Path
import sys

from common import SafeError, Vault
from github_oidc import configure
from recover_root import decrypt_pgp


def repair(root, repo, repository_id, owner_id, client=Vault):
    operator = None
    try:
        try:
            root.ready()
            lookup = root.request('GET', 'auth/token/lookup-self')[1]['data']
            if 'root' not in lookup.get('policies', []):
                raise SafeError('The recovered session is not a root token.')
            auth = root.request('POST', 'auth/token/create-orphan', {
                'policies': ['boutique-operator'], 'no_default_policy': True,
                'ttl': '15m', 'explicit_max_ttl': '15m', 'renewable': False,
                'display_name': 'boutique-oidc-repair',
            })[1]['auth']
            operator = client(auth['client_token'])
            lookup = operator.request('GET', 'auth/token/lookup-self')[1]['data']
            if (lookup.get('policies') != ['boutique-operator']
                    or lookup.get('renewable') is not False
                    or not 0 < lookup.get('ttl', 0) <= 900
                    or not 0 < lookup.get('explicit_max_ttl', 0) <= 900):
                raise SafeError('The temporary operator did not retain its policy and fifteen-minute limit.')
        finally:
            # No root session remains while the actual role configuration runs.
            root.request('POST', 'auth/token/revoke-self')
        configure(operator, repo, repository_id, owner_id)
    finally:
        if operator is not None:
            operator.request('POST', 'auth/token/revoke-self')
    print('PASS: OIDC role repaired; temporary root and operator tokens revoked.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root-file', type=Path, required=True)
    parser.add_argument('--gpg-home', type=Path, required=True)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--repository-id', required=True)
    parser.add_argument('--owner-id', required=True)
    args = parser.parse_args()
    if (os.name != 'posix' or args.root_file.is_symlink()
            or args.root_file.stat().st_uid != os.getuid()
            or args.root_file.stat().st_mode & 0o077):
        raise SafeError('Use the private encrypted root custody file on Linux/WSL.')
    document = json.loads(args.root_file.read_text())
    if document.get('status') != 'verified':
        raise SafeError('The root recovery ceremony must verify and close its access window first.')
    token = decrypt_pgp(base64.b64decode(document['root_token'], validate=True), args.gpg_home)
    repair(Vault(token), args.repo, args.repository_id, args.owner_id)


if __name__ == '__main__':
    try:
        main()
    except SafeError as exc:
        sys.exit('OIDC repair stopped: ' + str(exc))
    except (OSError, ValueError, KeyError):
        sys.exit('OIDC repair stopped; no private values were logged. Check token revocation before retrying.')
