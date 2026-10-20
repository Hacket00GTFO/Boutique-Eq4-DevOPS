"""One-time OIDC activation, or rotation of only the three repository AWS secrets."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from common import SafeError, Vault
from github_oidc import configure

AWS_KEYS = ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN')
ENVIRONMENT = 'vault-lab'


def command(args, value=None):
    result = subprocess.run(args, input=value, text=True, capture_output=True, check=False)
    if result.returncode:
        # CLI errors can contain request details. Never forward stdout/stderr or stdin.
        if args[0] == 'aws' and 'voc-cancel-cred' in result.stderr:
            raise SafeError('Start the Learner Lab and load its three new credentials before updating GitHub.')
        raise SafeError(f'{args[0]} failed; check authentication, permissions and connectivity.')
    return result.stdout


def api(repo, path='', method='GET', body=None):
    args = ['gh', 'api', '--method', method, f'repos/{repo}' + ('/' + path if path else '')]
    if body is not None:
        args += ['--input', '-']
    output = command(args, json.dumps(body) if body is not None else None)
    return json.loads(output) if output.strip() else {}


def set_value(repo, kind, name, value, environment=False):
    args = ['gh', kind, 'set', name, '--repo', repo]
    if environment:
        args += ['--env', ENVIRONMENT]
    command(args, value)


def prepare_environment(repo, current):
    # Preserve the simple lab configuration. Do not replace reviewer/timer/app rules.
    if any(rule.get('type') != 'branch_policy' for rule in current.get('protection_rules', [])):
        raise SafeError('Existing environment protection rules need to be preserved manually; no settings changed.')
    path = f'environments/{ENVIRONMENT}'
    policy = current.get('deployment_branch_policy') or {}
    rules = []
    if policy.get('custom_branch_policies'):
        rules = api(repo, path + '/deployment-branch-policies?per_page=100').get('branch_policies', [])
        if any(r.get('name') != 'main' or r.get('type', 'branch') != 'branch' for r in rules):
            raise SafeError('Additional environment branch rules exist; refusing to replace them.')
    api(repo, path, 'PUT', {'deployment_branch_policy': {
        'protected_branches': False, 'custom_branch_policies': True}})
    if not rules:
        # A previously unrestricted environment has no custom branch rules.
        rules = api(repo, path + '/deployment-branch-policies?per_page=100').get('branch_policies', [])
        if any(r.get('name') != 'main' or r.get('type', 'branch') != 'branch' for r in rules):
            raise SafeError('Unexpected environment branch rules; activation was not enabled.')
        if not rules:
            api(repo, path + '/deployment-branch-policies', 'POST', {'name': 'main', 'type': 'branch'})


def rotate_credentials(repo, credentials):
    for name in AWS_KEYS:
        set_value(repo, 'secret', name, credentials[name])
    # Remove stale environment overrides only after all repository values were uploaded.
    path = f'environments/{ENVIRONMENT}/secrets'
    existing = {entry['name'] for entry in api(repo, path + '?per_page=100').get('secrets', [])}
    for name in AWS_KEYS:
        if name in existing:
            api(repo, path + '/' + name, 'DELETE')


def publish(repo, instance, credentials, bucket, account, environment):
    prepare_environment(repo, environment)
    set_value(repo, 'variable', 'VAULT_AUTOMATION_READY', 'false', environment=True)
    rotate_credentials(repo, credentials)
    set_value(repo, 'variable', 'VAULT_INSTANCE_ID', instance, environment=True)
    set_value(repo, 'variable', 'VAULT_SSM_BUCKET', bucket, environment=True)
    set_value(repo, 'variable', 'AWS_ACCOUNT_ID', account)
    # The new workflow uses per-job OIDC, never this expired legacy token.
    path = f'environments/{ENVIRONMENT}/secrets'
    if any(s['name'] == 'VAULT_CI_TOKEN' for s in api(repo, path + '?per_page=100').get('secrets', [])):
        api(repo, path + '/VAULT_CI_TOKEN', 'DELETE')
    set_value(repo, 'variable', 'VAULT_AUTOMATION_READY', 'true', environment=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--instance-id')
    parser.add_argument('--expected-account')
    parser.add_argument('--ssm-bucket')
    parser.add_argument('--rotate-aws-only', action='store_true')
    parser.add_argument('--migration-journal', type=Path)
    parser.add_argument('--operator-file', type=Path, default=Path.home() / 'vault-custody/operator.json')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', args.repo):
        raise SafeError('Supply a repository as owner/name.')
    if not args.expected_account and args.rotate_aws_only:
        args.expected_account = api(args.repo, 'actions/variables/AWS_ACCOUNT_ID').get('value', '')
    if not re.fullmatch(r'[0-9]{12}', args.expected_account or ''):
        raise SafeError('Supply the expected AWS account once during automation setup.')
    credentials = {name: os.environ.get(name, '') for name in AWS_KEYS}
    if any(not value or any(c in value for c in '\r\n\0') for value in credentials.values()):
        raise SafeError('Export all three temporary AWS credentials in this terminal; do not paste them into chat.')
    identity = json.loads(command(['aws', 'sts', 'get-caller-identity', '--region', 'us-east-1',
                                   '--output', 'json', '--no-cli-pager']))
    if identity.get('Account') != args.expected_account:
        raise SafeError('The active AWS account does not match the expected lab account.')
    # STS identity still works for some cancelled Lab sessions; require a real service permission.
    command(['aws', 'eks', 'describe-cluster', '--name', 'boutique-eq4-eks',
             '--region', 'us-east-1', '--no-cli-pager'])
    repository = api(args.repo)
    if not repository.get('permissions', {}).get('admin'):
        raise SafeError('GitHub environment configuration requires repository admin access.')
    if args.rotate_aws_only:
        rotate_credentials(args.repo, credentials)
        print('PASS: all three repository AWS secrets updated; stale environment overrides removed.')
        print('Merge development into main to build and deploy automatically; no Vault CI token is required.')
        return
    if (not re.fullmatch(r'i-[0-9a-f]{8,17}', args.instance_id or '')
            or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', args.ssm_bucket or '')
            or not args.migration_journal):
        raise SafeError('One-time setup requires the Vault instance, dedicated SSM bucket and verified KMS migration journal.')
    journal = json.loads(args.migration_journal.read_text())
    if (journal.get('phase') != 'verified' or journal.get('instance') != args.instance_id
            or not journal.get('key', '').startswith('arn:aws:kms:us-east-1:' + args.expected_account + ':key/')):
        raise SafeError('Complete and verify the one-time KMS migration before activation.')
    environment = api(args.repo, f'environments/{ENVIRONMENT}')
    if any(rule.get('type') != 'branch_policy' for rule in environment.get('protection_rules', [])):
        raise SafeError('Existing environment protections require manual preservation before using this helper.')
    if os.name != 'posix' or args.operator_file.stat().st_mode & 0o077:
        raise SafeError('Use Linux/WSL and a private operator file with mode 0600.')
    operator_auth = json.loads(args.operator_file.read_text())
    operator = Vault(token=operator_auth['client_token'])
    operator.ready()
    _, lookup = operator.request('GET', 'auth/token/lookup-self')
    policies = lookup['data'].get('policies', [])
    if 'boutique-operator' not in policies or 'root' in policies:
        raise SafeError('The supplied file must contain the named lab operator, not root.')
    seal = operator.request('GET', 'sys/seal-status')[1]
    if seal.get('type') != 'awskms' or seal.get('migration'):
        raise SafeError('Vault must use the completed native AWS KMS seal before activation.')
    configure(operator, args.repo, repository['id'], repository['owner']['id'])
    publish(args.repo, args.instance_id, credentials, args.ssm_bucket, args.expected_account, environment)
    print('PASS: three repository AWS secrets configured; environment overrides removed.')
    print('PASS: main-only Vault automation activated; no stored CI token or public certificate variable is needed.')


if __name__ == '__main__':
    try:
        main()
    except SafeError as exc:
        print('Actions configuration failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError):
        print('Actions configuration failed; verify local credentials, custody files and TLS. No values were logged.', file=sys.stderr)
        sys.exit(1)
