"""Transfer the active lab credentials and a restricted Vault token directly to Actions."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import bootstrap
from common import DATA_PATH, SafeError, Vault, validate_values

AWS_KEYS = ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN')
ENVIRONMENT = 'vault-lab'


def command(args, value=None):
    result = subprocess.run(args, input=value, text=True, capture_output=True, check=False)
    if result.returncode:
        # CLI errors can contain request details. Never forward stdout/stderr or stdin.
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


def publish(repo, instance, credentials, certificate, ci_auth, environment):
    prepare_environment(repo, environment)
    for name, value in credentials.items():
        set_value(repo, 'secret', name, value)
        # Environment secrets override repository secrets; keep both scopes in sync.
        set_value(repo, 'secret', name, value, environment=True)
    set_value(repo, 'secret', 'VAULT_CI_TOKEN', ci_auth['client_token'], environment=True)
    set_value(repo, 'variable', 'VAULT_INSTANCE_ID', instance, environment=True)
    set_value(repo, 'variable', 'VAULT_CA_CERT_B64', base64.b64encode(certificate).decode(), environment=True)
    # Select the Vault job only after every credential and trust setting was transferred.
    set_value(repo, 'variable', 'ENABLE_VAULT_LAB', 'true')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--instance-id', required=True)
    parser.add_argument('--expected-account', required=True)
    parser.add_argument('--operator-file', type=Path, default=Path.home() / 'vault-custody/operator.json')
    parser.add_argument('--ci-output', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', args.repo):
        raise SafeError('Supply a repository as owner/name.')
    if not re.fullmatch(r'i-[0-9a-f]{8,17}', args.instance_id) or not re.fullmatch(r'[0-9]{12}', args.expected_account):
        raise SafeError('Supply the expected instance and AWS account identifiers.')
    credentials = {name: os.environ.get(name, '') for name in AWS_KEYS}
    if any(not value or any(c in value for c in '\r\n\0') for value in credentials.values()):
        raise SafeError('Export all three temporary AWS credentials in this terminal; do not paste them into chat.')
    identity = json.loads(command(['aws', 'sts', 'get-caller-identity', '--region', 'us-east-1',
                                   '--output', 'json', '--no-cli-pager']))
    if identity.get('Account') != args.expected_account:
        raise SafeError('The active AWS account does not match the expected lab account.')
    if not api(args.repo).get('permissions', {}).get('admin'):
        raise SafeError('GitHub environment configuration requires repository admin access.')
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
    validate_values(operator.request('GET', DATA_PATH)[1]['data']['data'])
    certificate = Path(os.environ['VAULT_CACERT']).read_bytes()
    previous_token, previous_argv = os.environ.get('VAULT_TOKEN'), sys.argv
    try:
        os.environ['VAULT_TOKEN'] = operator_auth['client_token']
        sys.argv = ['bootstrap.py', 'issue-ci', '--output', str(args.ci_output)]
        bootstrap.main()
    finally:
        sys.argv = previous_argv
        if previous_token is None:
            os.environ.pop('VAULT_TOKEN', None)
        else:
            os.environ['VAULT_TOKEN'] = previous_token
    ci_auth = json.loads(args.ci_output.read_text())
    if (ci_auth.get('policies') != ['boutique-ci'] or not ci_auth.get('orphan') or
            ci_auth.get('renewable') or not 0 < ci_auth.get('lease_duration', 0) <= 14400):
        raise SafeError('The issued CI token does not meet the expected scope and lifetime.')
    ci = Vault(token=ci_auth['client_token'])
    validate_values(ci.request('GET', DATA_PATH)[1]['data']['data'])
    code, _ = ci.request('GET', 'sys/audit', allowed=(200, 403))
    if code != 403:
        raise SafeError('The CI token unexpectedly has administration access.')
    publish(args.repo, args.instance_id, credentials, certificate, ci_auth, environment)
    print('PASS: AWS credentials synchronized in repository and vault-lab.')
    print('PASS: restricted Vault CI token verified and uploaded (maximum lifetime: four hours).')
    print('PASS: vault-lab restricted to main; ENABLE_VAULT_LAB configured as a repository variable.')
    print(f'CI custody file: {args.ci_output}')


if __name__ == '__main__':
    try:
        main()
    except SafeError as exc:
        print('Actions configuration failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError):
        print('Actions configuration failed; verify local credentials, custody files and TLS. No values were logged.', file=sys.stderr)
        sys.exit(1)
