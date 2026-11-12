"""One-time Vault trust for the main GitHub deployment; no stored CI token."""
import argparse
import json
import os
from pathlib import Path
import re
import sys

from common import SafeError, Vault

ISSUER = 'https://token.actions.githubusercontent.com'
ROLE = 'boutique-main'
MOUNT = 'github'


def role_configuration(repo, repository_id, owner_id):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise SafeError('Supply a repository as owner/name.')
    if not all(re.fullmatch(r'[1-9][0-9]*', str(i)) for i in (repository_id, owner_id)):
        raise SafeError('Supply the numeric repository and owner IDs from GitHub.')
    owner, name = repo.split('/')
    return {
        'role_type': 'jwt', 'user_claim': 'repository_id',
        'bound_audiences': ['https://github.com/' + repo],
        'bound_subject': f'repo:{owner}@{owner_id}/{name}@{repository_id}:environment:vault-lab',
        'bound_claims_type': 'string',
        'bound_claims': {
            'repository_id': str(repository_id), 'repository_owner_id': str(owner_id),
            'ref': 'refs/heads/main',
            'workflow_ref': [repo + '/.github/workflows/' + name + '@refs/heads/main'
                             for name in ('ci-docker-ecr.yaml', 'cd-helm-eks.yaml')],
        },
        'token_policies': ['boutique-ci'], 'token_no_default_policy': True,
        'token_type': 'batch', 'token_ttl': 900, 'token_max_ttl': 900,
        'token_explicit_max_ttl': 900,
    }


def validate_existing_role(existing, expected, repo):
    if not existing:
        return
    legacy = 'repo:' + repo + ':environment:vault-lab'
    if existing.get('bound_subject') not in (expected['bound_subject'], legacy):
        raise SafeError('The deployment role belongs to another subject; refusing to replace it.')
    claims = existing.get('bound_claims') or {}
    for name in ('repository_id', 'repository_owner_id'):
        if claims.get(name) != expected['bound_claims'][name]:
            raise SafeError('The existing role has a different immutable repository/owner identity; no trust changed.')
    if set(claims) - set(expected['bound_claims']):
        raise SafeError('The existing role has additional claim restrictions; preserve them before changing trust.')


def configure(vault, repo, repository_id, owner_id):
    role = role_configuration(repo, repository_id, owner_id)
    vault.ready()
    mounts = vault.request('GET', 'sys/auth')[1]['data']
    if MOUNT + '/' in mounts:
        if mounts[MOUNT + '/']['type'] != 'jwt':
            raise SafeError('The github auth mount already has another type; preserve it manually.')
        old = vault.request('GET', 'auth/github/config')[1].get('data', {})
        if (old.get('bound_issuer') not in (None, '', ISSUER)
                or old.get('oidc_discovery_url') not in (None, '', ISSUER)
                or any(old.get(name) for name in ('jwks_url', 'jwks_pairs', 'jwt_validation_pubkeys',
                                                 'oidc_discovery_ca_pem'))):
            raise SafeError('The github auth mount has another trust configuration; refusing to replace it.')
        _, existing = vault.request('GET', 'auth/github/role/' + ROLE, allowed=(200, 404))
        validate_existing_role(existing.get('data', {}), role, repo)
    else:
        vault.request('POST', 'sys/auth/' + MOUNT, {'type': 'jwt', 'description': 'GitHub main deployment identity'})
    vault.request('POST', 'auth/github/config', {
        'oidc_discovery_url': ISSUER, 'bound_issuer': ISSUER, 'jwt_supported_algs': ['RS256'],
    })
    vault.request('POST', 'auth/github/role/' + ROLE, role)
    installed = vault.request('GET', 'auth/github/role/' + ROLE)[1]['data']
    for name in ('bound_claims', 'bound_subject', 'bound_audiences', 'token_policies',
                 'token_no_default_policy', 'token_type', 'token_explicit_max_ttl'):
        if installed.get(name) != role[name]:
            raise SafeError('Vault did not retain the required GitHub trust or token restrictions.')
    print('PASS: immutable GitHub repository/owner subject configured; main-only CI tokens last at most 15 minutes.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--repository-id', required=True)
    parser.add_argument('--owner-id', required=True)
    parser.add_argument('--operator-file', type=Path, default=Path.home() / 'vault-custody/operator.json')
    try:
        args = parser.parse_args()
        if os.name != 'posix' or args.operator_file.stat().st_mode & 0o077:
            raise SafeError('Use the private operator custody file on Linux/WSL.')
        token = json.loads(args.operator_file.read_text())['client_token']
        configure(Vault(token), args.repo, args.repository_id, args.owner_id)
    except SafeError as exc:
        sys.exit('GitHub trust configuration failed: ' + str(exc))
    except (OSError, ValueError, KeyError):
        sys.exit('GitHub trust configuration failed; private values were not logged.')
