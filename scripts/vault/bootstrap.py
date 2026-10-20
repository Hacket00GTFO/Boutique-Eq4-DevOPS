"""Explicit operator ceremony; never called by installation or deployment CI."""
import argparse
import base64
import getpass
from pathlib import Path
import secrets
import sys
from urllib.parse import quote
from common import DATA_PATH, SafeError, Vault, private_json, validate_values
from local_env import integration_values

POLICIES = Path(__file__).resolve().parents[2] / 'infrastructure/vault/policies'


def seed_values():
    app, postgres = secrets.token_urlsafe(36), secrets.token_urlsafe(36)
    return validate_values({
        'DATABASE_URL': f'postgresql://boutique_app:{quote(app, safe="")}@postgres:5432/boutique',
        'MIGRATION_DATABASE_URL': f'postgresql://postgres:{quote(postgres, safe="")}@postgres:5432/boutique',
        'APP_DB_PASSWORD': app, 'POSTGRES_PASSWORD': postgres,
        'ADMIN_EMAIL': 'admin@boutique.com', 'ADMIN_PASSWORD': secrets.token_urlsafe(24),
        **{k: secrets.token_urlsafe(36) for k in ('ADMIN_API_TOKEN', 'CATALOG_READ_TOKEN', 'CHECKOUT_API_TOKEN')},
    })


def configure_base(vault):
    mounts = vault.request('GET', 'sys/mounts')[1]['data']
    if 'secret/' not in mounts:
        vault.request('POST', 'sys/mounts/secret', {'type': 'kv', 'options': {'version': '2'}})
    elif mounts['secret/']['type'] != 'kv' or mounts['secret/'].get('options', {}).get('version') != '2':
        raise SafeError('Existing secret mount is not KV v2; refusing an implicit migration.')
    audits = vault.request('GET', 'sys/audit')[1]['data']
    if 'file/' not in audits:
        vault.request('PUT', 'sys/audit/file', {'type': 'file', 'options': {
            'file_path': '/var/log/vault/audit.json', 'log_raw': 'false'}})
    elif audits['file/'].get('options', {}).get('log_raw', 'false') != 'false':
        raise SafeError('Existing audit device logs raw secrets; disable raw logging before proceeding.')
    for name in ('boutique-ci', 'boutique-operator'):
        vault.request('PUT', 'sys/policies/acl/' + name, {'policy': (POLICIES / (name + '.hcl')).read_text()})
    vault.request('POST', 'auth/token/roles/boutique-ci', {
        'allowed_policies': ['boutique-ci'], 'orphan': True, 'renewable': False,
        'token_explicit_max_ttl': 14400, 'token_no_default_policy': True,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='Initialize once, encrypting all shares and root token with PGP')
    init.add_argument('--key-pgp', action='append', required=True, help='Three distinct binary public PGP key files')
    init.add_argument('--root-pgp', required=True)
    init.add_argument('--output', required=True)
    sub.add_parser('unseal', help='Supply one share via hidden prompt; two custodians run this separately')
    sub.add_parser('configure', help='Enable KV v2, hashed audit logging and access policies')
    seed = sub.add_parser('seed', help='Seed new lab data without overwriting existing database credentials')
    seed.add_argument('--rotate-api-tokens', action='store_true')
    seed.add_argument('--env-file', help='Import the local application/admin settings into a NEW Vault secret')
    issue = sub.add_parser('issue-ci', help='Create an orphan, non-renewable, read-only four-hour token')
    issue.add_argument('--output', required=True)
    operator = sub.add_parser('issue-operator')
    operator.add_argument('--output', required=True)
    sub.add_parser('revoke-self', help='Explicitly revoke the currently supplied root/operator token')
    args = parser.parse_args()
    vault = Vault()
    if args.command == 'init':
        if len(args.key_pgp) != 3 or len(set(args.key_pgp)) != 3:
            raise SafeError('Supply three distinct PGP public keys.')
        public_keys = [Path(p).read_bytes() for p in args.key_pgp]
        if len(set(public_keys)) != 3:
            raise SafeError('The three PGP public keys must contain different keys.')
        if vault.request('GET', 'sys/init')[1].get('initialized'):
            raise SafeError('Vault is already initialized; refusing to reinitialize.')
        # Reserve the destination BEFORE changing Vault, to avoid losing returned custody material.
        private_json(args.output, {})
        payload = {'secret_shares': 3, 'secret_threshold': 2,
                   'pgp_keys': [base64.b64encode(p).decode() for p in public_keys],
                   'root_token_pgp_key': base64.b64encode(Path(args.root_pgp).read_bytes()).decode()}
        _, result = vault.request('PUT', 'sys/init', payload)
        with Path(args.output).expanduser().open('w') as stream:
            __import__('json').dump(result, stream)
        print('Initialized. Encrypted shares and root token saved to the private custody file.')
        return
    if args.command == 'unseal':
        _, result = vault.request('PUT', 'sys/unseal', {'key': getpass.getpass('Unseal share: ')})
        print('Vault unsealed.' if not result.get('sealed', True) else 'Share accepted; another custodian is required.')
        return
    vault.authenticate().ready()
    if args.command == 'configure':
        configure_base(vault)
        print('KV v2, audit and policies configured; no application values were printed.')
    elif args.command == 'seed':
        if args.env_file and args.rotate_api_tokens:
            raise SafeError('Local environment import cannot rotate an existing secret.')
        imported = integration_values(args.env_file) if args.env_file else None
        code, result = vault.request('GET', DATA_PATH, allowed=(200, 404))
        if code == 200:
            values = validate_values(result['data']['data'])
            if not args.rotate_api_tokens:
                raise SafeError('Secret already exists; seed never replaces existing credentials.')
            values.update({k: secrets.token_urlsafe(36) for k in ('ADMIN_API_TOKEN', 'CATALOG_READ_TOKEN', 'CHECKOUT_API_TOKEN')})
            version = result['data']['metadata']['version']
        else:
            if args.rotate_api_tokens:
                raise SafeError('Cannot rotate a missing integration secret.')
            values, version = imported or seed_values(), 0
        vault.request('POST', DATA_PATH, {'options': {'cas': version}, 'data': values})
        print('Integration secret saved using check-and-set; database credentials were preserved on rotation.')
    elif args.command in ('issue-ci', 'issue-operator'):
        private_json(args.output, {})
        ci = args.command == 'issue-ci'
        _, result = vault.request('POST', 'auth/token/create/boutique-ci' if ci else 'auth/token/create-orphan', {
            'policies': ['boutique-ci' if ci else 'boutique-operator'], 'no_default_policy': ci,
            'ttl': '4h' if ci else '24h', 'explicit_max_ttl': '4h' if ci else '24h',
            'renewable': False, 'display_name': 'boutique-ci' if ci else 'boutique-operator',
        })
        with Path(args.output).expanduser().open('w') as stream:
            __import__('json').dump(result['auth'], stream)
        print('Token saved to private custody file; never copy its contents into a log.')
    else:
        vault.request('POST', 'auth/token/revoke-self')
        print('Supplied token revoked.')


if __name__ == '__main__':
    try:
        main()
    except SafeError as exc:
        print('Operator operation failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError):
        print('Operator operation failed. Check TLS, permissions, input files and Vault state; no sensitive details logged.', file=sys.stderr)
        sys.exit(1)
